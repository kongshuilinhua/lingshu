"""Agent-as-Tool 测试:supervisor 调用已发布子 agent。

三层覆盖:
1. tool_schema_for_llm 对 agent 类型生成正确 schema。
2. execute_tool 真实分发到 _execute_agent_tool,调用子 agent(桩 WorkflowRunner.run)回流 answer。
3. 防递归:target 已在 agent_call_stack 中则拒绝。
4. _runtime_tools 把绑定的已发布子 agent 包装成 type="agent" 工具。
"""

from types import SimpleNamespace

import pytest

from core.db.models import AgentAgentBinding
from core.runtime.workflow import WorkflowRunner
from core.services.tools import execute_tool, tool_schema_for_llm


def test_tool_schema_for_llm_agent_type():
    tool = SimpleNamespace(
        name="call_agent_2",
        label="子agent",
        description="委派任务给子智能体",
        type="agent",
        schema={"target_agent_id": 2},
        enabled=True,
    )
    schema = tool_schema_for_llm(tool)
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "call_agent_2"
    assert schema["function"]["description"] == "委派任务给子智能体"
    params = schema["function"]["parameters"]
    assert params["type"] == "object"
    assert params["properties"]["input"]["type"] == "string"
    assert params["required"] == ["input"]


def _make_target():
    return SimpleNamespace(id=2, published_version_id=99, workspace_id=1, name="子agent")


def _make_stub_db(target):
    return SimpleNamespace(
        get=lambda *a, **k: target,
        add=lambda *a, **k: None,
        flush=lambda *a, **k: None,
    )


def _make_agent_tool():
    return SimpleNamespace(
        id="agent_2",
        name="call_agent_2",
        label="子agent",
        description="委派任务给子智能体",
        type="agent",
        schema={"target_agent_id": 2},
        enabled=True,
    )


def test_execute_agent_tool_returns_sub_agent_answer(monkeypatch):
    """execute_tool 真实分发到 _execute_agent_tool,调用子 agent(桩 run)回流 answer。"""
    target = _make_target()
    stub_db = _make_stub_db(target)
    captured: dict = {}

    def fake_run(self, **kw):
        captured["user_message"] = kw.get("user_message")
        captured["call_stack"] = kw.get("_agent_call_stack")
        captured["mode"] = kw.get("mode")
        return SimpleNamespace(id=99), "子agent的回答", [], []

    monkeypatch.setattr(WorkflowRunner, "run", fake_run)

    result = execute_tool(
        _make_agent_tool(),
        {"input": {"input": "帮我查X"}, "_db": stub_db, "_user_id": 1, "agent_call_stack": []},
    )
    assert result["content"] == "子agent的回答"
    assert result["tool_type"] == "agent"
    # 子任务文本从 tool_args["input"] 提取
    assert captured["user_message"] == "帮我查X"
    # target 入栈传给子 run,供下一层防递归判断
    assert captured["call_stack"] == [2]
    # 子 agent 用发布快照跑
    assert captured["mode"] == "published"


def test_execute_agent_tool_blocks_recursion(monkeypatch):
    """target 已在调用栈中 → 拒绝,防 A→B→A 死循环。"""
    target = _make_target()
    stub_db = _make_stub_db(target)
    monkeypatch.setattr(WorkflowRunner, "run", lambda self, **kw: (_ for _ in ()).throw(AssertionError("子 agent 不该被调到")))

    with pytest.raises(ValueError, match="Recursive"):
        execute_tool(
            _make_agent_tool(),
            {"input": {"input": "x"}, "_db": stub_db, "_user_id": 1, "agent_call_stack": [2]},
        )


def test_execute_agent_tool_rejects_unpublished_target(monkeypatch):
    """未发布的子 agent 不可被调用。"""
    target = SimpleNamespace(id=3, published_version_id=None, workspace_id=1, name="草稿agent")
    stub_db = _make_stub_db(target)
    monkeypatch.setattr(WorkflowRunner, "run", lambda self, **kw: (_ for _ in ()).throw(AssertionError("不该跑")))

    with pytest.raises(ValueError, match="no published version"):
        execute_tool(
            _make_agent_tool(),
            {"input": {"input": "x"}, "_db": stub_db, "_user_id": 1, "agent_call_stack": []},
        )


def test_runtime_tools_wraps_published_sub_agents(monkeypatch):
    """_runtime_tools 把绑定的已发布子 agent 包装成 type="agent" 工具。"""
    target = _make_target()
    binding = SimpleNamespace(agent_id=1, target_agent_id=2, enabled=True)

    def query(model, *a, **k):
        class _Q:
            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return [binding] if model is AgentAgentBinding else []

        return _Q()

    stub_db = SimpleNamespace(query=query, get=lambda *a, **k: target)
    runner = WorkflowRunner(db=stub_db)
    agent = SimpleNamespace(id=1, tool_ids=[])
    tools = runner._runtime_tools(agent, {"type": "Tool"})
    agent_tools = [t for t in tools if getattr(t, "type", None) == "agent"]
    assert len(agent_tools) == 1
    at = agent_tools[0]
    assert at.name == "call_agent_2"
    assert at.type == "agent"
    assert at.schema == {"target_agent_id": 2}
    assert at.enabled is True


def test_runtime_tools_skips_unpublished_sub_agents(monkeypatch):
    """未发布的子 agent 不被包装(避免调用一个没有发布快照的 agent)。"""
    target = SimpleNamespace(id=3, published_version_id=None, workspace_id=1, name="草稿")
    binding = SimpleNamespace(agent_id=1, target_agent_id=3, enabled=True)

    def query(model, *a, **k):
        class _Q:
            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return [binding] if model is AgentAgentBinding else []

        return _Q()

    stub_db = SimpleNamespace(query=query, get=lambda *a, **k: target)
    runner = WorkflowRunner(db=stub_db)
    tools = runner._runtime_tools(SimpleNamespace(id=1, tool_ids=[]), {"type": "Tool"})
    assert not any(getattr(t, "type", None) == "agent" for t in tools)
