"""Langfuse 链路追踪：软依赖开关、脱敏、trace 层级、流式用量、工作流端到端埋点。

不连真实 Langfuse：通过 tracing.reset_for_tests(span_exporter=InMemorySpanExporter()) 把 SDK 产生的
OTel span 截在内存里，断言真实的父子关系与属性。
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from core.integrations import llm
from core.observability import tracing


def _settings(**overrides):
    base = dict(langfuse_public_key=f"pk-lf-test-{uuid.uuid4().hex[:8]}", langfuse_secret_key="sk-lf-test",
                langfuse_base_url="http://127.0.0.1:9", langfuse_environment="test", langfuse_sample_rate=1.0,
                mock_llm=False, openai_model="m", openai_embedding_model="emb", llm_max_tokens=0,
                circuit_breaker_distributed=False)
    return SimpleNamespace(**{**base, **overrides})


@pytest.fixture
def exporter(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(tracing, "_settings", lambda: settings)
    memory = InMemorySpanExporter()
    tracing.reset_for_tests(span_exporter=memory)
    yield memory
    tracing.reset_for_tests()


def _spans(memory):
    tracing.flush()
    return {span.name: span for span in memory.get_finished_spans()}


def _attr(span, key):
    value = span.attributes.get(key)
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


# ── 开关 ───────────────────


@pytest.mark.parametrize("overrides", [{"langfuse_public_key": None}, {"langfuse_base_url": ""}, {"mock_llm": True}])
def test_disabled_without_config_or_in_mock_mode(monkeypatch, overrides):
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(**overrides))
    tracing.reset_for_tests()
    try:
        assert tracing.enabled() is False
        with tracing.trace("t") as root, tracing.generation("g", model="m") as generation:
            assert root is tracing.NOOP and generation is tracing.NOOP
            generation.update(output="x")
        tracing.score(trace_id="abc", name="n", value=1)  # 不抛
    finally:
        tracing.reset_for_tests()


def test_observations_outside_trace_are_not_recorded(exporter):
    with tracing.generation("orphan", model="m") as generation:
        assert generation is tracing.NOOP
    assert _spans(exporter) == {}


def test_noop_mirrors_observation_interface():
    public = {name for name in dir(tracing.Observation) if not name.startswith("_")}
    assert public <= {name for name in dir(tracing.NOOP) if not name.startswith("_")}


# ── 脱敏 ───────────────────


def test_mask_redacts_secrets_images_and_long_text():
    data = {
        "api_key": "sk-real-secret-value",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 500}},
            {"type": "text", "text": "Authorization: Bearer abcdefghijklmnop"},
        ]}],
        "long": "字" * 9000,
    }
    masked = tracing._mask(data=data)
    assert masked["api_key"] == "***"
    image = masked["messages"][0]["content"][0]["image_url"]["url"]
    assert "base64 omitted" in image and "AAAA" not in image
    assert "abcdefghijklmnop" not in masked["messages"][0]["content"][1]["text"]
    assert len(masked["long"]) < 8100 and "truncated" in masked["long"]


# ── 层级与属性 ───────────────────


def test_trace_span_generation_nesting_and_trace_attributes(exporter):
    trace_id = tracing.trace_id_for("lingshu-message-42")
    assert trace_id == tracing.trace_id_for("lingshu-message-42")  # 确定性派生，打分时能回挂
    with tracing.trace("agent:demo", trace_id=trace_id, user_id=7, session_id=99, tags=["agent:1", "draft"], input="你好"):
        with tracing.span("node:LLM", as_type="chain"):
            with tracing.generation("chat", model="deepseek", input=[{"role": "user", "content": "你好"}]) as generation:
                generation.mark_first_token()
                generation.update(output="hi", usage_details={"input": 3, "output": 1, "total": 4})
    spans = _spans(exporter)
    root, node, gen = spans["agent:demo"], spans["node:LLM"], spans["chat"]
    assert format(root.context.trace_id, "032x") == trace_id
    assert node.parent.span_id == root.context.span_id and gen.parent.span_id == node.context.span_id
    for span in (root, node, gen):
        assert span.attributes.get("session.id") == "99" and span.attributes.get("user.id") == "7"
    assert _attr(gen, "langfuse.observation.usage_details") == {"input": 3, "output": 1, "total": 4}
    assert gen.attributes.get("langfuse.observation.model.name") == "deepseek"
    assert "langfuse.observation.completion_start_time" in gen.attributes


def test_exceptions_mark_error_and_propagate(exporter):
    with pytest.raises(RuntimeError):
        with tracing.trace("t"):
            with tracing.span("boom"):
                raise RuntimeError("bad gateway")
    span = _spans(exporter)["boom"]
    assert span.attributes.get("langfuse.observation.level") == "ERROR"
    assert "bad gateway" in span.attributes.get("langfuse.observation.status_message")


def test_bind_context_keeps_nesting_across_thread_hops(exporter):
    """模拟 Starlette：每次 next() 在不同线程、各自 copy_context()。"""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    def generator():
        with tracing.trace("root"):
            yield 1
            with tracing.span("child"):
                yield 2

    bound = tracing.bind_context(generator())
    with ThreadPoolExecutor(max_workers=2) as pool:
        while True:
            try:
                pool.submit(contextvars.copy_context().run, next, bound).result()
            except StopIteration:
                break
    spans = _spans(exporter)
    assert spans["child"].parent.span_id == spans["root"].context.span_id


# ── 模型调用层 ───────────────────


class _Response:
    status = 200

    def __init__(self, chunks):
        self.lines = [f"data: {json.dumps(chunk)}\n".encode() for chunk in chunks] + [b"data: [DONE]\n"]

    def __iter__(self):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _provider(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    provider = llm.OpenAICompatibleProvider()
    monkeypatch.setattr(provider, "_api_key", lambda *a, **k: "k")
    monkeypatch.setattr(provider, "_api_base", lambda *a, **k: "https://gw.example/v1")
    monkeypatch.setattr(provider, "_supports_enable_thinking", lambda *a: False)
    return provider


def test_chat_stream_requests_usage_and_records_generation(exporter, monkeypatch):
    provider = _provider(monkeypatch)
    payloads = []

    def urlopen(request, timeout):
        payloads.append(json.loads(request.data))
        return _Response([{"choices": [{"delta": {"content": "答"}}]},
                          {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}}])

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    with tracing.trace("t"):
        frames = list(provider.chat_stream([{"role": "user", "content": "hi"}]))
    assert frames == [{"type": "content", "text": "答"}]  # usage 帧不外泄给调用方
    assert payloads[0]["stream_options"] == {"include_usage": True}
    gen = _spans(exporter)["chat_stream"]
    assert _attr(gen, "langfuse.observation.usage_details") == {"input": 12, "output": 3, "total": 15}
    assert _attr(gen, "langfuse.observation.output") == "答"


def test_stream_options_dropped_when_gateway_rejects(exporter, monkeypatch):
    provider = _provider(monkeypatch)
    payloads = []

    def post_stream(url, payload, api_key, **kwargs):
        payloads.append(dict(payload))
        if "stream_options" in payload:
            raise RuntimeError("Model call failed: HTTP 400. unknown field stream_options")
        yield {"type": "content", "text": "ok"}

    monkeypatch.setattr(provider, "_post_json_stream", post_stream)
    with tracing.trace("t"):
        assert list(provider.chat_stream([{"role": "user", "content": "hi"}])) == [{"type": "content", "text": "ok"}]
    assert "stream_options" in payloads[0] and "stream_options" not in payloads[1]


def test_stream_options_not_sent_when_tracing_disabled(monkeypatch):
    provider = _provider(monkeypatch)
    tracing.reset_for_tests()
    monkeypatch.setattr(tracing, "_settings", lambda: _settings(langfuse_public_key=None))
    payloads = []

    def urlopen(request, timeout):
        payloads.append(json.loads(request.data))
        return _Response([{"choices": [{"delta": {"content": "答"}}]}])

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    list(provider.chat_stream([{"role": "user", "content": "hi"}]))
    assert "stream_options" not in payloads[0]
    tracing.reset_for_tests()


def test_non_stream_chat_records_usage(exporter, monkeypatch):
    provider = _provider(monkeypatch)
    monkeypatch.setattr(provider, "_post_json", lambda *a, **k: {
        "choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    with tracing.trace("t"):
        response = provider.chat([{"role": "user", "content": "hi"}])
    assert response.usage == {"input": 5, "output": 2, "total": 7}
    assert _attr(_spans(exporter)["chat"], "langfuse.observation.usage_details") == {"input": 5, "output": 2, "total": 7}


def test_anthropic_stream_yields_usage_frame():
    from core.integrations import anthropic_protocol as protocol

    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 20, "cache_read_input_tokens": 5, "output_tokens": 1}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "message_delta", "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ]
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
    frames = list(protocol.stream_frames(iter(body.splitlines(keepends=True))))
    assert frames[-1] == {"type": "usage", "usage": {"input": 25, "output": 9, "total": 34}}


# ── 工作流端到端（真实 API + mock 模型 + 内存导出） ───────────────────


def test_chat_stream_builds_trace_tree_and_feedback_score(client, auth_headers, monkeypatch):
    settings = _settings()
    monkeypatch.setattr(tracing, "_settings", lambda: settings)
    memory = InMemorySpanExporter()
    tracing.reset_for_tests(span_exporter=memory)
    scores = []
    try:
        langfuse = tracing.client()
        monkeypatch.setattr(langfuse, "create_score", lambda **kwargs: scores.append(kwargs))
        agent_id = client.post("/api/agents", headers=auth_headers, json={"name": "Traced"}).json()["agent"]["id"]
        response = client.post(f"/api/agents/{agent_id}/chat/stream", headers=auth_headers,
                               json={"message": "你好", "mode": "draft", "rag_enabled": False})
        assert response.status_code == 200 and "event: error" not in response.text
        spans = _spans(memory)
        root = spans["agent:Traced"]
        node_names = {name for name in spans if name.startswith("node:")}
        assert {"node:Start", "node:LLM", "node:Answer"} <= node_names
        for name in node_names:
            assert spans[name].parent.span_id == root.context.span_id
        assert root.attributes.get("session.id") and root.attributes.get("user.id")

        done = next(json.loads(line[6:]) for line in response.text.splitlines()
                    if line.startswith("data: ") and '"message_id"' in line)
        feedback = client.post(f"/api/messages/{done['message_id']}/feedback", headers=auth_headers, json={"rating": "negative"})
        assert feedback.status_code == 200
        assert scores and scores[0]["trace_id"] == format(root.context.trace_id, "032x")
        assert scores[0]["name"] == "user_feedback" and scores[0]["value"] == 0
    finally:
        tracing.reset_for_tests()
