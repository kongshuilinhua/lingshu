"""扩展节点：HTTP / 代码 / 变量赋值 / 参数提取 / 问题分类。"""

from types import SimpleNamespace

import pytest

from core.runtime import flow_nodes
from core.runtime.graph import resolve_next, validate_graph


def _provider_returning(content: str):
    return SimpleNamespace(chat=lambda *a, **k: SimpleNamespace(content=content))


# ── 模板 ──────────────────────────────────────────────


def test_render_template_keeps_type_for_whole_placeholder():
    context = {"input": "你好", "variables": {"count": 3}, "http": {"json": {"title": "结果"}}}
    assert flow_nodes.render_template("{{input}}", context) == "你好"
    assert flow_nodes.render_template("{{variables.count}}", context) == 3
    assert flow_nodes.render_template("前缀-{{http.json.title}}", context) == "前缀-结果"
    # 取不到时渲染成空串，而不是抛错或留下原始占位符
    assert flow_nodes.render_template("x{{nope.missing}}", context) == "x"


def test_render_template_walks_nested_structures():
    context = {"input": "q"}
    rendered = flow_nodes.render_template({"a": ["{{input}}", {"b": "{{input}}!"}], "n": 1}, context)
    assert rendered == {"a": ["q", {"b": "q!"}], "n": 1}


# ── 变量赋值 ──────────────────────────────────────────


def test_variable_assign_writes_rendered_values():
    node = {"config": {"assignments": [
        {"name": "title", "value": "{{http.json.title}}"},
        {"name": "count", "value": "{{rag_status.matched_chunks}}"},
        {"name": "blank", "value": ""},
        {"name": "", "value": "ignored"},
    ]}}
    context = {"http": {"json": {"title": "手册"}}, "rag_status": {"matched_chunks": 2}, "variables": {"old": 1}}
    output = flow_nodes.run_variable_assign(node, context)
    assert output["variables"] == {"old": 1, "title": "手册", "count": 2, "blank": ""}
    assert output["assigned"]["title"] == "手册"


def test_variable_assign_rejects_non_list():
    with pytest.raises(ValueError):
        flow_nodes.run_variable_assign({"config": {"assignments": "oops"}}, {})


# ── 参数提取 ──────────────────────────────────────────


def test_parameter_extract_keeps_only_declared_fields():
    node = {"config": {"schema": {"order_id": {"description": "订单号"}, "phone": {"description": "手机号"}}}}
    provider = _provider_returning('```json\n{"order_id": "A123", "phone": null, "extra": "丢弃"}\n```')
    agent = SimpleNamespace(model="m", runtime_config=None)
    output = flow_nodes.run_parameter_extract(node, {"input": "订单 A123"}, provider=provider, agent=agent)
    assert output["extracted"] == {"order_id": "A123", "phone": None}


def test_parameter_extract_tolerates_non_json_output():
    node = {"config": {"schema": {"a": {}}}}
    provider = _provider_returning("抱歉，我无法提取")
    agent = SimpleNamespace(model="m", runtime_config=None)
    output = flow_nodes.run_parameter_extract(node, {"input": "x"}, provider=provider, agent=agent)
    assert output["extracted"] == {"a": None}


def test_parameter_extract_requires_schema():
    with pytest.raises(ValueError):
        flow_nodes.run_parameter_extract({"config": {}}, {}, provider=_provider_returning("{}"),
                                         agent=SimpleNamespace(model="m"))


# ── 问题分类 ──────────────────────────────────────────


def test_classifier_returns_branch_next():
    node = {"config": {
        "classes": [
            {"label": "售后", "description": "退换修", "next": "after_sales"},
            {"label": "售前", "description": "产品咨询", "next": "presale"},
        ],
        "default": "fallback",
    }}
    agent = SimpleNamespace(model="m", runtime_config=None)
    output = flow_nodes.run_classifier(node, {"input": "我要退货"}, provider=_provider_returning('{"label": "售后"}'), agent=agent)
    assert output["classification"] == {"label": "售后", "matched": True}
    assert output["_next"] == "after_sales"


def test_classifier_unknown_label_falls_back_to_default():
    node = {"config": {"classes": [{"label": "售后", "next": "a"}], "default": "fallback"}}
    agent = SimpleNamespace(model="m", runtime_config=None)
    output = flow_nodes.run_classifier(node, {"input": "?"}, provider=_provider_returning('{"label": "其它"}'), agent=agent)
    assert output["classification"]["matched"] is False
    assert output["_next"] == "fallback"


def test_classifier_requires_classes():
    with pytest.raises(ValueError):
        flow_nodes.run_classifier({"config": {}}, {}, provider=_provider_returning("{}"),
                                  agent=SimpleNamespace(model="m"))


# ── 图解析：_next 优先 ────────────────────────────────


def test_resolve_next_prefers_node_computed_next():
    node = {"id": "c", "type": "Classifier", "config": {"classes": [], "default": "fallback"}}
    assert resolve_next(node, {"_next": "chosen"}, {}) == "chosen"
    assert resolve_next(node, {}, {}) == "fallback"


def test_validate_graph_accepts_new_node_types():
    nodes = [
        {"id": "start", "type": "Start"},
        {"id": "http", "type": "HTTP", "config": {"url": "https://example.com"}},
        {"id": "code", "type": "Code", "config": {"code": "print(1)"}},
        {"id": "assign", "type": "VariableAssign", "config": {"assignments": []}},
        {"id": "extract", "type": "ParameterExtract", "config": {"schema": {"a": {}}}},
        {"id": "cls", "type": "Classifier", "config": {"classes": [{"label": "x"}]}},
        {"id": "answer", "type": "Answer"},
    ]
    assert validate_graph(nodes) == []


# ── HTTP：参数校验与防护 ──────────────────────────────


def test_http_node_requires_url():
    with pytest.raises(ValueError, match="url"):
        flow_nodes.run_http_node({"config": {"url": ""}}, {})


def test_http_node_rejects_unsupported_method():
    with pytest.raises(ValueError, match="方法"):
        flow_nodes.run_http_node({"config": {"url": "https://example.com", "method": "TRACE"}}, {})


def test_http_node_blocks_private_hosts(monkeypatch):
    """走项目的 SSRF 防护：内网地址在连接前就被拒绝。"""
    node = {"config": {"url": "https://127.0.0.1/secret"}}
    with pytest.raises(ValueError):
        flow_nodes.run_http_node(node, {})


def test_http_node_renders_templates_into_url(monkeypatch):
    captured = {}

    def fake_open(request, *, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        raise ValueError("stop here")

    monkeypatch.setattr("core.security.outbound_http.open_public_https", fake_open)
    node = {"config": {"url": "https://example.com/search?q={{input}}", "timeout_seconds": 5}}
    with pytest.raises(ValueError, match="stop here"):
        flow_nodes.run_http_node(node, {"input": "退款"})
    assert "q=%E9%80%80%E6%AC%BE" in captured["url"] or "q=退款" in captured["url"]
    assert captured["timeout"] == 5


# ── 代码节点 ──────────────────────────────────────────


def test_code_node_requires_code():
    with pytest.raises(ValueError, match="代码"):
        flow_nodes.run_code_node({"config": {"code": ""}}, {})


def test_code_node_rejects_unknown_language():
    with pytest.raises(ValueError, match="语言"):
        flow_nodes.run_code_node({"config": {"code": "x", "language": "ruby"}}, {})


def test_code_node_passes_context_as_input_file(monkeypatch):
    captured = {}

    def fake_run(files, path, args):
        captured["files"] = {file.path: file.content_base64 for file in files}
        captured["path"] = path
        return {"exit_code": 0, "output": '{"sum": 3}', "truncated": False}

    monkeypatch.setattr(flow_nodes, "run_script", fake_run)
    node = {"config": {"code": "print('x')", "language": "python"}}
    context = {"input": "q", "sources": [{"title": "t", "snippet": "s", "score": 1, "chunk_id": "c", "content": "丢弃"}],
               "variables": {"a": 1}}
    output = flow_nodes.run_code_node(node, context)
    assert captured["path"] == "scripts/main.py"
    assert "scripts/input.json" in captured["files"]
    assert output["code"]["result"] == {"sum": 3}


def test_code_node_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(flow_nodes, "run_script",
                        lambda *a, **k: {"exit_code": 1, "output": "boom", "truncated": False})
    with pytest.raises(ValueError, match="退出码 1"):
        flow_nodes.run_code_node({"config": {"code": "x"}}, {})


# ── 循环节点 ──────────────────────────────────────────


def test_loop_runs_body_fixed_iterations():
    visited = []

    def execute(node, context):
        visited.append((node["id"], context["loop"]["iteration"]))
        return {"seen": context["loop"]["iteration"]}

    node = {"config": {"body": [{"id": "b1", "type": "LLM"}], "max_iterations": 3}}
    output = flow_nodes.run_loop_node(node, {}, execute=execute)
    assert output["loop"]["iterations"] == 3
    assert visited == [("b1", 1), ("b1", 2), ("b1", 3)]


def test_loop_iterates_items_and_exposes_loop_variable():
    collected = []

    def execute(node, context):
        collected.append((context["variables"]["item"], context["loop"]["index"]))
        return {}

    node = {"config": {
        "body": [{"id": "b1", "type": "LLM"}],
        "items": ["{{variables.list}}"],
        "loop_var": "item",
    }}
    context = {"variables": {"list": ["甲", "乙", "丙"]}}
    output = flow_nodes.run_loop_node(node, context, execute=execute)
    assert collected == [("甲", 0), ("乙", 1), ("丙", 2)]
    assert output["loop"]["iterations"] == 3


def test_loop_empty_items_runs_zero_times():
    called = []

    def execute(node, context):
        called.append(node["id"])
        return {}

    node = {"config": {"body": [{"id": "b1", "type": "LLM"}], "items": "{{variables.empty}}"}}
    output = flow_nodes.run_loop_node(node, {"variables": {"empty": []}}, execute=execute)
    assert called == []
    assert output["loop"]["iterations"] == 0


def test_loop_stops_early_when_condition_matches():
    def execute(node, context):
        return {"draft": f"第 {context['loop']['iteration']} 轮"}

    node = {
        "config": {
            "body": [{"id": "b1", "type": "LLM"}],
            "max_iterations": 5,
            "stop_when": {"field": "loop.iteration", "gte": 2},
        },
    }
    output = flow_nodes.run_loop_node(node, {}, execute=execute)
    assert output["loop"]["iterations"] == 2
    assert output["loop"]["stopped"] is True


def test_loop_clears_previous_draft_so_llm_runs_every_round():
    """主执行器里 LLM 见到 draft 会短路复用，循环里必须每轮清掉。"""
    drafts = []

    def execute(node, context):
        # 模拟 _execute_node 的 LLM 分支：有 draft 就直接复用
        drafts.append(context.get("draft"))
        return {"draft": f"答案 {context['loop']['iteration']}"}

    node = {"config": {"body": [{"id": "b1", "type": "LLM"}], "max_iterations": 3}}
    flow_nodes.run_loop_node(node, {}, execute=execute)
    assert drafts == [None, None, None]


def test_loop_body_supports_branching_within_body():
    order = []

    def execute(node, context):
        order.append(node["id"])
        if node["type"] == "Condition":
            return {}
        return {"value": 1}

    body = [
        {"id": "b1", "type": "LLM"},
        {"id": "cond", "type": "Condition", "config": {"branches": [{"when": {"field": "value", "gte": 1}, "next": "b3"}], "default": "b2"}},
        {"id": "b2", "type": "LLM"},
        {"id": "b3", "type": "LLM"},
    ]
    node = {"config": {"body": body, "max_iterations": 1}}
    flow_nodes.run_loop_node(node, {}, execute=execute)
    assert order == ["b1", "cond", "b3"]


def test_loop_rejects_start_or_answer_in_body():
    with pytest.raises(ValueError, match="Start 或 Answer"):
        flow_nodes.run_loop_node(
            {"config": {"body": [{"id": "b1", "type": "Answer"}]}}, {}, execute=lambda node, context: {}
        )


def test_loop_requires_body():
    with pytest.raises(ValueError, match="循环体"):
        flow_nodes.run_loop_node({"config": {}}, {}, execute=lambda node, context: {})


def test_loop_enforces_step_budget():
    def execute(node, context):
        return {}

    # 7 个节点 × 20 轮 = 140 步 > 120 的循环步数上限
    body = [{"id": f"b{index}", "type": "LLM"} for index in range(7)]
    node = {"config": {"body": body, "max_iterations": 20}}
    with pytest.raises(ValueError, match="节点"):
        flow_nodes.run_loop_node(node, {}, execute=execute)


def test_validate_graph_accepts_loop_and_checks_body():
    ok = [
        {"id": "start", "type": "Start"},
        {"id": "loop", "type": "Loop", "config": {"body": [{"id": "b1", "type": "LLM"}]}},
        {"id": "answer", "type": "Answer"},
    ]
    assert validate_graph(ok) == []

    empty = [
        {"id": "start", "type": "Start"},
        {"id": "loop", "type": "Loop", "config": {"body": []}},
        {"id": "answer", "type": "Answer"},
    ]
    assert any("non-empty body" in item for item in validate_graph(empty))

    with_answer = [
        {"id": "start", "type": "Start"},
        {"id": "loop", "type": "Loop", "config": {"body": [{"id": "b1", "type": "Answer"}]}},
        {"id": "answer", "type": "Answer"},
    ]
    assert any("Start or Answer" in item for item in validate_graph(with_answer))


# ── 迭代：结果收集 / 失败策略 / 并行 ─────────────────────────────


def _iterate_node(**config):
    return {"config": {
        "body": [{"id": "b1", "type": "LLM"}],
        "items": ["{{variables.list}}"],
        **config,
    }}


def test_loop_collects_each_round_output_into_results():
    def execute(node, context):
        return {"draft": f"处理了 {context['variables']['item']}"}

    context = {"variables": {"list": ["甲", "乙"]}}
    output = flow_nodes.run_loop_node(_iterate_node(), context, execute=execute)
    assert context["variables"]["results"] == ["处理了 甲", "处理了 乙"]
    assert output["variables"]["results"] == ["处理了 甲", "处理了 乙"]


def test_loop_failure_strategies():
    def execute(node, context):
        if context["variables"]["item"] == "坏":
            raise RuntimeError("模型超时")
        return {"draft": context["variables"]["item"]}

    def run(on_error):
        context = {"variables": {"list": ["好", "坏", "好2"]}}
        return context, flow_nodes.run_loop_node(_iterate_node(on_error=on_error), context, execute=execute)

    context, output = run("skip")
    assert context["variables"]["results"] == ["好", "好2"]
    assert output["loop"]["failures"][0]["iteration"] == 2

    context, _ = run("null")
    assert context["variables"]["results"] == ["好", None, "好2"]

    import pytest
    with pytest.raises(ValueError, match="第 2 轮失败"):
        run("stop")


def test_loop_parallel_keeps_order_and_isolates_items():
    import threading
    import time

    active = {"now": 0, "peak": 0}
    lock = threading.Lock()
    closed = []

    def factory():
        def run(node, context):
            with lock:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.05)
            with lock:
                active["now"] -= 1
            return {"draft": context["variables"]["item"] * 2}
        return run, lambda: closed.append(1)

    context = {"variables": {"list": [1, 2, 3, 4, 5, 6]}}
    flow_nodes.run_loop_node(
        _iterate_node(parallel=3), context,
        execute=lambda *_: (_ for _ in ()).throw(AssertionError("并行模式不该走串行执行器")),
        execute_factory=factory,
    )
    assert context["variables"]["results"] == [2, 4, 6, 8, 10, 12]  # 按原数组顺序
    assert active["peak"] == 3  # 真的并行，且不超过上限
    assert len(closed) == 6  # 每项的执行器都被关闭（数据库会话不泄漏）
    assert "item" not in context["variables"]  # 并行各项在副本里跑，不污染主上下文


def test_variable_aggregate_picks_first_non_empty():
    context = {"variables": {"a": "", "b": "来自分支 B"}}
    node = {"config": {"sources": ["{{variables.a}}", "{{variables.b}}", "兜底"], "output_var": "reply"}}
    output = flow_nodes.run_variable_aggregate(node, context)
    assert output["variables"]["reply"] == "来自分支 B"
    assert output["aggregated"]["source_index"] == 1

    import pytest
    with pytest.raises(ValueError):
        flow_nodes.run_variable_aggregate({"config": {"sources": [], "output_var": "x"}}, {})
