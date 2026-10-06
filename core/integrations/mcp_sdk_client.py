"""Synchronous bridge to the official asynchronous MCP client SDK.

Each registered server owns one event loop and connection. The synchronous
WorkflowRunner can call it without spawning the stdio server for every tool.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import urllib.parse
from builtins import BaseExceptionGroup
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures import Future
from contextlib import AsyncExitStack

import httpx2
from mcp import Client, StdioServerParameters, stdio_client
from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.extensions.client_credentials import ClientCredentialsOAuthProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.client.sse import sse_client
from mcp.shared.auth import OAuthClientMetadata
from pydantic import AnyUrl

from core.config import get_settings
from core.integrations.mcp_oauth import DatabaseOAuthBridge, DatabaseTokenStorage, oauth_redirect_url
from core.security.api_keys import decrypt_api_key
from core.security.mcp_http import PublicMcpHTTPTransport
from core.security.outbound_http import resolve_public_https


class McpSdkError(RuntimeError):
    pass


def _connection_error(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        for nested in exc.exceptions:
            message = _connection_error(nested)
            if message != "MCP 服务连接失败，请检查地址、服务进程和网络。":
                return message
    if isinstance(exc, httpx2.HTTPStatusError):
        if exc.response.status_code == 401:
            return "MCP 服务需要认证，请配置 Bearer Token 或完成 OAuth 授权。"
        if exc.response.status_code == 403:
            return "MCP 服务拒绝访问，请检查令牌权限和服务端的访问策略。"
    if isinstance(exc, (httpx2.TimeoutException, TimeoutError)):
        return "MCP 服务连接超时，请检查服务地址和网络。"
    return "MCP 服务连接失败，请检查地址、服务进程和网络。"


class SdkMcpClient:
    def __init__(self, config: dict, *, timeout_seconds: float = 30):
        self.config = dict(config)
        self.timeout_seconds = timeout_seconds
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stack: AsyncExitStack | None = None
        self._client: Client | None = None
        self._connect_future: Future | None = None
        self._oauth_bridge = DatabaseOAuthBridge(int(config["id"])) if config.get("auth_type") == "oauth" else None

    def _start_connection(self) -> Future:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._serve_loop, daemon=True)
            self._thread.start()
        if self._connect_future is None:
            self._connect_future = asyncio.run_coroutine_threadsafe(self._connect(), self._loop)
        return self._connect_future

    def _ensure(self) -> None:
        with self._lock:
            if self._client is not None:
                return
            future = self._start_connection()
        try:
            future.result(timeout=self.timeout_seconds)
        except Exception as exc:
            future.cancel()
            self.close()
            raise McpSdkError(_connection_error(exc)) from exc

    def begin_authorization(self) -> dict:
        if self._oauth_bridge is None:
            raise ValueError("MCP server does not use OAuth")
        oauth_redirect_url()
        if urllib.parse.urlsplit(self.config.get("url") or "").hostname == "api.githubcopilot.com":
            state = json.loads(decrypt_api_key(self.config["encrypted_auth"])) if self.config.get("encrypted_auth") else {}
            client = state.get("oauth_client") or state.get("client_info") or {}
            if not client.get("client_id") or not client.get("client_secret"):
                raise McpSdkError("GitHub OAuth 需要已注册应用的 Client ID 和 Client Secret；也可改用 Bearer Token，填写 GitHub PAT。")
        with self._lock:
            future = self._start_connection()
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    url = self._oauth_bridge.wait_for_authorization_url(timeout=0.1)
                    return {"authorization_url": url, "authorized": False}
                except TimeoutError:
                    if future.done():
                        if future.exception() is None:
                            return {"authorization_url": "", "authorized": True}
                        raise McpSdkError("OAuth 授权启动失败，请检查服务地址、应用凭据和已登记的回调地址。") from future.exception()
            raise TimeoutError("OAuth discovery timed out")
        except (TimeoutError, McpSdkError) as exc:
            future.cancel()
            self.close()
            if isinstance(exc, McpSdkError):
                raise
            raise McpSdkError("OAuth 服务响应超时，请检查网络连接和服务地址。") from exc

    def _serve_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self) -> None:
        stack = AsyncExitStack()
        try:
            transport = self.config.get("transport") or "stdio"
            if transport == "stdio":
                command = self.config.get("command") or []
                if not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command):
                    raise ValueError("MCP stdio command must be a list of strings")
                encrypted = self.config.get("encrypted_env") or ""
                env = json.loads(decrypt_api_key(encrypted)) if encrypted else dict(self.config.get("env") or {})
                params = StdioServerParameters(command=command[0], args=command[1:], env=env)
                errlog = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
                client = Client(
                    stdio_client(params, errlog=errlog), mode=self.config.get("protocol_mode") or "auto",
                    read_timeout_seconds=self.timeout_seconds,
                )
            elif transport in {"streamable_http", "sse"}:
                url = str(self.config.get("url") or "")
                await asyncio.to_thread(resolve_public_https, url)
                headers = {}
                auth = None
                if self.config.get("auth_type") == "bearer":
                    secret = decrypt_api_key(self.config.get("encrypted_auth") or "")
                    headers["Authorization"] = f"Bearer {secret}"
                elif self.config.get("auth_type") == "oauth":
                    redirect_url = oauth_redirect_url()
                    auth = OAuthClientProvider(
                        server_url=url,
                        client_metadata=OAuthClientMetadata(
                            client_name="Lingshu Agent", redirect_uris=[AnyUrl(redirect_url)],
                            scope=self.config.get("oauth_scope") or "",
                        ),
                        storage=DatabaseTokenStorage(int(self.config["id"])),
                        redirect_handler=self._oauth_bridge.redirect_handler,
                        callback_handler=self._oauth_bridge.callback_handler,
                        client_metadata_url=get_settings().mcp_client_metadata_url or None,
                    )
                elif self.config.get("auth_type") == "client_credentials":
                    state = json.loads(decrypt_api_key(self.config.get("encrypted_auth") or ""))
                    credentials = state["credentials"]
                    auth = ClientCredentialsOAuthProvider(
                        server_url=url,
                        storage=DatabaseTokenStorage(int(self.config["id"])),
                        client_id=credentials["client_id"],
                        client_secret=credentials["client_secret"],
                        token_endpoint_auth_method=credentials.get("method") or "client_secret_basic",
                        scope=credentials.get("scope") or None,
                    )
                if transport == "sse":
                    def http_factory(**kwargs):
                        return httpx2.AsyncClient(transport=PublicMcpHTTPTransport(), follow_redirects=False, trust_env=False, **kwargs)

                    connection = sse_client(url, headers=headers, auth=auth, timeout=self.timeout_seconds,
                                            httpx_client_factory=http_factory)
                else:
                    http_client = await stack.enter_async_context(httpx2.AsyncClient(
                        transport=PublicMcpHTTPTransport(), headers=headers, auth=auth,
                        timeout=httpx2.Timeout(self.timeout_seconds, read=300),
                        follow_redirects=False, trust_env=False,
                    ))
                    connection = streamable_http_client(url, http_client=http_client)
                client = Client(
                    connection,
                    mode=self.config.get("protocol_mode") or "auto",
                    read_timeout_seconds=self.timeout_seconds,
                )
            else:
                raise ValueError("Unsupported MCP transport")
            self._client = await stack.enter_async_context(client)
            self._stack = stack
        except BaseException:
            await stack.aclose()
            raise

    def _invoke(self, factory):
        self._ensure()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(factory(), self._loop)
        try:
            return future.result(timeout=self.timeout_seconds + 5)
        except FutureTimeout as exc:
            future.cancel()
            self.close()
            raise McpSdkError("MCP service timed out") from exc
        except Exception as exc:
            raise McpSdkError("MCP service request failed") from exc

    async def _list(self, kind: str, *, refresh: bool = False) -> list[dict]:
        assert self._client is not None
        method = getattr(self._client, f"list_{kind}")
        items: list[dict] = []
        cursor = None
        for _ in range(20):
            options = {"cache_mode": "refresh"} if refresh and kind == "tools" else {}
            result = await asyncio.wait_for(
                method(cursor=cursor, **options), timeout=self.timeout_seconds if kind == "tools" else 8
            )
            items.extend(item.model_dump(mode="json", by_alias=True) for item in getattr(result, kind))
            cursor = result.next_cursor
            if not cursor or len(items) >= 500:
                break
        return items[:500]

    def list_tools(self) -> list[dict]:
        return self._invoke(lambda: self._list("tools"))

    def refresh_tools(self) -> list[dict]:
        return self._invoke(lambda: self._list("tools", refresh=True))

    def list_resources(self) -> list[dict]:
        return self._invoke(lambda: self._list("resources"))

    def list_prompts(self) -> list[dict]:
        return self._invoke(lambda: self._list("prompts"))

    def call_tool_result(self, name: str, arguments: dict | None = None) -> dict:
        async def call():
            assert self._client is not None
            result = await self._client.call_tool(name, arguments or {}, read_timeout_seconds=self.timeout_seconds)
            return result.model_dump(mode="json", by_alias=True)

        return self._invoke(call)

    def read_resource(self, uri: str) -> dict:
        async def read():
            assert self._client is not None
            result = await self._client.read_resource(uri)
            return result.model_dump(mode="json", by_alias=True)

        return self._invoke(read)

    def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> dict:
        async def read():
            assert self._client is not None
            result = await self._client.get_prompt(name, arguments or {})
            return result.model_dump(mode="json", by_alias=True)

        return self._invoke(read)

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        result = self.call_tool_result(name, arguments)
        texts = [part.get("text", "") for part in result.get("content") or [] if part.get("type") == "text"]
        text = "\n".join(item for item in texts if item)
        if result.get("isError"):
            raise McpSdkError(text or "MCP tool failed")
        return text

    def close(self) -> None:
        with self._lock:
            loop = self._loop
            if loop is None:
                return
            if self._connect_future is not None and not self._connect_future.done():
                self._connect_future.cancel()
            if self._stack is not None:
                try:
                    asyncio.run_coroutine_threadsafe(self._stack.aclose(), loop).result(timeout=5)
                except Exception:
                    pass
            loop.call_soon_threadsafe(loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=2)
            loop.close() if not loop.is_running() else None
            self._client = None
            self._stack = None
            self._thread = None
            self._loop = None
            self._connect_future = None
