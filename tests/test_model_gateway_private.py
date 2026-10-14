"""USER_MODEL_ALLOW_PRIVATE_NETWORK 只放开用户模型网关，云元数据与其它出站通道仍被拦截；连接测试只测连通。"""

import socket
import urllib.request
from types import SimpleNamespace

import pytest

import core.security.outbound_http as outbound
import core.services.user_models as um
from core.security.outbound_http import open_model_gateway, resolve_model_gateway


def _addr(ip, port=443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (ip, port))


@pytest.fixture
def dns(monkeypatch):
    table = {}
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [_addr(table[host])])
    return table


@pytest.mark.parametrize("url,host,ip", [
    ("http://gw.lan:11434/v1", "gw.lan", "192.168.1.10"),
    ("https://console.tars-ai.com/v1", "console.tars-ai.com", "198.19.0.234"),
    ("http://localhost:8000/v1", "localhost", "127.0.0.1"),
])
def test_resolve_model_gateway_allows_private_and_fake_ip(dns, url, host, ip):
    dns[host] = ip
    assert resolve_model_gateway(url)[1] == ip


@pytest.mark.parametrize("url,host,ip", [
    ("http://169.254.169.254/latest", "169.254.169.254", "169.254.169.254"),
    ("http://metadata.google.internal/v1", "metadata.google.internal", "169.254.169.254"),
    ("http://evil.example/v1", "evil.example", "169.254.169.254"),
    ("http://mapped.example/v1", "mapped.example", "::ffff:169.254.169.254"),
])
def test_resolve_model_gateway_still_blocks_metadata(dns, url, host, ip):
    dns[host] = ip
    with pytest.raises(ValueError, match="blocked"):
        resolve_model_gateway(url)


@pytest.mark.parametrize("url", ["https://user:pw@example.com/v1", "https://example.com/v1#x", "ftp://example.com/v1"])
def test_resolve_model_gateway_rejects_malformed(url):
    with pytest.raises(ValueError):
        resolve_model_gateway(url)


def test_open_model_gateway_without_switch_is_public_https(monkeypatch):
    seen = []
    monkeypatch.setattr(outbound, "open_public_https", lambda request, **kw: seen.append(request.full_url) or "public")
    assert open_model_gateway(urllib.request.Request("https://example.com/v1"), timeout=5, allow_private=False) == "public"
    assert seen == ["https://example.com/v1"]


def test_open_model_gateway_without_switch_hints_env_for_fake_ip(dns):
    dns["console.tars-ai.com"] = "198.19.0.234"
    with pytest.raises(ValueError, match="USER_MODEL_ALLOW_PRIVATE_NETWORK"):
        open_model_gateway(urllib.request.Request("https://console.tars-ai.com/v1/models"), timeout=5, allow_private=False)


def test_open_model_gateway_with_switch_pins_http_private_ip(monkeypatch, dns):
    dns["gw.lan"] = "192.168.1.10"
    calls = []

    def fake_connect(address, *args, **kwargs):
        calls.append(address)
        raise OSError("stop")

    monkeypatch.setattr(socket, "create_connection", fake_connect)
    with pytest.raises(OSError):
        open_model_gateway(urllib.request.Request("http://gw.lan:11434/v1/models"), timeout=5, allow_private=True)
    assert calls == [("192.168.1.10", 11434)]


@pytest.mark.parametrize("allowed,url,ok", [
    (False, "http://192.168.1.10/v1", False),
    (False, "https://10.0.0.5/v1", False),
    (True, "http://192.168.1.10/v1", True),
    (True, "https://198.19.0.234/v1", True),
    (True, "http://169.254.169.254/v1", False),
    (True, "http://metadata.google.internal/v1", False),
])
def test_registration_validation_follows_switch(monkeypatch, allowed, url, ok):
    monkeypatch.setattr(um, "_private_gateways_allowed", lambda: allowed)
    if ok:
        um._validate_public_base_url(url)
    else:
        with pytest.raises(ValueError):
            um._validate_public_base_url(url)


def test_switch_off_error_mentions_env_var(monkeypatch):
    monkeypatch.setattr(um, "_private_gateways_allowed", lambda: False)
    with pytest.raises(ValueError, match="USER_MODEL_ALLOW_PRIVATE_NETWORK"):
        um._validate_public_base_url("http://192.168.1.10/v1")


def test_switch_does_not_relax_generic_public_https_guard(dns):
    # MCP / HTTP 工具 / web reader 走 resolve_public_https，不受开关影响
    dns["gw.lan"] = "192.168.1.10"
    with pytest.raises(ValueError):
        outbound.resolve_public_https("https://gw.lan/v1")


def test_llm_post_json_passes_switch_to_gateway(monkeypatch):
    import core.integrations.llm as llm_module

    monkeypatch.setattr(llm_module, "get_settings", lambda: SimpleNamespace(user_model_allow_private_network=True))
    seen = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self, *a):
            return b'{"ok": true}'

    monkeypatch.setattr(llm_module, "open_model_gateway", lambda request, **kw: seen.append(kw["allow_private"]) or Resp())
    provider = llm_module.OpenAICompatibleProvider()
    assert provider._post_json("http://192.168.1.10/v1/chat/completions", {}, "k", public_only=True) == {"ok": True}
    assert seen == [True]


def test_connection_test_stops_after_first_stream_frame(monkeypatch):
    # 只发一个极短的流式请求，收到首帧就关闭——推理模型非流式要等完整思考，可能近 1 分钟
    calls, consumed = [], []

    def fake_stream(self, messages, **kwargs):
        calls.append((messages, kwargs))
        for text in ["p", "o", "n", "g"]:
            consumed.append(text)
            yield {"type": "reasoning", "text": text}

    monkeypatch.setattr(um.OpenAICompatibleProvider, "chat_stream", fake_stream)
    monkeypatch.setattr(um, "user_model_runtime_config", lambda config: {"chat_model": "m"})
    result = um.test_user_model_config(SimpleNamespace(chat_model="m"))
    assert result["ok"] is True
    assert len(calls) == 1
    assert isinstance(calls[0][0][0]["content"], str)
    assert calls[0][1]["runtime_config"]["max_tokens"] == um._CONNECTION_TEST_MAX_TOKENS
    assert consumed == ["p"]


def test_connection_test_failure_is_sanitized(monkeypatch):
    def fail(self, messages, **kwargs):
        raise RuntimeError("Model call failed: HTTP 401. Authorization: Bearer sk-leaked-secret")
        yield  # 生成器：错误在首次迭代时抛出，与真实 chat_stream 一致

    monkeypatch.setattr(um.OpenAICompatibleProvider, "chat_stream", fail)
    monkeypatch.setattr(um, "user_model_runtime_config", lambda config: {"chat_model": "m"})
    result = um.test_user_model_config(SimpleNamespace(chat_model="m"))
    assert result["ok"] is False
    assert result["error_code"] == "auth_failed"
    assert "sk-leaked-secret" not in result["message"]


def test_chat_retries_without_enable_thinking_when_endpoint_rejects_it(monkeypatch):
    # 硅基流动的 Qwen3-VL 不接受 enable_thinking：400 后去掉该参数重试一次
    import core.integrations.llm as llm_module

    monkeypatch.setattr(llm_module, "get_settings", lambda: SimpleNamespace(
        mock_llm=False, openai_model="m", llm_max_tokens=100, circuit_breaker_distributed=False,
        openai_api_base="https://example.com/v1", user_model_allow_private_network=False))
    provider = llm_module.OpenAICompatibleProvider()
    sent = []

    def fake_post(url, payload, key, **kwargs):
        sent.append(dict(payload))
        if "enable_thinking" in payload:
            raise RuntimeError('Model call failed: HTTP 400. {"message":"current model does not support parameter `enable_thinking`."}')
        return {"choices": [{"message": {"content": "green"}}]}

    monkeypatch.setattr(provider, "_post_json", fake_post)
    runtime = {"untrusted_base_url": True, "base_url": "https://example.com/v1", "api_key": "k", "chat_model": "Qwen/Qwen3-VL-8B-Instruct"}
    assert provider.chat([{"role": "user", "content": "hi"}], runtime_config=runtime).content == "green"
    assert ["enable_thinking" in p for p in sent] == [True, False]
