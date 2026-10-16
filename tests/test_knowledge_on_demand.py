"""知识库按需调用：检索方式配置、Knowledge 节点让行、search_knowledge_base 工具。"""

import json
from types import SimpleNamespace

import pytest

from core.integrations import llm
from core.runtime import workflow as workflow_module
from core.runtime.workflow import KNOWLEDGE_TOOL_NAME, WorkflowRunner
from core.services.agents import normalize_rag
from core.services.rag import RagResult


# ── 配置 ───────────────────


def test_normalize_rag_defaults_legacy_data_to_auto():
    assert normalize_rag({})["retrieval_mode"] == "auto"
    assert normalize_rag(None)["retrieval_mode"] == "auto"
    assert normalize_rag({"retrieval_mode": "on_demand"})["retrieval_mode"] == "on_demand"
    assert normalize_rag({"retrieval_mode": "bogus"})["retrieval_mode"] == "auto"


def test_new_agent_defaults_to_on_demand_and_update_keeps_mode(client, auth_headers):
    created = client.post("/api/agents", headers=auth_headers, json={"name": "On demand default"}).json()["agent"]
    assert created["rag"]["retrieval_mode"] == "on_demand"
    # 只改 top_k、不带检索方式的更新不能把模式回落成 auto
    patched = client.patch(f"/api/agents/{created['id']}", headers=auth_headers, json={"rag": {"top_k": 6}}).json()["agent"]
    assert patched["rag"]["retrieval_mode"] == "on_demand" and patched["rag"]["top_k"] == 6
    switched = client.patch(f"/api/agents/{created['id']}", headers=auth_headers,
                            json={"rag": {"top_k": 6, "retrieval_mode": "auto"}}).json()["agent"]
    assert switched["rag"]["retrieval_mode"] == "auto"
    explicit = client.post("/api/agents", headers=auth_headers,
                           json={"name": "Explicit auto", "rag": {"retrieval_mode": "auto"}}).json()["agent"]
    assert explicit["rag"]["retrieval_mode"] == "auto"


# ── 运行时（不连库的单元层） ───────────────────


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return self.rows


def _runner(kbs):
    runner = WorkflowRunner(SimpleNamespace(query=lambda model: _Query(kbs)))
    return runner


def _kb(kb_id, name, description=""):
    return SimpleNamespace(id=kb_id, name=name, description=description, enabled=True)


def _agent(kb_ids=(11,)):
    return SimpleNamespace(workspace_id=7, knowledge_base_ids=list(kb_ids), runtime_config=None, settings={},
                           model="m", temperature=0.4)


def _context(mode="on_demand", **extra):
    return {"input": "营业时间", "rag_enabled": True, "rag_config": normalize_rag({"retrieval_mode": mode}),
            "sources": [], "user_id": 1, **extra}


def _source(chunk_id, title="手册", snippet="内容"):
    return {"chunk_id": chunk_id, "title": title, "snippet": snippet, "content": snippet}


def test_knowledge_node_skips_retrieval_in_on_demand_mode(monkeypatch):
    runner = _runner([])
    monkeypatch.setattr(workflow_module, "retrieve", lambda *a, **k: pytest.fail("on_demand 模式不应在 Knowledge 节点检索"))
    output = runner._execute_node(_agent(), {"type": "Knowledge"}, _context())
    assert output["sources"] == [] and output["rag_status"]["reason"] == "on_demand"
    assert "draft" not in output and not output.get("rag_refusal")


def test_knowledge_tool_only_offered_in_on_demand_with_bound_kb():
    runner = _runner([_kb(11, "咖啡手册", "门店营业与饮品价格")])
    assert runner._knowledge_tools(_agent(), _context("auto")) == []
    assert runner._knowledge_tools(_agent(), _context(rag_enabled=False)) == []
    assert runner._knowledge_tools(_agent(kb_ids=()), _context()) == []
    assert _runner([])._knowledge_tools(_agent(), _context()) == []  # 绑定的库全部停用
    [tool] = runner._knowledge_tools(_agent(), _context())
    function = tool.llm_schema["function"]
    assert function["name"] == KNOWLEDGE_TOOL_NAME
    assert "咖啡手册：门店营业与饮品价格" in function["description"] and "web_search" in function["description"]
    # 只有一个库时不提供 knowledge_base 参数
    assert set(function["parameters"]["properties"]) == {"query"}


def test_knowledge_tool_lists_kb_enum_when_multiple():
    runner = _runner([_kb(11, "咖啡手册", "门店"), _kb(12, "员工制度")])
    [tool] = runner._knowledge_tools(_agent(kb_ids=(11, 12)), _context())
    parameters = tool.llm_schema["function"]["parameters"]["properties"]
    assert parameters["knowledge_base"]["enum"] == ["咖啡手册", "员工制度"]
    assert "- 员工制度" in tool.llm_schema["function"]["description"]


def test_invoke_knowledge_tool_numbers_dedupes_and_targets_kb(monkeypatch):
    runner = _runner([_kb(11, "咖啡手册"), _kb(12, "员工制度")])
    agent = _agent(kb_ids=(11, 12))
    context = _context()
    [tool] = runner._knowledge_tools(agent, context)
    calls = []
    results = [[_source("a", snippet="早八晚十"), _source("b", snippet="周末延长")], [_source("b", snippet="周末延长"), _source("c", snippet="请假流程")]]

    def fake_retrieve(db, **kwargs):
        calls.append(kwargs)
        return RagResult(results.pop(0), {"knowledge_base_ids": kwargs["knowledge_base_ids"], "no_evidence": False})

    monkeypatch.setattr(workflow_module, "retrieve", fake_retrieve)
    first = runner._invoke_knowledge_tool(agent, context, tool, {"query": "营业时间"})
    second = runner._invoke_knowledge_tool(agent, context, tool, {"query": "请假", "knowledge_base": "员工制度"})
    assert calls[0]["knowledge_base_ids"] == [11, 12] and calls[1]["knowledge_base_ids"] == [12]
    assert "[1] 手册：早八晚十" in first["content"] and "[2] 手册：周末延长" in first["content"]
    # 第二次调用里重复命中的 b 沿用编号 2，新片段 c 接着编号 3
    assert "[2] 手册：周末延长" in second["content"] and "[3] 手册：请假流程" in second["content"]
    assert [item["chunk_id"] for item in context["sources"]] == ["a", "b", "c"]
    assert context["rag_grounded"] is True
    assert context["rag_status"]["trigger"] == "tool" and context["rag_status"]["query"] == "请假"


def test_invoke_knowledge_tool_reports_empty_and_enforces_call_limit(monkeypatch):
    runner = _runner([_kb(11, "咖啡手册")])
    agent = _agent()
    context = _context()
    [tool] = runner._knowledge_tools(agent, context)
    monkeypatch.setattr(workflow_module, "retrieve", lambda db, **k: RagResult([], {"no_evidence": True}))
    empty = runner._invoke_knowledge_tool(agent, context, tool, {"query": "火星"})
    assert "没有找到" in empty["content"] and context["sources"] == [] and context["rag_grounded"] is False
    runner._invoke_knowledge_tool(agent, context, tool, {"query": "火星2"})
    runner._invoke_knowledge_tool(agent, context, tool, {"query": "火星3"})
    with pytest.raises(ValueError, match="上限"):
        runner._invoke_knowledge_tool(agent, context, tool, {"query": "火星4"})


def test_tool_node_runs_knowledge_tool_and_emits_rag_status(monkeypatch):
    runner = _runner([_kb(11, "咖啡手册", "营业时间")])
    monkeypatch.setattr(runner, "_runtime_tools", lambda *args: [])
    monkeypatch.setattr(runner, "_llm_messages", lambda *args: [{"role": "user", "content": "几点开门"}])
    monkeypatch.setattr(workflow_module, "retrieve",
                        lambda db, **k: RagResult([_source("a", snippet="早八点开门")], {"no_evidence": False}))
    responses = [
        llm.ChatResponse(tool_calls=[{"id": "c1", "type": "function",
                                      "function": {"name": KNOWLEDGE_TOOL_NAME, "arguments": json.dumps({"query": "开门时间"})}}]),
        llm.ChatResponse(content="早上八点开门[1]"),
    ]
    seen_tools = []

    def chat(messages, **kwargs):
        seen_tools.append([tool["function"]["name"] for tool in kwargs.get("tools") or []])
        return responses.pop(0)

    runner.provider = SimpleNamespace(chat=chat, last_chat_mock=False)
    context = _context()
    output = runner._execute_node(_agent(), {"type": "Tool"}, context)
    assert seen_tools[0] == [KNOWLEDGE_TOOL_NAME]
    assert output["draft"] == "早上八点开门[1]"
    assert [event["event"] for event in output["events"]] == ["rag_status", "tool_call"]
    assert context["sources"][0]["chunk_id"] == "a"


def test_knowledge_tool_bypasses_tool_allowlist(monkeypatch):
    runner = _runner([_kb(11, "咖啡手册")])
    monkeypatch.setattr(runner, "_runtime_tools", lambda *args: [])
    monkeypatch.setattr(runner, "_llm_messages", lambda *args: [{"role": "user", "content": "hi"}])
    seen = []
    runner.provider = SimpleNamespace(last_chat_mock=False, chat=lambda messages, **kwargs: (
        seen.append([tool["function"]["name"] for tool in kwargs["tools"]]) or llm.ChatResponse(content="hi")))
    agent = _agent()
    agent.settings = {"tool_policy": {"allowed_tool_names": ["calculator"]}}
    runner._execute_node(agent, {"type": "Tool"}, _context())
    assert seen == [[KNOWLEDGE_TOOL_NAME]]


def test_query_understanding_skipped_in_on_demand_mode():
    runner = WorkflowRunner(SimpleNamespace())
    runner.provider = SimpleNamespace(chat=lambda *a, **k: pytest.fail("on_demand 模式不应调用查询理解"))
    context = {**_context(), "knowledge_base_ids": [11], "conversation_history": []}
    runner._understand_query(SimpleNamespace(settings={}, runtime_config=None), context)
    assert context["rewritten_query"] == "营业时间"
    assert context["query_understanding_event"]["reason"] == "on_demand"


def test_on_demand_chat_end_to_end_with_mock_llm(client, auth_headers):
    """mock LLM 有工具时总会调第一个工具：端到端验证工具检索出的片段进入引用来源。"""
    kb_id = client.post("/api/knowledge-bases", headers=auth_headers,
                        json={"name": "星澜手册", "description": "门店营业时间"}).json()["knowledge_base"]["id"]
    doc = client.post(f"/api/knowledge-bases/{kb_id}/documents", headers=auth_headers,
                      json={"title": "营业时间", "content": "星澜咖啡馆每天早上八点开门，晚上十点打烊。"})
    assert doc.status_code == 200
    agent_id = client.post("/api/agents", headers=auth_headers,
                           json={"name": "按需助手", "knowledge_base_ids": [kb_id]}).json()["agent"]["id"]
    response = client.post(f"/api/agents/{agent_id}/chat/stream", headers=auth_headers,
                           json={"message": "星澜咖啡馆几点开门", "mode": "draft", "rag_enabled": True})
    assert response.status_code == 200
    assert "event: error" not in response.text
    assert f'"tool_name": "{KNOWLEDGE_TOOL_NAME}"' in response.text
    assert '"trigger": "tool"' in response.text
    assert "event: sources" in response.text and "星澜咖啡馆每天早上八点开门" in response.text
