"""Official SDK bridge works with a current stdio MCP service."""

import sys
import asyncio
import json
from types import SimpleNamespace

import httpx2
import pytest
from httpcore2._backends.auto import AutoBackend

from core.integrations.mcp_sdk_client import SdkMcpClient
from core.security.mcp_http import PublicMcpHTTPTransport, _PublicNetworkBackend


SERVER_CODE = '''
from mcp.server import MCPServer

server = MCPServer("catalog-test")

@server.tool()
def echo(text: str) -> str:
    return "ECHO:" + text

server.run("stdio")
'''


def test_sdk_client_discovers_and_calls_stdio_tool():
    client = SdkMcpClient(
        {"transport": "stdio", "protocol_mode": "auto", "command": [sys.executable, "-c", SERVER_CODE]},
        timeout_seconds=10,
    )
    try:
        tools = client.list_tools()
        assert [tool["name"] for tool in tools] == ["echo"]
        result = client.call_tool_result("echo", {"text": "hi"})
        assert result["isError"] is False
        assert result["content"][0]["text"] == "ECHO:hi"
    finally:
        client.close()


def test_remote_mcp_transport_blocks_loopback_before_connecting():
    async def request():
        async with httpx2.AsyncClient(transport=PublicMcpHTTPTransport()) as client:
            await client.get("https://127.0.0.1/mcp")

    with pytest.raises(ValueError, match="blocked"):
        asyncio.run(request())


def test_remote_mcp_connector_uses_validated_ip(monkeypatch):
    import core.security.mcp_http as mcp_http

    captured = []
    monkeypatch.setattr(mcp_http, "resolve_public_https", lambda url: ("example.test", "93.184.216.34"))

    async def fake_connect(self, host, port, **kwargs):
        captured.append((host, port))
        return object()

    monkeypatch.setattr(AutoBackend, "connect_tcp", fake_connect)
    asyncio.run(_PublicNetworkBackend().connect_tcp("example.test", 443))
    assert captured == [("93.184.216.34", 443)]


def test_github_oauth_without_registered_app_fails_immediately(monkeypatch):
    from core.integrations import mcp_sdk_client
    monkeypatch.setattr(mcp_sdk_client, "oauth_redirect_url", lambda: "http://127.0.0.1:8000/api/mcp/oauth/callback")
    client = SdkMcpClient({"id": 1, "auth_type": "oauth", "url": "https://api.githubcopilot.com/mcp/"})
    monkeypatch.setattr(client, "_start_connection", lambda: pytest.fail("Must validate registration before starting network IO"))
    with pytest.raises(mcp_sdk_client.McpSdkError, match="Client ID.*PAT"):
        client.begin_authorization()


@pytest.mark.parametrize("transport", ["streamable_http", "sse"])
def test_remote_transports_complete_handshake_discovery_and_tool_call(monkeypatch, transport):
    from core.integrations import mcp_sdk_client
    from core.integrations import mcp_oauth

    class Events(httpx2.AsyncByteStream):
        def __init__(self):
            self.queue = asyncio.Queue()

        async def __aiter__(self):
            yield b'event: endpoint\ndata: /messages?session_id=test\n\n'
            while True:
                yield await self.queue.get()

    events = None
    captured_auth = []

    async def request(req):
        nonlocal events
        captured_auth.append(req.headers.get("Authorization"))
        if req.method == "GET":
            events = Events()
            return httpx2.Response(200, headers={"Content-Type": "text/event-stream"}, stream=events)
        data = json.loads(await req.aread())
        if "id" not in data:
            return httpx2.Response(202)
        method = data["method"]
        if method == "initialize":
            result = {"protocolVersion": data["params"]["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "transport-fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "ECHO:" + data["params"]["arguments"]["text"]}], "isError": False}
        else:
            pytest.fail("Unexpected RPC method: " + method)
        message = {"jsonrpc": "2.0", "id": data["id"], "result": result}
        if transport == "sse":
            await events.queue.put(('event: message\ndata: ' + json.dumps(message) + '\n\n').encode())
            return httpx2.Response(202)
        return httpx2.Response(200, json=message)

    monkeypatch.setattr(mcp_sdk_client, "resolve_public_https", lambda url: ("mcp.example.test", "93.184.216.34"))
    monkeypatch.setattr(mcp_sdk_client, "PublicMcpHTTPTransport", lambda: httpx2.MockTransport(request))
    monkeypatch.setattr(mcp_sdk_client, "decrypt_api_key", lambda value: "fixture-token")
    monkeypatch.setattr(mcp_oauth, "get_settings", lambda: SimpleNamespace(deployment_mode="development", mcp_oauth_redirect_url=""))
    client = SdkMcpClient({"id": 1, "transport": transport, "protocol_mode": "legacy", "url": "https://mcp.example.test/sse",
                           "auth_type": "bearer", "encrypted_auth": "encrypted-fixture"}, timeout_seconds=10)
    try:
        assert [tool["name"] for tool in client.list_tools()] == ["echo"]
        assert client.call_tool("echo", {"text": "hello"}) == "ECHO:hello"
        assert captured_auth and all(value == "Bearer fixture-token" for value in captured_auth)
    finally:
        client.close()
