"""McpClient 测试:真实 spawn 内联 MCP server,验证 JSON-RPC 协议全流程。

不用桩:用一个最小 MCP server 脚本(经 sys.executable -c 跑)作为子进程,
McpClient 真实 spawn + initialize 握手 + tools/list + tools/call,端到端验证。
"""

import sys

import pytest

from core.integrations.mcp_client import McpClient, McpError

# 最小 MCP server:回显工具。每行一个 JSON-RPC 消息。
SERVER_CODE = r'''
import json, sys
def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"echo","version":"1.0"}}}) + "\n")
            sys.stdout.flush()
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":mid,"result":{"tools":[{"name":"echo","description":"回显输入","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}]}}) + "\n")
            sys.stdout.flush()
        elif method == "tools/call":
            params = msg.get("params",{})
            name = params.get("name")
            if name == "echo":
                text = params.get("arguments",{}).get("text","")
                sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":"ECHO: " + text}]}}) + "\n")
                sys.stdout.flush()
            else:
                sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":mid,"error":{"code":-32602,"message":"unknown tool: " + str(name)}}) + "\n")
                sys.stdout.flush()
main()
'''


def test_mcp_client_initialize_list_call():
    """真实 spawn + 握手 + 列工具 + 调工具,端到端。"""
    client = McpClient(command=[sys.executable, "-c", SERVER_CODE])
    try:
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"
        assert "text" in tools[0]["inputSchema"]["properties"]

        result = client.call_tool("echo", {"text": "hi"})
        assert result == "ECHO: hi"
    finally:
        client.close()


def test_mcp_client_error_response_raises():
    """server 返回 JSON-RPC error → McpError。"""
    client = McpClient(command=[sys.executable, "-c", SERVER_CODE])
    try:
        with pytest.raises(McpError, match="MCP error"):
            client.call_tool("nonexistent", {"x": 1})
    finally:
        client.close()


def test_mcp_client_close_terminates_subprocess():
    client = McpClient(command=[sys.executable, "-c", SERVER_CODE])
    client.list_tools()
    assert client.started
    client.close()
    assert not client.started
    # 幂等:再次 close 不报错
    client.close()


def test_mcp_client_start_failure():
    """command 不存在 → 启动即 McpError。"""
    with pytest.raises(McpError, match="Cannot start"):
        McpClient(command=["definitely-not-a-real-binary-xyz"]).list_tools()
