"""工具循环流式化：OpenAI 兼容流式 tool_calls 拼装 + Tool 节点实时推送正文/思考。"""

import json
from types import SimpleNamespace

import pytest

from core.integrations import llm
from core.runtime.workflow import WorkflowRunner, _drain


class _Response:
    """模拟 urlopen 的流式响应：逐行迭代 SSE 字节。"""

    status = 200

    def __init__(self, chunks):
        self.lines = [f"data: {json.dumps(chunk)}\n".encode() for chunk in chunks] + [b"data: [DONE]\n"]

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def live_settings(monkeypatch):
    settings = SimpleNamespace(mock_llm=False, openai_model="m", llm_max_tokens=0, circuit_breaker_distributed=False)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    provider = llm.OpenAICompatibleProvider()
    monkeypatch.setattr(provider, "_api_key", lambda *args, **kwargs: "k")
    monkeypatch.setattr(provider, "_api_base", lambda *args, **kwargs: "https://gw.example/v1")
    monkeypatch.setattr(provider, "_supports_enable_thinking", lambda *args: False)
    return provider


def _delta(**delta):
    return {"choices": [{"delta": delta}]}


def test_openai_stream_assembles_split_tool_call_arguments(live_settings, monkeypatch):
    chunks = [
        _delta(content="先查一下。"),
        _delta(tool_calls=[{"index": 0, "id": "call_a", "function": {"name": "search", "arguments": '{"q":'}}]),
        _delta(tool_calls=[{"index": 1, "id": "call_b", "function": {"name": "clock", "arguments": ""}}]),
        _delta(tool_calls=[{"index": 0, "function": {"arguments": '"咖啡"}'}}]),
        _delta(tool_calls=[{"index": 1, "function": {"arguments": "{}"}}]),
    ]
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda request, timeout: _Response(chunks))
    frames = list(live_settings.chat_stream([{"role": "user", "content": "hi"}], tools=[{"type": "function"}]))
    assert frames[0] == {"type": "content", "text": "先查一下。"}
    assert frames[-1]["type"] == "tool_calls"
    calls = frames[-1]["calls"]
    assert [call["id"] for call in calls] == ["call_a", "call_b"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"q": "咖啡"}
    assert calls[1]["function"] == {"name": "clock", "arguments": "{}"}


def test_openai_stream_without_tool_calls_has_no_tool_frame(live_settings, monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda request, timeout: _Response([_delta(content="答")]))
    assert list(live_settings.chat_stream([{"role": "user", "content": "hi"}])) == [{"type": "content", "text": "答"}]


# ── workflow 层 ───────────────────


class _ScriptedProvider:
    """按轮次回放预置帧序列的 provider 替身；chat() 用于非流式回退。"""

    def __init__(self, turns, chat_response=None):
        self.turns = list(turns)
        self.calls = []
        self.chat_response = chat_response
        self.last_chat_mock = False

    def chat_stream(self, messages, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], **kwargs})
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        yield from turn

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "non_stream": True, **kwargs})
        return self.chat_response


def _runner(monkeypatch, provider, tool_result="tool says hi"):
    runner = WorkflowRunner(SimpleNamespace())
    runner.provider = provider
    tool = SimpleNamespace(id=1, name="echo", label="echo", description="Echo", type="http",
                           schema={}, query_schema={"text": {"required": True}}, method="GET")
    monkeypatch.setattr(runner, "_runtime_tools", lambda *args: [tool])
    monkeypatch.setattr(runner, "_llm_messages", lambda *args: [{"role": "user", "content": "hi"}])
    monkeypatch.setattr("core.runtime.workflow.execute_tool",
                        lambda tool, ctx: {"tool": "echo", "tool_type": "http", "content": tool_result})
    return runner


def _agent():
    return SimpleNamespace(settings={}, model="m", temperature=0.4, runtime_config=None, workspace_id=1)


def _collect(generator):
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as stop:
            return events, stop.value


def _call(name="echo", call_id="call_1", arguments='{"text": "x"}'):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_tool_node_streams_direct_answer_without_extra_round(monkeypatch):
    provider = _ScriptedProvider([[{"type": "reasoning", "text": "想"}, {"type": "content", "text": "直接"},
                                   {"type": "content", "text": "回答"}]])
    runner = _runner(monkeypatch, provider)
    context = {"user_id": 1, "thinking_enabled": True, "thinking_status": {"type": "native"}}
    events, output = _collect(runner._tool_node(_agent(), {"type": "Tool"}, context, stream=True))
    assert events == [{"event": "thinking_token", "content": "想"}, {"event": "token", "content": "直接"},
                      {"event": "token", "content": "回答"}]
    assert output["draft"] == "直接回答" and output["draft_streamed"] is True
    assert len(provider.calls) == 1 and provider.calls[0]["thinking"] is True and provider.calls[0]["tools"]


def test_tool_node_streams_after_tool_call_and_resets_preamble(monkeypatch):
    provider = _ScriptedProvider([
        [{"type": "content", "text": "我先查一下。"}, {"type": "tool_calls", "calls": [_call()]}],
        [{"type": "content", "text": "查到了"}],
    ])
    runner = _runner(monkeypatch, provider)
    events, output = _collect(runner._tool_node(_agent(), {"type": "Tool"}, {"user_id": 1}, stream=True))
    assert [event["event"] for event in events] == ["token", "token_reset", "runtime", "token"]
    assert events[1]["content"] == "我先查一下。"
    # 工具事件在工具执行完立即推送（早于下一轮正文），节点结束时不再重复
    assert events[2]["data"]["event"] == "tool_call" and events[2]["data"]["data"]["tool_name"] == "echo"
    assert output["events_streamed"] is True
    assert output["draft"] == "查到了" and output["tool_stats"]["total_calls"] == 1
    second = provider.calls[1]["messages"]
    assert second[1]["role"] == "assistant" and second[1]["tool_calls"][0]["id"] == "call_1"
    assert second[2] == {"role": "tool", "tool_call_id": "call_1", "content": "tool says hi"}


def test_tool_node_replays_reasoning_and_provider_content(monkeypatch):
    blocks = [{"type": "thinking", "thinking": "plan", "signature": "s"}]
    provider = _ScriptedProvider([
        [{"type": "reasoning", "text": "plan"}, {"type": "tool_calls", "calls": [_call()], "provider_content": blocks}],
        [{"type": "content", "text": "ok"}],
    ])
    runner = _runner(monkeypatch, provider)
    _collect(runner._tool_node(_agent(), {"type": "Tool"}, {"user_id": 1}, stream=True))
    assistant = provider.calls[1]["messages"][1]
    assert assistant["anthropic_content"] == blocks and assistant["reasoning_content"] == "plan"


def test_tool_node_holds_text_style_function_calls(monkeypatch):
    text_call = "<function=echo><parameter=text>x</parameter></function>"
    provider = _ScriptedProvider([
        [{"type": "content", "text": text_call[:6]}, {"type": "content", "text": text_call[6:]}],
        [{"type": "content", "text": "final"}],
    ])
    runner = _runner(monkeypatch, provider)
    events, output = _collect(runner._tool_node(_agent(), {"type": "Tool"}, {"user_id": 1}, stream=True))
    assert [event for event in events if event["event"] != "runtime"] == [{"event": "token", "content": "final"}]
    assert output["tool_stats"]["total_calls"] == 1 and output["draft"] == "final"


def test_tool_node_flushes_held_prefix_that_is_not_a_call(monkeypatch):
    provider = _ScriptedProvider([[{"type": "content", "text": "<"}, {"type": "content", "text": "b>粗体</b>"}]])
    runner = _runner(monkeypatch, provider)
    events, output = _collect(runner._tool_node(_agent(), {"type": "Tool"}, {"user_id": 1}, stream=True))
    assert "".join(event["content"] for event in events) == "<b>粗体</b>"
    assert output["draft"] == "<b>粗体</b>"


def test_tool_node_falls_back_to_non_stream_when_stream_rejected(monkeypatch):
    provider = _ScriptedProvider([RuntimeError("HTTP 400 stream tools unsupported")],
                                 chat_response=llm.ChatResponse(content="非流式答案"))
    runner = _runner(monkeypatch, provider)
    events, output = _collect(runner._tool_node(_agent(), {"type": "Tool"}, {"user_id": 1}, stream=True))
    assert events == []
    assert output["draft"] == "非流式答案" and output["draft_streamed"] is False
    assert provider.calls[-1]["non_stream"] is True


def test_tool_node_non_stream_path_unchanged(monkeypatch):
    responses = [llm.ChatResponse(tool_calls=[_call()]), llm.ChatResponse(content="done")]
    provider = _ScriptedProvider([])
    provider.chat = lambda messages, **kwargs: responses.pop(0)
    runner = _runner(monkeypatch, provider)
    output = _drain(runner._tool_node(_agent(), {"type": "Tool"}, {"user_id": 1}, stream=False))
    assert output["draft"] == "done" and output["draft_streamed"] is False
    assert output["tool_stats"]["total_calls"] == 1 and output["events_streamed"] is False


def test_llm_node_does_not_restream_streamed_tool_draft():
    runner = WorkflowRunner(db=None)
    runner.provider = SimpleNamespace(last_chat_mock=False)
    agent = SimpleNamespace(model="m")
    events, output = _collect(runner._stream_llm_node(agent, {"type": "LLM"}, {"draft": "已推过", "draft_streamed": True}))
    assert events == [] and output["draft"] == "已推过"
