"""User-controlled outbound connections must use only validated public IPs."""

import socket
import urllib.request
from types import SimpleNamespace

import httpx
import pytest

from core.security.outbound_http import (
    PublicHTTPSHttpxTransport,
    _PinnedHTTPSHandler,
    resolve_public_https,
)


def _addr(ip):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))


@pytest.mark.parametrize("url", [
    "http://example.com/v1", "https://127.0.0.1/v1", "https://localhost/v1",
    "https://user:password@example.com/v1", "https://example.com/v1#fragment",
])
def test_rejects_unsafe_model_url(url):
    with pytest.raises(ValueError):
        resolve_public_https(url)


def test_rejects_mixed_public_and_private_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        _addr("93.184.216.34"), _addr("127.0.0.1")
    ])
    with pytest.raises(ValueError, match="blocked"):
        resolve_public_https("https://example.com/v1")


def test_pinned_connection_keeps_tls_hostname_without_global_socket_patch(monkeypatch):
    original_dns = socket.getaddrinfo
    calls = []
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout, source: calls.append(address) or object())
    handler = _PinnedHTTPSHandler("93.184.216.34")

    def fake_do_open(factory, request, **kwargs):
        connection = factory("example.com", timeout=5, **kwargs)
        connection._create_connection(("example.com", 443), 5, None)
        assert connection.host == "example.com"
        return object()

    monkeypatch.setattr(handler, "do_open", fake_do_open)
    handler.https_open(urllib.request.Request("https://example.com/v1"))
    assert calls == [("93.184.216.34", 443)]
    assert socket.getaddrinfo is original_dns


def test_httpx_transport_rejects_redirect_before_client_can_follow(monkeypatch):
    import core.security.outbound_http as outbound

    class Redirect:
        status = 302
        headers = {"Location": "https://other.example/models"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    seen = []
    monkeypatch.setattr(outbound, "open_public_https", lambda request, **kwargs: seen.append(request.full_url) or Redirect())
    with httpx.Client(transport=PublicHTTPSHttpxTransport(), follow_redirects=True) as client:
        with pytest.raises(ValueError, match="redirect"):
            client.get("https://example.com/models")
    assert seen == ["https://example.com/models"]


def test_langchain_user_model_uses_pinned_transport(monkeypatch):
    import core.config
    import langchain_openai
    from core.integrations.langchain_provider import get_chat_model

    monkeypatch.setattr(core.config, "get_settings", lambda: SimpleNamespace(mock_llm=False, openai_model="model"))
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", lambda **kwargs: kwargs)
    model = get_chat_model(runtime_config={
        "untrusted_base_url": True,
        "base_url": "https://example.com/v1",
        "api_key": "test-only-key",
        "chat_model": "model",
    })
    try:
        assert isinstance(model["http_client"]._transport, PublicHTTPSHttpxTransport)
    finally:
        model["http_client"].close()


def test_native_provider_routes_user_model_through_public_transport(monkeypatch):
    import core.integrations.llm as llm_module

    monkeypatch.setattr(llm_module, "get_settings", lambda: SimpleNamespace(
        mock_llm=False, openai_model="model", llm_max_tokens=100,
        circuit_breaker_distributed=False,
    ))
    provider = llm_module.OpenAICompatibleProvider()
    seen = []

    def fake_post(url, payload, key, **kwargs):
        seen.append(kwargs.get("public_only"))
        return {"choices": [{"message": {"content": "ok"}}]}

    def fake_stream(url, payload, key, **kwargs):
        seen.append(kwargs.get("public_only"))
        yield {"type": "content", "text": "ok"}

    monkeypatch.setattr(provider, "_post_json", fake_post)
    monkeypatch.setattr(provider, "_post_json_stream", fake_stream)
    runtime = {
        "untrusted_base_url": True, "base_url": "https://example.com/v1",
        "api_key": "test-only-key", "chat_model": "model",
    }
    provider.chat([{"role": "user", "content": "hi"}], runtime_config=runtime)
    list(provider.chat_stream([{"role": "user", "content": "hi"}], runtime_config=runtime))
    assert seen == [True, True]
