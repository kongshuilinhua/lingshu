"""模型列表自动拉取测试：请求地址、响应和失败降级。"""

import io
import json
import pytest

from core.services.user_models import probe_models_payload


class _FakeResp(io.BytesIO):
    status = 200

    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_probe_models_payload_lists_sorted_ids(monkeypatch):
    import core.services.user_models as um

    monkeypatch.setattr(
        um,
        "open_public_https",
        lambda url, **kw: _FakeResp({"data": [{"id": "qwen-plus"}, {"id": "deepseek-chat"}, {"id": None}]}),
    )
    r = probe_models_payload({"base_url": "https://x.com/v1", "api_key": "sk-x"})
    assert r["ok"] is True
    assert r["models"] == ["deepseek-chat", "qwen-plus"]
    assert r["count"] == 2


def test_probe_models_payload_builds_url_and_auth_header(monkeypatch):
    captured: dict = {}
    import core.services.user_models as um

    def fake_get(request, **kw):
        captured["url"] = request.full_url
        captured["headers"] = request.headers
        return _FakeResp({"data": []})

    monkeypatch.setattr(um, "open_public_https", fake_get)
    probe_models_payload({"base_url": "https://x.com/v1/", "api_key": "sk-secret"})
    # 尾斜杠被 rstrip,再拼 /models
    assert captured["url"] == "https://x.com/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer sk-secret"


def test_probe_models_payload_handles_missing_data(monkeypatch):
    import core.services.user_models as um

    monkeypatch.setattr(um, "open_public_https", lambda url, **kw: _FakeResp({}))
    r = probe_models_payload({"base_url": "https://x.com/v1", "api_key": "sk-x"})
    assert r["ok"] is True
    assert r["models"] == []
    assert r["count"] == 0


def test_probe_models_payload_handles_failure(monkeypatch):
    import core.services.user_models as um

    def boom(url, **kw):
        raise RuntimeError("conn refused")

    monkeypatch.setattr(um, "open_public_https", boom)
    r = probe_models_payload({"base_url": "https://x.com/v1", "api_key": "sk-x"})
    assert r["ok"] is False
    assert r["models"] == []
    assert r["count"] == 0


def test_probe_models_payload_requires_base_url():
    with pytest.raises(ValueError):
        probe_models_payload({"base_url": "", "api_key": "sk-x"})


@pytest.mark.parametrize("base_url", ["http://example.com/v1", "https://127.0.0.1/v1"])
def test_probe_models_payload_rejects_private_or_plaintext_target(base_url):
    with pytest.raises(ValueError, match="Public HTTPS"):
        probe_models_payload({"base_url": base_url, "api_key": "test-only-key"})
