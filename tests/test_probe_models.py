"""模型列表自动拉取(probe-models)测试:进阶层易用性。

桩 httpx.get 验证:URL 拼接、列表解析与排序、失败降级、空 base_url 校验。
"""

import pytest

from core.services.user_models import probe_models_payload


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_probe_models_payload_lists_sorted_ids(monkeypatch):
    import core.services.user_models as um

    monkeypatch.setattr(
        um.httpx,
        "get",
        lambda url, **kw: _FakeResp({"data": [{"id": "qwen-plus"}, {"id": "deepseek-chat"}, {"id": None}]}),
    )
    r = probe_models_payload({"base_url": "https://x.com/v1", "api_key": "sk-x"})
    assert r["ok"] is True
    assert r["models"] == ["deepseek-chat", "qwen-plus"]
    assert r["count"] == 2


def test_probe_models_payload_builds_url_and_auth_header(monkeypatch):
    captured: dict = {}
    import core.services.user_models as um

    def fake_get(url, **kw):
        captured["url"] = url
        captured["headers"] = kw.get("headers")
        return _FakeResp({"data": []})

    monkeypatch.setattr(um.httpx, "get", fake_get)
    probe_models_payload({"base_url": "https://x.com/v1/", "api_key": "sk-secret"})
    # 尾斜杠被 rstrip,再拼 /models
    assert captured["url"] == "https://x.com/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer sk-secret"


def test_probe_models_payload_handles_missing_data(monkeypatch):
    import core.services.user_models as um

    monkeypatch.setattr(um.httpx, "get", lambda url, **kw: _FakeResp({}))
    r = probe_models_payload({"base_url": "https://x.com/v1", "api_key": "sk-x"})
    assert r["ok"] is True
    assert r["models"] == []
    assert r["count"] == 0


def test_probe_models_payload_handles_failure(monkeypatch):
    import core.services.user_models as um

    def boom(url, **kw):
        raise RuntimeError("conn refused")

    monkeypatch.setattr(um.httpx, "get", boom)
    r = probe_models_payload({"base_url": "https://x.com/v1", "api_key": "sk-x"})
    assert r["ok"] is False
    assert r["models"] == []
    assert r["count"] == 0


def test_probe_models_payload_requires_base_url():
    with pytest.raises(ValueError):
        probe_models_payload({"base_url": "", "api_key": "sk-x"})
