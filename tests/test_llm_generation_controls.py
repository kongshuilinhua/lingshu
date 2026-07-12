"""LLM 生成控制单元测试：生成参数限制、流式帧协议与深度思考独立通道。

覆盖三层契约：
- 生成限制：`_apply_generation_limits` 统一注入 `max_tokens`，并对 Qwen3 系列显式下发
  `enable_thinking`（thinking=False 时显式关闭，避免混合推理模型默认思考拖慢首字输出）。
- provider 层：`_stream_delta` 把 `reasoning_content` 拆成独立 reasoning 帧（不再丢弃、也绝不混入正文）；
  `chat_stream` 统一产出 `{"type": "content"|"reasoning", "text": ...}` 帧协议（mock 路径同协议）。
- workflow 层：`_stream_llm_node` 把 reasoning 帧转成 `thinking_token` 事件，思考文本不得拼入 draft
  （draft 会存库并进入会话记忆）。
"""

from types import SimpleNamespace

from core.integrations import llm as llm_module
from core.integrations.llm import OpenAICompatibleProvider
from core.runtime.workflow import WorkflowRunner


def _settings(**overrides):
    data = {
        "mock_llm": False,
        "openai_api_base": "https://api.siliconflow.cn/v1",
        "openai_api_key": "test-key",
        "openai_model": "Qwen/Qwen3.5-122B-A10B",
        "dashscope_api_key": None,
        "deepseek_api_base": "https://api.deepseek.com",
        "deepseek_api_key": None,
        "deepseek_model": "deepseek-chat",
        "llm_max_tokens": 8192,
        "circuit_breaker_distributed": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


# ── 生成限制：max_tokens 与 enable_thinking 显式下发 ─────────


def test_chat_disables_qwen3_thinking_by_default(monkeypatch):
    captured = {}
    provider = OpenAICompatibleProvider()
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())

    def fake_post(url, payload, api_key):
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "4"}}]}

    monkeypatch.setattr(provider, "_post_json", fake_post)

    provider.chat([{"role": "user", "content": "2+2"}])

    assert captured["payload"]["enable_thinking"] is False
    assert captured["payload"]["max_tokens"] == 8192


def test_chat_stream_allows_qwen3_thinking_when_requested(monkeypatch):
    captured = {}
    provider = OpenAICompatibleProvider()
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())

    def fake_stream(url, payload, api_key):
        captured["payload"] = payload
        yield {"type": "content", "text": "ok"}

    monkeypatch.setattr(provider, "_post_json_stream", fake_stream)

    frames = list(provider.chat_stream([{"role": "user", "content": "think"}], thinking=True))

    assert frames == [{"type": "content", "text": "ok"}]
    assert captured["payload"]["enable_thinking"] is True
    assert captured["payload"]["max_tokens"] == 8192


def test_chat_forces_thinking_off_for_non_streaming_qwen3(monkeypatch):
    # DashScope 约束：enable_thinking=true 仅流式支持。
    # 同步 chat() 即使显式传 thinking=True 也必须强制关闭，避免非流式路径直接 400。
    captured = {}
    provider = OpenAICompatibleProvider()
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())

    def fake_post(url, payload, api_key):
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(provider, "_post_json", fake_post)

    provider.chat([{"role": "user", "content": "深度分析"}], thinking=True)

    assert captured["payload"]["enable_thinking"] is False


def test_chat_stream_disables_qwen3_thinking_when_not_requested(monkeypatch):
    # thinking=False 时对 Qwen3 显式下发 enable_thinking=false（而非不下发）：
    # 混合推理模型默认开思考，不显式关闭会让简单问题也长时间无首字输出。
    captured = {}
    provider = OpenAICompatibleProvider()
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings())

    def fake_stream(url, payload, api_key):
        captured["payload"] = payload
        yield {"type": "content", "text": "ok"}

    monkeypatch.setattr(provider, "_post_json_stream", fake_stream)

    list(provider.chat_stream([{"role": "user", "content": "hi"}], model="qwen3-8b", thinking=False))

    assert captured["payload"]["enable_thinking"] is False


def test_enable_thinking_is_not_sent_to_unknown_provider(monkeypatch):
    captured = {}
    provider = OpenAICompatibleProvider()
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: _settings(openai_api_base="https://provider.example/v1", openai_model="plain-model"),
    )

    def fake_post(url, payload, api_key):
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(provider, "_post_json", fake_post)

    provider.chat([{"role": "user", "content": "hello"}])

    assert "enable_thinking" not in captured["payload"]
    assert captured["payload"]["max_tokens"] == 8192


def test_enable_thinking_not_sent_to_non_qwen3_stream_even_when_thinking(monkeypatch):
    # 非 Qwen3 模型即使开启 thinking 也不下发该扩展参数（deepseek-reasoner 等自带思考流）
    captured = {}
    provider = OpenAICompatibleProvider()
    monkeypatch.setattr(
        llm_module,
        "get_settings",
        lambda: _settings(openai_api_base="https://provider.example/v1", openai_model="plain-model"),
    )

    def fake_stream(url, payload, api_key):
        captured["payload"] = payload
        yield {"type": "content", "text": "ok"}

    monkeypatch.setattr(provider, "_post_json_stream", fake_stream)

    list(provider.chat_stream([{"role": "user", "content": "hi"}], model="deepseek-reasoner", thinking=True))

    assert "enable_thinking" not in captured["payload"]


# ── provider 层：_stream_delta 拆帧（取代旧的 skips_reasoning 语义） ──


def test_stream_delta_splits_reasoning_and_content():
    provider = OpenAICompatibleProvider()

    # reasoning_content 不再被丢弃，而是拆成独立 reasoning 帧——依旧绝不混入回答正文
    assert provider._stream_delta({"choices": [{"delta": {"reasoning_content": "思考"}}]}) == [
        {"type": "reasoning", "text": "思考"}
    ]
    assert provider._stream_delta({"choices": [{"delta": {"content": "正文"}}]}) == [
        {"type": "content", "text": "正文"}
    ]
    # 阶段切换的边界报文可能同时携带两个字段：reasoning 帧先行保证时序
    assert provider._stream_delta(
        {"choices": [{"delta": {"reasoning_content": "尾段思考", "content": "首字"}}]}
    ) == [
        {"type": "reasoning", "text": "尾段思考"},
        {"type": "content", "text": "首字"},
    ]


def test_stream_delta_empty_and_variant_payloads():
    provider = OpenAICompatibleProvider()

    # 空报文 / 空 delta / 空字符串字段不产帧
    assert provider._stream_delta({}) == []
    assert provider._stream_delta({"choices": [{"delta": {}}]}) == []
    assert provider._stream_delta({"choices": [{"delta": {"content": "", "reasoning_content": ""}}]}) == []
    # content 列表变体（多模态分片）仍解析为 content 帧
    assert provider._stream_delta(
        {"choices": [{"delta": {"content": [{"type": "text", "text": "分片"}]}}]}
    ) == [{"type": "content", "text": "分片"}]
    # 非 delta 的 message/text 兜底变体
    assert provider._stream_delta({"choices": [{"message": {"content": "整段"}}]}) == [
        {"type": "content", "text": "整段"}
    ]
    assert provider._stream_delta({"choices": [{"text": "裸文本"}]}) == [
        {"type": "content", "text": "裸文本"}
    ]


# ── provider 层：chat_stream 帧协议（mock 路径） ─────────────


def test_chat_stream_mock_yields_content_frames(monkeypatch):
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings(mock_llm=True))
    provider = OpenAICompatibleProvider()
    messages = [{"role": "user", "content": "你好"}]

    frames = list(provider.chat_stream(messages))

    assert frames and all(frame["type"] == "content" for frame in frames)
    expected = provider.chat(messages).content
    assert "".join(frame["text"] for frame in frames) == expected


def test_chat_stream_mock_emits_reasoning_frames_when_thinking(monkeypatch):
    monkeypatch.setattr(llm_module, "get_settings", lambda: _settings(mock_llm=True))
    provider = OpenAICompatibleProvider()
    messages = [{"role": "user", "content": "需要思考的问题"}]

    frames = list(provider.chat_stream(messages, thinking=True))
    types = [frame["type"] for frame in frames]

    # 思考帧与正文帧共存，且思考流先于正文（对齐真实推理模型的输出时序）
    assert "reasoning" in types and "content" in types
    assert max(i for i, t in enumerate(types) if t == "reasoning") < types.index("content")
    # 正文与关闭 thinking 时完全一致：思考文本绝不混入 content 通道
    expected = provider.chat(messages).content
    assert "".join(frame["text"] for frame in frames if frame["type"] == "content") == expected


# ── workflow 层：_stream_llm_node 事件分流 ───────────────────


class _StubProvider:
    """捕获调用参数并按预置帧序列回放的 provider 替身。"""

    def __init__(self, frames):
        self.frames = frames
        self.kwargs = None
        self.last_chat_mock = False

    def chat_stream(self, messages, **kwargs):
        self.kwargs = kwargs
        yield from self.frames


def _make_runner(frames):
    runner = WorkflowRunner(db=None)
    runner.provider = _StubProvider(frames)
    return runner


def _make_agent():
    return SimpleNamespace(system_prompt="你是测试智能体", model="qwen3-8b", temperature=0.3, runtime_config=None)


def _make_context(thinking_type: str):
    return {
        "input": "问题",
        "sources": [],
        "web_sources": [],
        "tool_outputs": [],
        "variables": {},
        "memory_summary": "",
        "profile_memory": "",
        "uploads": [],
        "draft": "",
        "thinking_enabled": thinking_type == "native",
        "thinking_status": {"enabled": thinking_type == "native", "type": thinking_type},
    }


def _drain(generator):
    """迭代生成器收集事件，同时取回 StopIteration 携带的节点输出。"""
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as stop:
            return events, stop.value


def test_stream_llm_node_routes_reasoning_to_thinking_events():
    frames = [
        {"type": "reasoning", "text": "先想"},
        {"type": "reasoning", "text": "一想"},
        {"type": "content", "text": "最终"},
        {"type": "content", "text": "答案"},
    ]
    runner = _make_runner(frames)
    context = _make_context("native")

    events, output = _drain(runner._stream_llm_node(_make_agent(), {"id": "llm", "type": "LLM"}, context))

    thinking_events = [e for e in events if e["event"] == "thinking_token"]
    token_events = [e for e in events if e["event"] == "token"]
    assert "".join(e["content"] for e in thinking_events) == "先想一想"
    assert "".join(e["content"] for e in token_events) == "最终答案"
    # draft（将存库并进入会话记忆）只含正文，思考文本绝不混入
    assert output["draft"] == "最终答案"
    # 原生推理模式下向 provider 下发 thinking=True
    assert runner.provider.kwargs["thinking"] is True


def test_stream_llm_node_keeps_thinking_off_for_prompt_mode():
    frames = [{"type": "content", "text": "直接回答"}]
    runner = _make_runner(frames)
    context = _make_context("prompt")
    context["thinking_enabled"] = True  # 提示词增强：开启了思考但非原生推理

    events, output = _drain(runner._stream_llm_node(_make_agent(), {"id": "llm", "type": "LLM"}, context))

    assert runner.provider.kwargs["thinking"] is False
    assert [e["event"] for e in events] == ["token"]
    assert output["draft"] == "直接回答"


def test_stream_llm_node_tolerates_legacy_str_frames():
    # 防御性兼容：provider 退化产出裸字符串时按正文 Token 处理
    runner = _make_runner(["旧协议", "文本"])
    context = _make_context("none")

    events, output = _drain(runner._stream_llm_node(_make_agent(), {"id": "llm", "type": "LLM"}, context))

    assert [e["event"] for e in events] == ["token", "token"]
    assert output["draft"] == "旧协议文本"
