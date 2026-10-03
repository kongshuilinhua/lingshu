"""MCP 接入 agent 工具链测试(方向 3 切片 2)。

覆盖:
1. tool_schema_for_llm 对 mcp 类型用 server 声明的 inputSchema。
2. execute_tool 真实分发到 _execute_mcp_tool,转发到 MCP server(桩 client)回流 text。
3. _runtime_tools 把绑定的 MCP server 工具包装成 type="mcp" 工具。
4. server 不可达时跳过其工具,不阻断主流程。
"""

from types import SimpleNamespace

from core.db.models import AgentMcpBinding
from core.runtime.workflow import WorkflowRunner
from core.services.tools import execute_tool, tool_schema_for_llm


def test_tool_schema_for_llm_mcp_uses_input_schema():
    tool = SimpleNamespace(
        name="mcp_1_echo",
        label="echo",
        description="回显输入",
        type="mcp",
        schema={
            "mcp_server_id": 1,
            "tool_name": "echo",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        },
        enabled=True,
    )
    schema = tool_schema_for_llm(tool)
    assert schema["function"]["name"] == "mcp_1_echo"
    params = schema["function"]["parameters"]
    assert params["properties"]["text"]["type"] == "string"
    assert params["required"] == ["text"]


def test_tool_schema_for_llm_mcp_fallback_without_input_schema():
    tool = SimpleNamespace(
        name="mcp_1_x",
        label="x",
        description="x",
        type="mcp",
        schema={"mcp_server_id": 1, "tool_name": "x"},
        enabled=True,
    )
    params = tool_schema_for_llm(tool)["function"]["parameters"]
    assert "input" in params["properties"]
    assert params["required"] == ["input"]


def test_execute_mcp_tool_calls_server_and_returns_text(monkeypatch):
    server = SimpleNamespace(id=1, command=["x"], env={}, enabled=True)
    stub_db = SimpleNamespace(get=lambda *a, **k: server)
    fake_client = SimpleNamespace(call_tool=lambda name, args: f"MCP:{name}:{args}")

    import core.integrations.mcp_client as mcp_mod

    monkeypatch.setattr(mcp_mod, "get_mcp_client", lambda srv: fake_client)

    tool = SimpleNamespace(name="mcp_1_echo", type="mcp", schema={"mcp_server_id": 1, "tool_name": "echo"}, enabled=True)
    result = execute_tool(tool, {"input": {"text": "hi"}, "_db": stub_db})
    assert result["tool_type"] == "mcp"
    assert "MCP:echo:" in result["content"]
    assert "hi" in result["content"]


def test_runtime_tools_wraps_mcp_server_tools(monkeypatch):
    server = SimpleNamespace(id=1, command=["x"], env={}, enabled=True, name="echo")
    binding = SimpleNamespace(agent_id=10, mcp_server_id=1, enabled=True)
    fake_client = SimpleNamespace(
        list_tools=lambda: [
            {"name": "echo", "description": "回显", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
        ]
    )

    import core.runtime.workflow as wf_mod

    monkeypatch.setattr(wf_mod, "get_mcp_client", lambda srv: fake_client)

    def query(model, *a, **k):
        class _Q:
            def __init__(self):
                self._model = model

            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return [binding] if self._model is AgentMcpBinding else []

        return _Q()

    stub_db = SimpleNamespace(query=query, get=lambda *a, **k: server)
    runner = WorkflowRunner(db=stub_db)
    tools = runner._runtime_tools(SimpleNamespace(id=10, tool_ids=[]), {"type": "Tool"})
    mcp_tools = [t for t in tools if getattr(t, "type", None) == "mcp"]
    assert len(mcp_tools) == 1
    mt = mcp_tools[0]
    assert mt.name == "mcp_1_echo"
    assert mt.type == "mcp"
    assert mt.schema["mcp_server_id"] == 1
    assert mt.schema["tool_name"] == "echo"
    assert mt.schema["input_schema"]["properties"]["text"]["type"] == "string"


def test_runtime_tools_skips_unreachable_mcp_server(monkeypatch):
    binding = SimpleNamespace(agent_id=10, mcp_server_id=1, enabled=True)
    server = SimpleNamespace(id=1, command=["x"], env={}, enabled=True)

    import core.runtime.workflow as wf_mod

    monkeypatch.setattr(wf_mod, "get_mcp_client", lambda srv: (_ for _ in ()).throw(RuntimeError("unreachable")))

    def query(model, *a, **k):
        class _Q:
            def __init__(self):
                self._model = model

            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return [binding] if self._model is AgentMcpBinding else []

        return _Q()

    stub_db = SimpleNamespace(query=query, get=lambda *a, **k: server)
    runner = WorkflowRunner(db=stub_db)
    tools = runner._runtime_tools(SimpleNamespace(id=10, tool_ids=[]), {"type": "Tool"})
    assert not any(getattr(t, "type", None) == "mcp" for t in tools)


def test_runtime_tools_skips_disabled_mcp_server(monkeypatch):
    binding = SimpleNamespace(agent_id=10, mcp_server_id=1, enabled=True)
    server = SimpleNamespace(id=1, command=["x"], env={}, enabled=False)  # server 被禁用

    import core.runtime.workflow as wf_mod

    monkeypatch.setattr(wf_mod, "get_mcp_client", lambda srv: (_ for _ in ()).throw(AssertionError("disabled server 不该连")))

    def query(model, *a, **k):
        class _Q:
            def __init__(self):
                self._model = model

            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return [binding] if self._model is AgentMcpBinding else []

        return _Q()

    stub_db = SimpleNamespace(query=query, get=lambda *a, **k: server)
    runner = WorkflowRunner(db=stub_db)
    tools = runner._runtime_tools(SimpleNamespace(id=10, tool_ids=[]), {"type": "Tool"})
    assert not any(getattr(t, "type", None) == "mcp" for t in tools)
