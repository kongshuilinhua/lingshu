"""JWT 验签加固与登出撤销出口的测试。"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from core.config import get_settings
from core.security import auth as auth_module
from core.security.auth import (
    _b64url_decode,
    _b64url_encode,
    create_access_token,
    decode_access_token,
)


def _forge(header: dict, payload: dict, secret: str | None = None) -> str:
    """按平台自研 JWT 的格式手工拼一个令牌，可自由指定 header。"""
    settings = get_settings()
    h = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    key = (secret if secret is not None else settings.jwt_secret).encode()
    sig = hmac.new(key, f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64url_encode(sig)}"


def _valid_payload() -> dict:
    now = int(time.time())
    return {
        "sub": "1",
        "iss": "lingshu-agent",
        "exp": now + 3600,
        "nbf": now,
        "jti": "test-jti-hardening",
    }


def test_valid_token_still_decodes():
    """加固不能破坏正常签发的令牌。"""
    token = create_access_token({"sub": "1", "workspace_id": 1, "role": "admin"})
    payload = decode_access_token(token)
    assert payload["sub"] == "1"


def test_alg_none_is_rejected():
    """alg:none 必须被白名单拦下。"""
    token = _forge({"alg": "none", "typ": "JWT"}, _valid_payload())
    with pytest.raises(ValueError, match="algorithm"):
        decode_access_token(token)


def test_unexpected_alg_is_rejected():
    """声明 RS256 但实际用 HMAC 签的令牌（algorithm confusion）必须拒绝。"""
    token = _forge({"alg": "RS256", "typ": "JWT"}, _valid_payload())
    with pytest.raises(ValueError, match="algorithm"):
        decode_access_token(token)


def test_malformed_header_is_rejected():
    """header 不是合法 JSON 时应报明确错误，而不是崩在别处。"""
    payload = _b64url_encode(json.dumps(_valid_payload()).encode())
    bad_header = _b64url_encode(b"not-json")
    sig = hmac.new(
        get_settings().jwt_secret.encode(), f"{bad_header}.{payload}".encode(), hashlib.sha256
    ).digest()
    with pytest.raises(ValueError, match="header"):
        decode_access_token(f"{bad_header}.{payload}.{_b64url_encode(sig)}")


def test_unsupported_typ_is_rejected():
    """typ 非 JWT（例如 JWE）应拒绝。"""
    token = _forge({"alg": "HS256", "typ": "JWE"}, _valid_payload())
    with pytest.raises(ValueError, match="type"):
        decode_access_token(token)


def test_issued_token_header_is_hs256():
    """签发侧与验签侧的算法必须一致。"""
    token = create_access_token({"sub": "1", "workspace_id": 1, "role": "user"})
    header = json.loads(_b64url_decode(token.split(".")[0]))
    assert header["alg"] == "HS256"
    assert header["typ"] == "JWT"


def test_revoked_token_is_rejected(monkeypatch):
    """撤销后的令牌必须无法再通过验签（模拟 Redis 可用）。"""
    revoked: set[str] = set()

    class _FakeStore:
        def set_string(self, key, ttl, value):
            revoked.add(key)
            return True

        def exists(self, key):
            return key in revoked

        @property
        def available(self):
            return True

    import core.services.rag_cache as rag_cache

    monkeypatch.setattr(rag_cache, "redis_store", _FakeStore())
    monkeypatch.setattr(auth_module, "_token_is_active", lambda jti, exp: f"revoked:{jti}" not in revoked)

    token = create_access_token({"sub": "1", "workspace_id": 1, "role": "user"})
    assert decode_access_token(token)["sub"] == "1"

    assert auth_module.revoke_access_token(token) is True
    with pytest.raises(ValueError, match="revoked"):
        decode_access_token(token)
