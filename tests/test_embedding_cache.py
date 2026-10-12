"""Embedding 缓存与 TTL 抖动单元测试（不依赖真实 Redis / 数据库）。

覆盖：
1. ttl_with_jitter 抖动范围与下限保护；
2. store 的 get_embedding / set_embedding 往返与 key 隔离；
3. embed() 第二次命中缓存、不再打外部 API；
4. 关闭缓存开关时每次都打 API（不回退误命中）；
5. embed_batch 对瞬时故障重试、对确定性故障不重试、重试耗尽后抛出；
6. _post_json 给异常打的 retryable 标记符合 5xx/408/429 分档规则。
"""

import time
from types import SimpleNamespace

import pytest

from core.config import get_settings
from core.integrations import llm as llm_module
from core.services import rag_cache


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float]] = {}

    def _live(self, key):
        item = self.store.get(key)
        if not item:
            return None
        value, expire_at = item
        if expire_at and expire_at < time.time():
            self.store.pop(key, None)
            return None
        return value

    def get(self, key):
        return self._live(key)

    def setex(self, key, ttl, value):
        self.store[key] = (value, time.time() + int(ttl))

    def exists(self, key):
        return 1 if self._live(key) is not None else 0

    def ping(self):
        return True


def _make_store(client) -> rag_cache.OptionalRedisStore:
    store = rag_cache.OptionalRedisStore.__new__(rag_cache.OptionalRedisStore)
    store.settings = get_settings()
    store._client = client
    store._error = ""
    store._runtime_error = ""
    return store


def _provider_with_fake_api(monkeypatch, embedding):
    provider = llm_module.OpenAICompatibleProvider()
    monkeypatch.setattr(provider, "_api_key", lambda *a, **k: "key")
    monkeypatch.setattr(provider, "_api_base", lambda *a, **k: "http://x")
    calls = {"n": 0}

    def fake_post(url, payload, api_key, **kwargs):
        calls["n"] += 1
        return {"data": [{"embedding": list(embedding)}]}

    monkeypatch.setattr(provider, "_post_json", fake_post)
    return provider, calls


def test_ttl_with_jitter_bounds():
    for _ in range(200):
        assert 900 <= rag_cache.ttl_with_jitter(1000, ratio=0.1) <= 1100
    assert rag_cache.ttl_with_jitter(1) == 1   # delta=0 → 返回原值
    assert rag_cache.ttl_with_jitter(0) == 1   # 下限保护，永不为 0


def test_embedding_roundtrip():
    store = _make_store(FakeRedis())
    assert store.get_embedding("m", "hello") is None
    assert store.set_embedding("m", "hello", [0.1, 0.2], 3600) is True
    assert store.get_embedding("m", "hello") == [0.1, 0.2]
    # 不同 model / 文本走不同 key，不串味
    assert store.get_embedding("m2", "hello") is None
    assert store.get_embedding("m", "world") is None


def test_embed_uses_cache(monkeypatch):
    monkeypatch.setattr(rag_cache, "redis_store", _make_store(FakeRedis()))
    monkeypatch.setattr(llm_module, "get_settings", lambda: SimpleNamespace(
        mock_llm=False, openai_embedding_model="text-embedding-v3",
        embedding_cache_enabled=True, embedding_cache_ttl_seconds=3600,
    ))
    provider, calls = _provider_with_fake_api(monkeypatch, [0.3, 0.4, 0.5])

    v1 = provider.embed("repeat me")
    v2 = provider.embed("repeat me")
    assert v1 == v2 == [0.3, 0.4, 0.5]
    assert calls["n"] == 1   # 第二次命中缓存，未再打 API


def test_embed_skips_cache_when_disabled(monkeypatch):
    monkeypatch.setattr(rag_cache, "redis_store", _make_store(FakeRedis()))
    monkeypatch.setattr(llm_module, "get_settings", lambda: SimpleNamespace(
        mock_llm=False, openai_embedding_model="m",
        embedding_cache_enabled=False, embedding_cache_ttl_seconds=3600,
    ))
    provider, calls = _provider_with_fake_api(monkeypatch, [1.0])

    provider.embed("x")
    provider.embed("x")
    assert calls["n"] == 2   # 关缓存时每次都打 API


def test_embed_batch_retries_on_transient_failure(monkeypatch):
    """embed_batch 必须对瞬时故障重试，而不是让整篇文档直接 failed。

    旧行为：批量 embedding 无重试，一次网络抖动导致 indexing→failed 终态，
    用户必须重新上传整篇文档。
    """
    provider = llm_module.OpenAICompatibleProvider()
    settings = llm_module.get_settings()
    monkeypatch.setattr(settings, "mock_llm", False, raising=False)
    monkeypatch.setattr(provider, "_api_key", lambda *a, **k: "test-key")
    monkeypatch.setattr(provider, "_api_base", lambda *a, **k: "https://example.invalid/v1")

    calls = {"n": 0}

    def flaky_post(url, payload, api_key, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            # 模拟 _post_json 对网络类异常的统一包装（带 retryable 标记）
            err = RuntimeError("Model call failed: cannot connect to model gateway")
            err.retryable = True
            raise err
        return {"data": [{"index": i, "embedding": [0.1, 0.2]} for i, _ in enumerate(payload["input"])]}

    monkeypatch.setattr(provider, "_post_json", flaky_post)
    # 关闭退避等待，让测试跑得快
    monkeypatch.setattr(llm_module, "_EMBED_RETRY_BACKOFF_SECONDS", 0.0)

    result = provider.embed_batch(["a", "b"])
    assert len(result) == 2
    assert calls["n"] == 3


def test_embed_batch_does_not_retry_on_client_error(monkeypatch):
    """4xx 属确定性失败，重试无意义，必须立即抛出（只调用一次）。"""
    provider = llm_module.OpenAICompatibleProvider()
    settings = llm_module.get_settings()
    monkeypatch.setattr(settings, "mock_llm", False, raising=False)
    monkeypatch.setattr(provider, "_api_key", lambda *a, **k: "test-key")
    monkeypatch.setattr(provider, "_api_base", lambda *a, **k: "https://example.invalid/v1")
    monkeypatch.setattr(llm_module, "_EMBED_RETRY_BACKOFF_SECONDS", 0.0)

    calls = {"n": 0}

    def bad_request(url, payload, api_key, **kwargs):
        calls["n"] += 1
        # 模拟 _post_json 对 4xx 的包装（retryable=False）
        err = RuntimeError("Model call failed: HTTP 400. Check endpoint, API key and model name.")
        err.retryable = False
        raise err

    monkeypatch.setattr(provider, "_post_json", bad_request)

    with pytest.raises(RuntimeError):
        provider.embed_batch(["a"])
    assert calls["n"] == 1


def test_embed_batch_raises_after_exhausting_retries(monkeypatch):
    """可重试但三次全失败，必须抛出而不是静默返回空结果。"""
    provider = llm_module.OpenAICompatibleProvider()
    settings = llm_module.get_settings()
    monkeypatch.setattr(settings, "mock_llm", False, raising=False)
    monkeypatch.setattr(provider, "_api_key", lambda *a, **k: "test-key")
    monkeypatch.setattr(provider, "_api_base", lambda *a, **k: "https://example.invalid/v1")
    monkeypatch.setattr(llm_module, "_EMBED_RETRY_BACKOFF_SECONDS", 0.0)

    calls = {"n": 0}

    def always_flaky(url, payload, api_key, **kwargs):
        calls["n"] += 1
        err = RuntimeError("Model call failed: HTTP 429. rate limited")
        err.retryable = True
        raise err

    monkeypatch.setattr(provider, "_post_json", always_flaky)

    with pytest.raises(RuntimeError):
        provider.embed_batch(["a"])
    assert calls["n"] == llm_module._EMBED_MAX_ATTEMPTS == 3


def test_post_json_marks_retryability(monkeypatch):
    """核实 _post_json 给异常打的 retryable 标记符合分档规则。

    既有两条重试用例都 monkeypatch 掉了 _post_json、直接伪造 retryable 属性，
    因此标记逻辑本身无测试覆盖——改错判定（如把 >=500 写成 >=400）套件仍全绿。
    本用例驱动真实 _post_json，用 stub 的 urlopen 抛真实 HTTPError。

    📌 stub 目标：`_post_json` 在 `public_only=False`（默认）时走
       `urllib.request.urlopen`（模块级 urllib 引用），故 patch
       `llm_module.urllib.request.urlopen` 即可命中。
    """
    import urllib.error

    provider = llm_module.OpenAICompatibleProvider()

    def raise_http(code):
        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, code, "err", {}, None)
        return fake_urlopen

    for code in (500, 503, 429, 408):
        monkeypatch.setattr(llm_module.urllib.request, "urlopen", raise_http(code))
        with pytest.raises(RuntimeError) as exc_info:
            provider._post_json("https://example.invalid/v1/embeddings", {"input": ["a"]}, "k")
        assert getattr(exc_info.value, "retryable", False) is True, f"HTTP {code} 应为可重试"

    for code in (400, 401, 404):
        monkeypatch.setattr(llm_module.urllib.request, "urlopen", raise_http(code))
        with pytest.raises(RuntimeError) as exc_info:
            provider._post_json("https://example.invalid/v1/embeddings", {"input": ["a"]}, "k")
        assert getattr(exc_info.value, "retryable", False) is False, f"HTTP {code} 应为不可重试"
