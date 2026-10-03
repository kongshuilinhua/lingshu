"""工作流 DAG 图编排内核:跳转解析与条件评估的纯逻辑。

与 WorkflowRunner 的职责切分:
    本模块只做"下一跳去哪""条件成不成立""图合不合法"三件事,
    不触碰数据库、不感知 agent/LLM,脱离运行时可单测。
    WorkflowRunner 负责节点具体执行,每个节点跑完调用 ``resolve_next`` 决定下一跳。
"""

from __future__ import annotations

from typing import Any

# 🛡️ 图执行步数硬墙:Condition 死循环(分支指回已执行节点)或自指 next 会被这堵墙拦住,
# 防止游标驱动执行器在异常图上无限推进、耗尽线程与 token 预算。
MAX_GRAPH_STEPS = 64

# 条件节点支持的谓词。用结构化 spec 而非字符串表达式,杜绝代码注入。
_PREDICATES = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
}


def _get_by_path(obj: Any, path: str) -> Any:
    """点号路径取值:``rag_status.matched_chunks`` → context["rag_status"]["matched_chunks"]。

    路径中任意一段缺失即视为条件不成立(返回哨兵),而不是抛 KeyError——
    条件评估应在缺失字段上温和失败,让分支落到 default。
    """
    cur: Any = obj
    for seg in path.split("."):
        if isinstance(cur, dict):
            if seg not in cur:
                return _MISSING
            cur = cur[seg]
        else:
            return _MISSING
    return cur


class _MissingSentinel:
    """context 里不存在的字段取值哨兵,与合法的 None 区分。"""

    _instance: _MissingSentinel | None = None

    def __new__(cls) -> _MissingSentinel:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return other is self

    def __repr__(self) -> str:
        return "<missing>"


_MISSING = _MissingSentinel()


def eval_condition(spec: dict | None, context: dict, output: dict | None = None) -> bool:
    """评估单个结构化条件 spec,成立返回 True。

    spec 形如 ``{"field": "rag_status.matched_chunks", "gte": 1}``。
    output 优先于 context:节点刚产出的字段(如 ``tool_stats``)先在 output 里找,
    找不到再回退 context(条件可能依赖更早节点写入 context 的值)。
    """
    if not spec or not isinstance(spec, dict):
        return False
    field = spec.get("field")
    if not isinstance(field, str):
        return False
    source = {**context, **(output or {})}
    value = _get_by_path(source, field)
    # exists 谓词只关心字段在不在,不比较值
    if "exists" in spec:
        want = bool(spec["exists"])
        return (value is not _MISSING) == want
    if "in" in spec:
        container = spec["in"]
        return isinstance(container, (list, tuple, set, dict)) and value in container
    # 比较类谓词:字段缺失时一律判不成立,落到 default 分支
    if value is _MISSING:
        return False
    for op, expected in spec.items():
        if op in ("field", "exists", "in"):
            continue
        pred = _PREDICATES.get(op)
        if pred is None:
            continue
        try:
            if not pred(value, expected):
                return False
        except TypeError:
            # 类型不可比(如 None 与 int)→ 该条件不成立
            return False
    return True


def resolve_next(node: dict, output: dict, context: dict) -> str | None:
    """解析节点执行后的下一跳 id。返回 None 表示走列表顺序下一个(线性兼容)。

    - Condition 节点:按 branches 顺序评估,首个命中的分支 next 胜出,否则 default。
    - 普通节点:显式 ``next`` 字段(字符串)优先,缺省则 None。
    """
    node_type = node.get("type")
    config = node.get("config") or {}
    if node_type == "Condition":
        for branch in config.get("branches") or []:
            if not isinstance(branch, dict):
                continue
            if eval_condition(branch.get("when"), context, output):
                nxt = branch.get("next")
                if isinstance(nxt, str):
                    return nxt
        default = config.get("default")
        return default if isinstance(default, str) else None
    nxt = node.get("next")
    return nxt if isinstance(nxt, str) else None


def advance(index: int, next_id: str | None, workflow: list[dict]) -> int:
    """根据下一跳 id 推进游标。悬空跳转(目标不存在)降级为顺序下一个,不报错。

    降级而非报错是兼容策略:旧快照里手工写错 next 不应让聊天直接挂掉。
    """
    if next_id is None:
        return index + 1
    for i, node in enumerate(workflow):
        if node.get("id") == next_id:
            return i
    return index + 1


def validate_graph(nodes: list[dict]) -> list[str]:
    """校验图合法性,返回错误信息列表(空列表=合法)。

    调用方据此拼 HTTP 400 detail。校验内容:
    - 必含 Start 与 Answer。
    - 节点类型在白名单内。
    - 节点 id 唯一。
    - 所有 next / Condition 分支 / default 指向的 id 必须存在(无悬空边)。
    """
    errors: list[str] = []
    allowed = {"Start", "LLM", "Knowledge", "Tool", "Answer", "Condition", "HumanApproval"}
    ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    seen_types = {n.get("type") for n in nodes if isinstance(n, dict)}

    if not seen_types.issubset(allowed):
        errors.append("Unsupported workflow node type")
    if "Start" not in seen_types:
        errors.append("Workflow requires a Start node")
    if "Answer" not in seen_types:
        errors.append("Workflow requires an Answer node")

    # id 唯一性
    dupes = [i for i in ids if ids.count(i) > 1]
    if dupes:
        errors.append("Duplicate node id: " + ", ".join(sorted(set(dupes))))

    id_set = set(ids)
    # 悬空边:任意 next / Condition 分支 / default 必须命中已存在的 id
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nxt = node.get("next")
        if isinstance(nxt, str) and nxt not in id_set:
            errors.append(f"Node '{node.get('id')}' has dangling next -> '{nxt}'")
        if node.get("type") == "Condition":
            config = node.get("config") or {}
            for branch in config.get("branches") or []:
                if not isinstance(branch, dict):
                    continue
                bn = branch.get("next")
                if isinstance(bn, str) and bn not in id_set:
                    errors.append(f"Condition '{node.get('id')}' has dangling branch -> '{bn}'")
            default = config.get("default")
            if isinstance(default, str) and default not in id_set:
                errors.append(f"Condition '{node.get('id')}' has dangling default -> '{default}'")
    return errors
