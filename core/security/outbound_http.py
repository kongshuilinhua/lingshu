"""Network transport for requests to user-controlled public HTTPS endpoints.

Resolve and validate every address before connecting, then connect to the
validated IP while retaining the original hostname for HTTP Host and TLS SNI.
No process-global socket functions are replaced.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request

import httpx


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def _passthrough(self, req, fp, code, msg, headers):
        return fp

    http_error_301 = _passthrough
    http_error_302 = _passthrough
    http_error_303 = _passthrough
    http_error_307 = _passthrough
    http_error_308 = _passthrough


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, ip: str):
        super().__init__()
        self._ip = ip

    def https_open(self, req):
        def connection(host, **kwargs):
            conn = http.client.HTTPSConnection(host, **kwargs)
            conn._create_connection = lambda address, timeout=None, source_address=None: socket.create_connection(
                (self._ip, address[1]), timeout, source_address
            )
            return conn

        return self.do_open(connection, req, context=self._context, check_hostname=self._check_hostname)


def resolve_public_https(url: str) -> tuple[str, str]:
    """Return (hostname, safe IP); reject private, mixed, or malformed targets."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Public HTTPS URL required")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "metadata", "metadata.google.internal"} or host.endswith(".localhost"):
        raise ValueError("HTTP tool target is blocked")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("Invalid HTTPS port") from exc
    if not 1 <= port <= 65535:
        raise ValueError("Invalid HTTPS port")
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("HTTPS host cannot be resolved") from exc
    if not addresses:
        raise ValueError("HTTPS host cannot be resolved")
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    if any(not ip.is_global for ip in ips):
        raise ValueError("HTTP tool target is blocked")
    return host, str(ips[0])


def open_pinned_https(request: urllib.request.Request, *, ip: str, timeout: float):
    """Open one HTTPS hop, bypassing ambient proxies and automatic redirects."""
    if request.type != "https":
        raise ValueError("Public HTTPS URL required")
    if not ipaddress.ip_address(ip).is_global:
        raise ValueError("HTTP tool target is blocked")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectHandler(), _PinnedHTTPSHandler(ip)
    )
    return opener.open(request, timeout=timeout)


def open_public_https(request: urllib.request.Request, *, timeout: float):
    """Open one validated hop; callers must decide whether to follow redirects."""
    _, ip = resolve_public_https(request.full_url)
    return open_pinned_https(request, ip=ip, timeout=timeout)


_METADATA_HOSTS = {"metadata", "metadata.google.internal"}


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, ip: str):
        super().__init__()
        self._ip = ip

    def http_open(self, req):
        def connection(host, **kwargs):
            conn = http.client.HTTPConnection(host, **kwargs)
            conn._create_connection = lambda address, timeout=None, source_address=None: socket.create_connection(
                (self._ip, address[1]), timeout, source_address
            )
            return conn

        return self.do_open(connection, req)


def resolve_model_gateway(url: str) -> tuple[str, str]:
    """Resolve a user model gateway when private networks are explicitly allowed.

    Accepts http/https and private, loopback or proxy fake-ip addresses (self-hosted
    gateways), but still rejects cloud metadata endpoints: link-local addresses and the
    well-known metadata hostnames. Userinfo and fragments are rejected as before.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Model gateway URL must be http(s) without credentials")
    host = parsed.hostname.rstrip(".").lower()
    if host in _METADATA_HOSTS:
        raise ValueError("Model gateway target is blocked")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Invalid model gateway port") from exc
    if not 1 <= port <= 65535:
        raise ValueError("Invalid model gateway port")
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Model gateway host cannot be resolved") from exc
    if not addresses:
        raise ValueError("Model gateway host cannot be resolved")
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    if any(_is_metadata_ip(ip) for ip in ips):
        raise ValueError("Model gateway target is blocked")
    return host, str(ips[0])


def _is_metadata_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_link_local or ip.is_multicast or ip.is_unspecified


def open_model_gateway(request: urllib.request.Request, *, timeout: float, allow_private: bool):
    """Open one hop to a user model gateway.

    With ``allow_private`` False this is exactly ``open_public_https``. With it True,
    private/self-hosted gateways are reachable over http or https; redirects are still
    not followed and ambient proxies are still bypassed.
    """
    if not allow_private:
        return open_public_https(request, timeout=timeout)
    _, ip = resolve_model_gateway(request.full_url)
    handler = _PinnedHTTPSHandler(ip) if request.type == "https" else _PinnedHTTPHandler(ip)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler(), handler)
    return opener.open(request, timeout=timeout)


class PublicHTTPSHttpxTransport(httpx.BaseTransport):
    """Pin user model calls made through LangChain's synchronous HTTPX client."""

    def __init__(self, *, allow_private: bool = False):
        self._allow_private = allow_private

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        timeout_settings = request.extensions.get("timeout") or {}
        timeout = min(float(timeout_settings.get("read") or 60), 120)
        outbound = urllib.request.Request(
            str(request.url),
            data=request.read() or None,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with open_model_gateway(outbound, timeout=timeout, allow_private=self._allow_private) as response:
                if 300 <= response.status < 400:
                    raise ValueError("Model endpoint cross-origin redirect is blocked")
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError("Model response is too large")
                return httpx.Response(response.status, headers=dict(response.headers), content=raw, request=request)
        except urllib.error.HTTPError as exc:
            return httpx.Response(
                exc.code, headers=dict(exc.headers), content=exc.read(1024 * 1024), request=request
            )
