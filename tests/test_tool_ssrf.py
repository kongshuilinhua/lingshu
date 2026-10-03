"""HTTP 工具的 SSRF 防线测试：首跳校验 + 重定向逐跳校验。"""

from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from core.db.models import Tool
from core.services import tools as tools_service


def _tool(url: str = "https://example.com/api", method: str = "GET") -> Tool:
    return Tool(
        name="probe",
        type="http",
        url=url,
        method=method,
        timeout_seconds=5,
        auth_type="none",
        headers_schema={},
        query_schema={},
        body_schema={},
    )


class _FakeResponse(io.BytesIO):
    """最小化的 urllib 响应替身，支持 with 语句与 .status/.headers。"""

    def __init__(self, status: int, headers: dict, body: bytes = b"{}"):
        super().__init__(body)
        self.status = status
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch):
    """把 DNS 解析固定到一个公网 IP，避免测试依赖真实网络。"""
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize(
    "location",
    [
        # 真实攻击载荷形态：AWS IMDS 走明文 http，先被 HTTPS 关哨拦下
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        # 即使攻击者改用 https，也必须被私网/元数据 IP 关哨拦下
        "https://169.254.169.254/latest/meta-data/iam/security-credentials/",
    ],
)
def test_redirect_to_cloud_metadata_is_blocked(monkeypatch, location):
    """核心回归：302 指向云元数据地址必须被拦截，而不是跟随过去读到凭证。"""
    calls: list[str] = []

    def fake_open(self, request, timeout=None):
        calls.append(request.full_url)
        if len(calls) == 1:
            return _FakeResponse(302, {"Location": location})
        return _FakeResponse(200, {"Content-Type": "application/json"}, b'{"secret":"leaked"}')

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)

    with pytest.raises(ValueError):
        tools_service._execute_http_tool(_tool(), {"input": {}})

    # 关键断言：第二跳绝不能发出
    assert len(calls) == 1


def test_redirect_to_private_network_is_blocked(monkeypatch):
    """重定向到 RFC 1918 私网同样必须拦截。"""

    def fake_open(self, request, timeout=None):
        return _FakeResponse(301, {"Location": "https://10.0.0.5/admin"})

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)

    with pytest.raises(ValueError, match="blocked"):
        tools_service._execute_http_tool(_tool(), {"input": {}})


def test_redirect_downgrade_to_http_is_blocked(monkeypatch):
    """重定向到明文 HTTP 必须拦截（首跳强制 HTTPS，跳转也不能降级）。"""

    def fake_open(self, request, timeout=None):
        return _FakeResponse(302, {"Location": "http://example.org/x"})

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)

    with pytest.raises(ValueError, match="HTTPS"):
        tools_service._execute_http_tool(_tool(), {"input": {}})


def test_safe_redirect_is_followed(monkeypatch):
    """合法的外网跳转仍然要能正常跟随，防止修复过度收紧。"""
    seen: list[str] = []

    def fake_open(self, request, timeout=None):
        seen.append(request.full_url)
        if len(seen) == 1:
            return _FakeResponse(302, {"Location": "https://example.com/v2/api"})
        return _FakeResponse(200, {"Content-Type": "application/json"}, b'{"ok":true}')

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)

    result = tools_service._execute_http_tool(_tool(), {"input": {}})
    assert result["status_code"] == 200
    assert seen[-1] == "https://example.com/v2/api"


def test_redirect_loop_hits_hop_limit(monkeypatch):
    """无限重定向必须被跳数上限截断，而不是打满超时或递归。"""
    hops: list[str] = []

    def fake_open(self, request, timeout=None):
        hops.append(request.full_url)
        return _FakeResponse(302, {"Location": "https://example.com/loop"})

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)

    with pytest.raises(ValueError, match="redirect limit"):
        tools_service._execute_http_tool(_tool(), {"input": {}})
    assert len(hops) == tools_service.MAX_REDIRECTS + 1


def test_http_error_does_not_leak_upstream_body(monkeypatch):
    """上游 4xx/5xx 的响应体不得回传给调用方。"""

    def fake_open(self, request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 500, "Server Error", {}, io.BytesIO(b"internal db dsn: user:pw@host")
        )

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)

    with pytest.raises(ValueError) as excinfo:
        tools_service._execute_http_tool(_tool(), {"input": {}})
    assert "500" in str(excinfo.value)
    assert "dsn" not in str(excinfo.value)
