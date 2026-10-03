"""工作流 DAG 图编排测试。

两层覆盖:
1. graph.py 纯逻辑:条件评估 / 下一跳解析 / 游标推进 / 图校验,脱离 DB 与 agent。
2. WorkflowRunner.run_events 真实游标循环:桩掉 DB 持久化与节点业务,
   保留 run_events 里真实的 while 游标 + resolve_next + advance 代码路径,
   验证 Condition 分支、显式 next、悬空降级、防环真实生效。
"""

from types import SimpleNamespace

from core.runtime.graph import (
    MAX_GRAPH_STEPS,
    advance,
    eval_condition,
    resolve_next,
    validate_graph,
)
from core.runtime.workflow import WorkflowRunner

# ── 纯逻辑:eval_condition ───────────────────────────────────────


def test_eval_condition_eq_ne():
    assert eval_condition({"field": "rag_enabled", "eq": True}, {"rag_enabled": True}) is True
    assert eval_condition({"field": "rag_enabled", "eq": True}, {"rag_enabled": False}) is False
    assert eval_condition({"field": "rag_enabled", "ne": False}, {"rag_enabled": True}) is True


def test_eval_condition_numeric_predicates():
    ctx = {"rag_status": {"matched_chunks": 3}}
    assert eval_condition({"field": "rag_status.matched_chunks", "gte": 1}, ctx) is True
    assert eval_condition({"field": "rag_status.matched_chunks", "lt": 1}, ctx) is False
    assert eval_condition({"field": "rag_status.matched_chunks", "gt": 3}, ctx) is False
    assert eval_condition({"field": "rag_status.matched_chunks", "lte": 3}, ctx) is True


def test_eval_condition_missing_field_is_false():
    # 字段缺失时不成立,落到 default 分支(温和失败而非 KeyError)
    assert eval_condition({"field": "rag_status.matched_chunks", "gte": 1}, {"rag_enabled": True}) is False


def test_eval_condition_exists_and_in():
    ctx = {"rag_status": {"matched_chunks": 2}}
    assert eval_condition({"field": "rag_status.matched_chunks", "exists": True}, ctx) is True
    assert eval_condition({"field": "rag_status.missing", "exists": False}, ctx) is True
    assert eval_condition({"field": "rag_status.matched_chunks", "in": [1, 2, 3]}, ctx) is True
    assert eval_condition({"field": "rag_status.matched_chunks", "in": [5, 6]}, ctx) is False


def test_eval_condition_output_overrides_context():
    # 节点刚产出的字段(output)优先于 context 里旧值
    assert (
        eval_condition({"field": "tool_stats.total_calls", "gte": 1}, {"tool_stats": {"total_calls": 0}}, {"tool_stats": {"total_calls": 2}})
        is True
    )


def test_eval_condition_type_mismatch_is_false():
    # None 与 int 不可比时判不成立,而非抛 TypeError
    assert eval_condition({"field": "count", "gt": 0}, {"count": None}) is False


# ── 纯逻辑:resolve_next ────────────────────────────────────────


def test_resolve_next_condition_picks_first_matching_branch():
    node = {
        "type": "Condition",
        "config": {
            "branches": [
                {"when": {"field": "rag_enabled", "eq": False}, "next": "answer"},
                {"when": {"field": "rag_enabled", "eq": True}, "next": "knowledge"},
            ],
            "default": "knowledge",
        },
    }
    assert resolve_next(node, {"rag_enabled": False}, {}) == "answer"
    assert resolve_next(node, {"rag_enabled": True}, {}) == "knowledge"


def test_resolve_next_condition_falls_back_to_default():
    node = {"type": "Condition", "config": {"branches": [{"when": {"field": "x", "eq": 1}, "next": "a"}], "default": "b"}}
    assert resolve_next(node, {"x": 99}, {}) == "b"


def test_resolve_next_plain_node_uses_explicit_next():
    assert resolve_next({"type": "Start", "next": "llm"}, {}, {}) == "llm"
    assert resolve_next({"type": "Knowledge"}, {}, {}) is None  # 无 next → 顺序


# ── 纯逻辑:advance ─────────────────────────────────────────────


def test_advance_none_is_sequential():
    wf = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    assert advance(0, None, wf) == 1
    assert advance(1, None, wf) == 2


def test_advance_jumps_to_target():
    wf = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    assert advance(0, "c", wf) == 2


def test_advance_dangling_next_degrades_to_sequential():
    # 悬空跳转不报错,降级为顺序下一个
    wf = [{"id": "a"}, {"id": "b"}]
    assert advance(0, "nonexistent", wf) == 1


# ── 纯逻辑:validate_graph ───────────────────────────────────────


def _base_nodes():
    return [{"id": "start", "type": "Start"}, {"id": "answer", "type": "Answer"}]


def test_validate_graph_accepts_linear():
    assert validate_graph(_base_nodes()) == []


def test_validate_graph_accepts_condition_with_valid_edges():
    nodes = [
        {"id": "start", "type": "Start", "next": "cond"},
        {"id": "cond", "type": "Condition", "config": {"branches": [{"when": {"field": "x", "eq": 1}, "next": "answer"}], "default": "answer"}},
        {"id": "answer", "type": "Answer"},
    ]
    assert validate_graph(nodes) == []


def test_validate_graph_rejects_missing_start_and_answer():
    assert validate_graph([{"id": "a", "type": "LLM"}])
    assert validate_graph([{"id": "a", "type": "Start"}])  # 缺 Answer


def test_validate_graph_rejects_unknown_type():
    errors = validate_graph([{"id": "start", "type": "Start"}, {"id": "x", "type": "WTF"}, {"id": "answer", "type": "Answer"}])
    assert any("Unsupported" in e for e in errors)


def test_validate_graph_rejects_dangling_next():
    nodes = [{"id": "start", "type": "Start", "next": "ghost"}, {"id": "answer", "type": "Answer"}]
    errors = validate_graph(nodes)
    assert any("dangling next" in e for e in errors)


def test_validate_graph_rejects_dangling_condition_branch():
    nodes = [
        {"id": "start", "type": "Start"},
        {"id": "cond", "type": "Condition", "config": {"branches": [{"when": {"field": "x", "eq": 1}, "next": "ghost"}], "default": "answer"}},
        {"id": "answer", "type": "Answer"},
    ]
    errors = validate_graph(nodes)
    assert any("dangling branch" in e for e in errors)


def test_validate_graph_rejects_duplicate_ids():
    nodes = [{"id": "dup", "type": "Start"}, {"id": "dup", "type": "Answer"}]
    errors = validate_graph(nodes)
    assert any("Duplicate" in e for e in errors)


# ── run_events 真实游标循环(端到端,桩 DB/业务,保留真实跳转代码路径) ──


def _run_events_with_stubs(monkeypatch, workflow, rag_enabled=False):
    """桩掉 _start_run / _execute_node / _persist_step 与 db.commit,
    保留 run_events 里真实的 while 游标 + resolve_next + advance 代码路径。"""
    executed: list[str] = []

    def fake_execute(self, agent, node, context):
        executed.append(node["id"])
        if node["type"] == "Answer":
            return {"answer": "答案"}
        if node["type"] == "Knowledge":
            return {"sources": [], "rag_status": {"matched_chunks": 0}}
        return {}

    monkeypatch.setattr(WorkflowRunner, "_execute_node", fake_execute)

    def fake_start_run(self, **kwargs):
        # workflow 通过闭包注入(真实 _start_run 内部经 _runtime_agent 解析,这里桩掉)
        runtime = SimpleNamespace(workflow=workflow, settings={"memory": {"enabled": False}})
        run = SimpleNamespace(id=1, status="running")
        context = {"rag_enabled": rag_enabled, "draft": "", "sources": [], "web_sources": [], "tool_outputs": []}
        return runtime, run, context

    monkeypatch.setattr(WorkflowRunner, "_start_run", fake_start_run)

    def fake_persist(self, run, node, user_message, output, *, status="succeeded"):
        return SimpleNamespace(id=id(node), node_id=node["id"], node_type=node["type"], status=status)

    monkeypatch.setattr(WorkflowRunner, "_persist_step", fake_persist)

    runner = WorkflowRunner(db=SimpleNamespace(commit=lambda: None, flush=lambda: None, refresh=lambda obj: None))
    events = list(
        runner.run_events(
            agent=SimpleNamespace(),
            chat_session=SimpleNamespace(id=1, user_id=1),
            user_message="hi",
            mode="draft",
            rag_enabled=rag_enabled,
        )
    )
    return executed, events


def test_run_events_linear_workflow_executes_all_in_order(monkeypatch):
    """回归:无 next / 无 Condition 的线性 workflow,游标执行器跑完全部节点,顺序与列表一致。"""
    workflow = [
        {"id": "start", "type": "Start"},
        {"id": "knowledge", "type": "Knowledge"},
        {"id": "answer", "type": "Answer"},
    ]
    executed, events = _run_events_with_stubs(monkeypatch, workflow, rag_enabled=False)
    assert executed == ["start", "knowledge", "answer"]
    assert any(e["event"] == "complete" for e in events)


def test_run_events_condition_branch_skips_knowledge(monkeypatch):
    """Condition 命中分支跳到非相邻节点,中间节点被跳过(不执行、不出现在 executed)。"""
    workflow = [
        {"id": "start", "type": "Start"},
        {
            "id": "cond",
            "type": "Condition",
            "config": {"branches": [{"when": {"field": "rag_enabled", "eq": False}, "next": "answer"}], "default": "knowledge"},
        },
        {"id": "knowledge", "type": "Knowledge"},
        {"id": "answer", "type": "Answer"},
    ]
    executed, _ = _run_events_with_stubs(monkeypatch, workflow, rag_enabled=False)
    # rag_enabled=False 命中分支 → 跳过 knowledge 直达 answer
    assert executed == ["start", "cond", "answer"]


def test_run_events_condition_default_takes_knowledge(monkeypatch):
    """Condition 分支不命中时走 default,顺序执行 knowledge。"""
    workflow = [
        {"id": "start", "type": "Start"},
        {
            "id": "cond",
            "type": "Condition",
            "config": {"branches": [{"when": {"field": "rag_enabled", "eq": False}, "next": "answer"}], "default": "knowledge"},
        },
        {"id": "knowledge", "type": "Knowledge"},
        {"id": "answer", "type": "Answer"},
    ]
    executed, _ = _run_events_with_stubs(monkeypatch, workflow, rag_enabled=True)
    assert executed == ["start", "cond", "knowledge", "answer"]


def test_run_events_explicit_next_skips_intermediate(monkeypatch):
    """普通节点显式 next 跳到非相邻节点。"""
    workflow = [
        {"id": "start", "type": "Start", "next": "answer"},
        {"id": "knowledge", "type": "Knowledge"},
        {"id": "answer", "type": "Answer"},
    ]
    executed, _ = _run_events_with_stubs(monkeypatch, workflow)
    assert executed == ["start", "answer"]


def test_run_events_dangling_next_degrades_to_sequential(monkeypatch):
    """悬空 next(目标不存在)降级为顺序下一个,不让聊天挂掉。"""
    workflow = [
        {"id": "start", "type": "Start", "next": "ghost"},
        {"id": "knowledge", "type": "Knowledge"},
        {"id": "answer", "type": "Answer"},
    ]
    executed, _ = _run_events_with_stubs(monkeypatch, workflow)
    assert executed == ["start", "knowledge", "answer"]


def test_run_events_prevents_infinite_loop(monkeypatch):
    """Condition default 指回已执行节点 → 被已执行集拦截,不死循环。"""
    workflow = [
        {"id": "start", "type": "Start"},
        {"id": "cond", "type": "Condition", "config": {"branches": [], "default": "start"}},
        {"id": "answer", "type": "Answer"},
    ]
    executed, _ = _run_events_with_stubs(monkeypatch, workflow)
    # start 执行后,cond default 指回 start(已在 executed 集合)→ break,answer 不再执行
    assert executed == ["start", "cond"]


def test_max_graph_steps_is_bounded():
    # 防失控硬墙存在且有限,保证异常图上执行有上界
    assert isinstance(MAX_GRAPH_STEPS, int) and MAX_GRAPH_STEPS > 0 and MAX_GRAPH_STEPS < 1000
