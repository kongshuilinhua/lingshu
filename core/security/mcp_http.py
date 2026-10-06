"""Public HTTPS transport for user-registered remote MCP services.

httpcore2 keeps the original hostname for TLS SNI while the network backend
connects to an IP that was checked immediately before opening the socket.
"""

from __future__ import annotations

import asyncio

import httpx2
from httpcore2._backends.auto import AutoBackend

from core.security.outbound_http import resolve_public_https


class _PublicNetworkBackend(AutoBackend):
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        target = f"[{host}]" if ":" in host else host
        _, ip = await asyncio.to_thread(resolve_public_https, f"https://{target}:{port}")
        return await super().connect_tcp(
            ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise ValueError("Unix sockets are not allowed for remote MCP services")


class PublicMcpHTTPTransport(httpx2.AsyncHTTPTransport):
    """Reject private targets and bypass ambient proxies for MCP and OAuth."""

    def __init__(self):
        super().__init__(trust_env=False)
        # httpx2/httpcore2 are pinned in requirements.txt; keep TLS handling in
        # the library and replace only its socket resolver/connector.
        self._pool._network_backend = _PublicNetworkBackend()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        await asyncio.to_thread(resolve_public_https, str(request.url))
        return await super().handle_async_request(request)
