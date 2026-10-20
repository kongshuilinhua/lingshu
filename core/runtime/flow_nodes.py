"""工作流扩展节点：HTTP 请求、代码执行、变量赋值、参数提取、问题分类。

🎯 独立成模块的原因：
    `workflow.py` 已经承载了主执行循环、工具循环、记忆与提示词装配，继续往里塞
    五类节点会让它更难维护。这里只放「输入 context → 输出 dict」的纯节点逻辑，
    由 `WorkflowRunner._execute_node` 分发进来，不依赖 WorkflowRunner 的其它状态。

🧠 共同约定：
    - 返回值会并入 context（与其它节点一致）；
    - 需要自己决定下一跳的节点（Classifier）返回 `_next`，
      `graph.resolve_next` 会优先采用它；
    - 所有对外请求都走项目的 SSRF 防护，不新开裸 httpx 通道。
"""

from __future__ import annotations

import base64
import json
import re
from types import SimpleNamespace

from core.runtime.skill_sandbox import run_script

# 模板占位符：{{input}}、{{rag_status.matched_chunks}}、{{variables.city}}
_TEMPLATE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

MAX_HTTP_RESPONSE_CHARS = 8000
MAX_CODE_OUTPUT_CHARS = 8000


def _lookup(context: dict, path: str):
    """按点号路径从上下文取值；取不到返回空串，避免模板渲染整体失败。"""
    current = context
    for segment in str(path).strip().split("."):
        if isinstance(current, dict):
            if segment not in current:
                return ""
            current = current[segment]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return ""
        else:
            return ""
    return current


def render_template(value, context: dict):
    """
    渲染 `{{path}}` 占位符。

    整串只有一个占位符时保留原始类型（便于把数组/数字直接传下去）；
    混合文本时统一转字符串。
    """
    if isinstance(value, dict):
        return {key: render_template(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_template(item, context) for item in value]
    if not isinstance(value, str):
        return value
    whole = _TEMPLATE.fullmatch(value.strip())
    if whole:
        return _lookup(context, whole.group(1))
    return _TEMPLATE.sub(lambda match: str(_lookup(context, match.group(1))), value)


# ── HTTP ──────────────────────────────────────────────────────────


def run_http_node(node: dict, context: dict) -> dict:
    """
    通用 HTTP 请求节点。

    🛡️ 与「用户自定义 HTTP 工具」同一套防护：
        只允许公网 HTTPS、解析后钉住 IP 再连接（防 DNS rebinding）、
        禁止自动跟随重定向、限制响应大小。节点额外支持 `{{...}}` 模板，
        方便把上游检索结果或变量拼进 URL / 请求体。
    """
    from core.security.outbound_http import open_public_https
    import urllib.error
    import urllib.request

    config = node.get("config") or {}
    url = str(render_template(config.get("url") or "", context)).strip()
    if not url:
        raise ValueError("HTTP 节点缺少 url")
    method = str(config.get("method") or "GET").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError(f"HTTP 节点不支持的方法：{method}")
    timeout = max(1, min(int(config.get("timeout_seconds") or 10), 30))

    headers = render_template(config.get("headers") or {}, context)
    headers = {str(key): str(value) for key, value in headers.items() if value != ""}
    body = None
    if method in {"POST", "PUT", "PATCH"}:
        payload = render_template(config.get("body"), context)
        if payload is None or payload == "":
            payload = {}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with open_public_https(request, timeout=timeout) as response:
            raw = response.read(MAX_HTTP_RESPONSE_CHARS * 4 + 1)
            status = getattr(response, "status", 0)
    except urllib.error.HTTPError as exc:
        # 与工具执行一致：只回状态码，不回上游响应体（可能含内部信息或凭证回显）
        raise ValueError(f"HTTP 节点请求失败，状态码 {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError("HTTP 节点请求失败") from exc

    text = raw.decode("utf-8", errors="replace")
    parsed = None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        pass

    # response_path 裁剪：只把业务数据交给下游，避免元数据占满上下文
    response_path = str(render_template(config.get("response_path") or "$", context)).strip()
    if parsed is not None and response_path and response_path != "$":
        picked = _pick_path(parsed, response_path)
        if picked is not _MISSING:
            parsed = picked
            text = json.dumps(picked, ensure_ascii=False)

    return {
        "http": {
            "status": status,
            "url": url,
            "content": text[:MAX_HTTP_RESPONSE_CHARS],
            "json": parsed,
        },
        # 让下游模板可以直接引用 {{http.json.xxx}} / {{http.content}}
        "http_status": status,
    }


class _Missing:
    pass


_MISSING = _Missing()


def _pick_path(payload, path: str):
    """与 HTTP 工具一致的简易路径取值，支持 `$.a.b`、`a.b[0]`、`$.items[*].id`。"""
    stripped = str(path or "").strip()
    if stripped.startswith("$"):
        stripped = stripped[1:]
    if not stripped:
        return payload
    segments: list = []
    token = ""
    index = 0
    while index < len(stripped):
        char = stripped[index]
        if char == "[":
            if token:
                segments.append(token)
                token = ""
            end = stripped.find("]", index)
            if end == -1:
                return _MISSING
            inner = stripped[index + 1:end].strip().strip("'\"")
            segments.append(None if inner == "*" else int(inner) if inner.lstrip("-").isdigit() else inner)
            index = end + 1
            continue
        if char == ".":
            if token:
                segments.append(token)
                token = ""
            index += 1
            continue
        token += char
        index += 1
    if token:
        segments.append(token)

    current = payload
    for position, segment in enumerate(segments):
        if segment is None:
            if not isinstance(current, list):
                return _MISSING
            rest = segments[position + 1:]
            remainder = "$" + "".join(
                f"[{item}]" if isinstance(item, int) else f".{item}" for item in rest
            ) if rest else "$"
            return [
                value for item in current
                if (value := _pick_path(item, remainder)) is not _MISSING
            ]
        if isinstance(current, list):
            if not isinstance(segment, int) or segment >= len(current) or segment < -len(current):
                return _MISSING
            current = current[segment]
            continue
        if isinstance(current, dict):
            if segment not in current:
                return _MISSING
            current = current[segment]
            continue
        return _MISSING
    return current


# ── 代码 ──────────────────────────────────────────────────────────


def run_code_node(node: dict, context: dict) -> dict:
    """
    代码执行节点：复用 Skill 脚本沙箱（Docker、禁网、只读、资源限制）。

    🛡️ 为什么不在进程内 exec：
        工作流由用户配置，代码节点等价于「用户可提交任意代码」。必须走与 Skill 脚本
        相同的容器隔离；未配置 SKILL_SANDBOX_IMAGE 时明确报错而不是降级执行。

    📦 输入输出协议：
        代码从 stdin 读 JSON（`input` 与上游上下文里可序列化的字段），
        向 stdout 写 JSON 作为本节点输出；输出过长会被截断。
    """
    config = node.get("config") or {}
    language = str(config.get("language") or "python").lower()
    code = str(config.get("code") or "").strip()
    if not code:
        raise ValueError("代码节点缺少代码")
    suffix = {"python": ".py", "javascript": ".js", "js": ".js"}.get(language)
    if not suffix:
        raise ValueError(f"代码节点不支持的语言：{language}")

    payload = {
        "input": context.get("input"),
        "variables": context.get("variables") or {},
        # 只传可 JSON 序列化的上游结果，避免把 ORM 对象塞进沙箱
        "sources": [
            {key: item.get(key) for key in ("title", "snippet", "score", "chunk_id")}
            for item in (context.get("sources") or [])
        ],
        "http": context.get("http"),
        "draft": context.get("draft") or "",
    }
    # 把 payload 作为运行时输入文件放进沙箱（scripts/ 下），代码自行读取
    files = [
        SimpleNamespace(
            path="scripts/main" + suffix,
            content_base64=base64.b64encode(code.encode("utf-8")).decode("ascii"),
        ),
        SimpleNamespace(
            path="scripts/input.json",
            content_base64=base64.b64encode(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")).decode("ascii"),
        ),
    ]
    result = run_script(files, "scripts/main" + suffix, [])
    output = str(result.get("output") or "")[:MAX_CODE_OUTPUT_CHARS]
    if result.get("exit_code"):
        raise ValueError(f"代码节点执行失败（退出码 {result.get('exit_code')}）：{output[:400]}")
    parsed = _parse_json_object(output)
    return {
        "code": {
            "output": output,
            "result": parsed,
            "truncated": bool(result.get("truncated")),
        },
    }


def _parse_json_object(text: str) -> dict | None:
    """从输出里取第一个 JSON 对象；取不到返回 None（纯文本输出也算合法结果）。"""
    if not text or not text.strip():
        return None
    match = _JSON_OBJECT.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


# ── 变量赋值 ──────────────────────────────────────────────────────


def run_variable_assign(node: dict, context: dict) -> dict:
    """
    变量赋值节点：把模板渲染后的值写进 `context["variables"]`。

    典型用法：把检索命中的第一个标题、HTTP 节点返回的字段、或模型输出的分类结果
    存成变量，供后面的提示词与条件分支引用（`{{variables.xxx}}`）。
    """
    config = node.get("config") or {}
    assignments = config.get("assignments") or []
    if not isinstance(assignments, list):
        raise ValueError("变量赋值节点的 assignments 必须是数组")
    variables = dict(context.get("variables") or {})
    applied = {}
    for item in assignments:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        value = render_template(item.get("value"), context)
        variables[name] = value
        applied[name] = value
    return {"variables": variables, "assigned": applied}


# ── 参数提取 ──────────────────────────────────────────────────────


def run_parameter_extract(node: dict, context: dict, *, provider, agent) -> dict:
    """
    参数提取节点：用一次模型调用把自然语言抽成结构化字段。

    🎯 用结构化 JSON 输出而不是 tool calling：
        提取结果要落进上下文给后续节点当变量用，不是要调工具；
        用「只输出 JSON」的提示词 + 容错解析，兼容不支持 tool_choice 的网关。
    """
    config = node.get("config") or {}
    schema = config.get("schema") or {}
    if not isinstance(schema, dict) or not schema:
        raise ValueError("参数提取节点缺少字段定义")
    fields = "\n".join(
        f"- {name}: {spec.get('description', '') if isinstance(spec, dict) else ''}"
        for name, spec in schema.items()
    )
    instruction = str(render_template(config.get("instruction") or "", context)).strip()
    source_text = str(render_template(config.get("source") or "{{input}}", context)).strip()

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个信息抽取器。只输出一个 JSON 对象，不要解释、不要代码围栏。\n"
                "字段取不到时用 null，不要编造。\n字段定义：\n" + fields
            ),
        },
        {"role": "user", "content": (instruction + "\n\n" if instruction else "") + source_text},
    ]
    response = provider.chat(
        messages,
        model=agent.model,
        temperature=0,
        runtime_config=getattr(agent, "runtime_config", None),
    )
    parsed = _parse_json_object(str(response.content or "")) or {}
    # 只保留声明过的字段，避免模型多输出污染上下文
    extracted = {name: parsed.get(name) for name in schema}
    return {"extracted": extracted, "extract_raw": str(response.content or "")[:2000]}


# ── 问题分类 ──────────────────────────────────────────────────────


def run_classifier(node: dict, context: dict, *, provider, agent) -> dict:
    """
    问题分类节点：用一次模型调用在多条分支里选一条。

    🎯 与 Condition 的区别：
        Condition 用确定性谓词判断上下文里的字段（快、可预测）；
        Classifier 用模型判断自然语言属于哪一类（适合路由到不同的处理链）。
        两者在图上都是「多出边」，分类器命中后返回 `_next` 决定跳转。
    """
    config = node.get("config") or {}
    classes = config.get("classes") or []
    if not isinstance(classes, list) or not classes:
        raise ValueError("问题分类节点缺少分类项")
    options = "\n".join(
        f"- {item.get('label')}: {item.get('description', '')}"
        for item in classes if isinstance(item, dict)
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个问题分类器。只输出一个 JSON 对象：{\"label\": \"<分类名>\"}。\n"
                "必须从下列分类里选一个，不要解释。\n分类：\n" + options
            ),
        },
        {"role": "user", "content": str(render_template(config.get("source") or "{{input}}", context))},
    ]
    response = provider.chat(
        messages,
        model=agent.model,
        temperature=0,
        runtime_config=getattr(agent, "runtime_config", None),
    )
    parsed = _parse_json_object(str(response.content or "")) or {}
    label = str(parsed.get("label") or "").strip()

    matched = next(
        (item for item in classes if isinstance(item, dict) and str(item.get("label")) == label),
        None,
    )
    if matched is None:
        # 模型给了不在选项里的标签：落到 default，不猜
        return {
            "classification": {"label": label or None, "matched": False},
            "_next": config.get("default") or None,
        }
    return {
        "classification": {"label": label, "matched": True},
        "_next": matched.get("next") or config.get("default") or None,
    }


# ── 循环 ──────────────────────────────────────────────────────────

MAX_LOOP_ITERATIONS = 20
MAX_LOOP_STEPS = 120
MAX_LOOP_SECONDS = 300


def run_loop_node(node: dict, context: dict, *, execute) -> dict:
    """
    循环节点：把一组节点（body）重复执行，直到次数用完或满足停止条件。

    🎯 为什么循环体要内嵌成子图、而不是在主图里画回边：
        主执行器的语义是「每个节点只经过一次」（防 Condition 死循环），
        画回边会直接撞上这堵墙。把循环体作为节点自己的子流程，
        主图仍然是一次性的 DAG，循环的重复只发生在节点内部——
        既不用改动主执行器，也让循环的预算（次数 / 步数 / 墙钟）可以单独收紧。

    🧠 三重预算（循环里再套工具循环是最容易失控的组合）：
        - max_iterations：迭代次数，夹紧到 1..20；
        - MAX_LOOP_STEPS：循环体内累计执行的节点数，到 120 直接报错；
        - MAX_LOOP_SECONDS：墙钟 300 秒，超时终止而不是拖死整轮对话。
        工具节点自身的 12 轮 / 20 次调用 / 120 秒上限仍然生效，两者叠加。

    📦 迭代变量：
        - 配了 `items`（数组）就逐项遍历，当前项写入 `variables[loop_var]`；
        - 没配则按 max_iterations 固定次数执行。
        两者都会把 `loop.index` / `loop.iteration` 放进上下文，供提示词与条件引用。

    ⚠️ 流式边界：循环体内的 LLM 节点走非流式执行，正文在循环结束后由下游 LLM 节点统一流式输出。
    """
    import time

    config = node.get("config") or {}
    body = [item for item in (config.get("body") or []) if isinstance(item, dict)]
    if not body:
        raise ValueError("循环节点缺少循环体")
    if any(item.get("type") in {"Start", "Answer"} for item in body):
        raise ValueError("循环体内不能包含 Start 或 Answer 节点")

    items = _render_items(config.get("items"), context)
    if items is not None and not isinstance(items, list):
        raise ValueError("循环节点的 items 必须是数组")
    if isinstance(items, list) and not items:
        # 空数组 = 一次都不跑，而不是退化成「固定跑 3 次」
        context["loop"] = {"finished": True, "iterations": 0, "stopped": False, "max_iterations": 0}
        return {"loop": {"iterations": 0, "stopped": False, "records": []}}
    default_iterations = len(items) if items else 3
    max_iterations = int(config.get("max_iterations") or default_iterations)
    cap = min(MAX_LOOP_ITERATIONS, len(items)) if items else MAX_LOOP_ITERATIONS
    max_iterations = max(1, min(max_iterations, cap))
    loop_var = str(config.get("loop_var") or "item").strip() or "item"
    stop_when = config.get("stop_when") if isinstance(config.get("stop_when"), dict) else None

    records: list[dict] = []
    steps_used = 0
    started = time.monotonic()
    stopped = False

    for index in range(max_iterations):
        if time.monotonic() - started > MAX_LOOP_SECONDS:
            raise ValueError(f"循环执行超过 {MAX_LOOP_SECONDS} 秒，已终止")
        item = items[index] if items else None
        context["loop"] = {
            "index": index,
            "iteration": index + 1,
            "max_iterations": max_iterations,
            "item": item,
            "total_items": len(items) if items else None,
        }
        if items:
            variables = dict(context.get("variables") or {})
            variables[loop_var] = item
            context["variables"] = variables

        executed: set[str] = set()
        cursor = 0
        last_output: dict = {}
        while cursor < len(body):
            body_node = body[cursor]
            if body_node.get("id") in executed:
                break
            executed.add(body_node.get("id"))
            if steps_used >= MAX_LOOP_STEPS:
                raise ValueError(f"循环体累计执行超过 {MAX_LOOP_STEPS} 个节点，已终止")
            steps_used += 1
            if body_node.get("type") == "LLM":
                # 主执行器里 LLM 节点见到已有 draft 会直接复用（用于拒答短路）。
                # 在循环里这会让第二轮开始不再调用模型，必须每轮清掉上一轮的草稿。
                context.pop("draft", None)
            output = execute(body_node, context) or {}
            # 循环体不产生 SSE 事件，避免把中间过程当成最终回答推给前端
            output.pop("events", None)
            output.pop("events_streamed", None)
            context.update(output)
            last_output = output
            nxt = _resolve_in_body(body_node, output, context)
            cursor = _advance_in_body(cursor, nxt, body)

        records.append({"iteration": index + 1, "output_keys": sorted(last_output.keys())})
        if stop_when and _eval_stop(stop_when, context, last_output):
            stopped = True
            break

    context["loop"] = {
        "finished": True,
        "iterations": len(records),
        "stopped": stopped,
        "max_iterations": max_iterations,
    }
    return {"loop": {"iterations": len(records), "stopped": stopped, "records": records[:20]}}


def _render_items(value, context: dict):
    """
    渲染循环的 items。

    🎯 兼容两种写法：
        - 字符串模板：`"{{variables.list}}"` → 直接取出该数组；
        - 字面数组：`["甲", "乙"]`，元素也可以是模板。
    单元素数组的模板写法（`["{{variables.list}}"]`）是接口里最容易写出来的一种，
    这里展开成它指向的数组，而不是变成 `[[...]]` 再被当成「一个元素」——
    否则遍历会只跑一次、且把整个数组当成单项传给循环变量。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return render_template(value, context)
    if isinstance(value, list):
        if len(value) == 1 and isinstance(value[0], str) and _TEMPLATE.fullmatch(value[0].strip()):
            rendered = render_template(value[0], context)
            return rendered if isinstance(rendered, list) else [rendered]
        return [render_template(item, context) for item in value]
    return value


def _resolve_in_body(node: dict, output: dict, context: dict) -> str | None:
    """循环体内的跳转解析：与主图同语义，但只在 body 内生效。"""
    from core.runtime.graph import resolve_next

    return resolve_next(node, output, context)


def _advance_in_body(index: int, next_id: str | None, body: list[dict]) -> int:
    from core.runtime.graph import advance

    return advance(index, next_id, body)


def _eval_stop(spec: dict, context: dict, output: dict) -> bool:
    from core.runtime.graph import eval_condition

    return eval_condition(spec, context, output)
