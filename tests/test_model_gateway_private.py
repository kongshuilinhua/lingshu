"""USER_MODEL_ALLOW_PRIVATE_NETWORK 只放开用户模型网关，云元数据与其它出站通道仍被拦截；推理能力真探测。"""

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


class _FakeStreamProvider:
    def __init__(self, frames=None, exc=None):
        self.frames, self.exc, self.calls = frames or [], exc, []

    def chat_stream(self, messages, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        yield from self.frames


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setattr(um, "get_settings", lambda: SimpleNamespace(mock_llm=False))


def test_reasoning_native_when_reasoning_frame(real_mode):
    provider = _FakeStreamProvider([{"type": "reasoning", "text": "let me think"}, {"type": "content", "text": "5"}])
    result = um._check_reasoning_capability(provider, {"chat_model": "deepseek-reasoner"})
    assert result["type"] == "native"
    assert provider.calls[0]["thinking"] is True
    assert provider.calls[0]["runtime_config"]["max_tokens"] == um._REASONING_PROBE_MAX_TOKENS


def test_reasoning_prompt_when_no_reasoning_frame(real_mode):
    provider = _FakeStreamProvider([{"type": "content", "text": "5"}])
    assert um._check_reasoning_capability(provider, {"chat_model": "deepseek-chat"})["type"] == "prompt"


def test_reasoning_prompt_with_error_when_request_fails(real_mode):
    provider = _FakeStreamProvider(exc=RuntimeError("HTTP 400 thinking not supported"))
    result = um._check_reasoning_capability(provider, {"chat_model": "m"})
    assert result["type"] == "prompt"
    assert result["error_code"] == "chat_payload_rejected"


@pytest.mark.parametrize("name,expected", [
    ("deepseek-reasoner", "native"), ("deepseek-ai/DeepSeek-R1", "native"), ("qwen3-8b", "native"),
    ("claude-thinking", "native"), ("deepseek-chat", "prompt"), ("gpt-4o", "prompt"),
])
def test_reasoning_mock_uses_model_name(monkeypatch, name, expected):
    monkeypatch.setattr(um, "get_settings", lambda: SimpleNamespace(mock_llm=True))
    assert um._check_reasoning_capability(_FakeStreamProvider(), {"chat_model": name})["type"] == expected


def test_detected_capabilities_reflect_reasoning_probe(monkeypatch):
    monkeypatch.setattr(um, "_check_reasoning_capability", lambda provider, runtime: {"type": "native", "error_code": "", "message": ""})
    monkeypatch.setattr(um.OpenAICompatibleProvider, "chat", lambda self, *a, **k: "ok")
    monkeypatch.setattr(um, "user_model_runtime_config", lambda config: {"chat_model": "m"})
    config = SimpleNamespace(chat_model="m", supports_image=False, supports_reasoning=False, reasoning_type="none")
    result = um.test_user_model_config(config, detect_reasoning=True)
    assert result["detected_capabilities"]["supports_reasoning"] is True
    assert result["detected_capabilities"]["reasoning_type"] == "native"
