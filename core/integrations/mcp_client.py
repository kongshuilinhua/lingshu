"""最小 MCP(Model Context Protocol)客户端:JSON-RPC 2.0 over stdio。

🎯 零依赖自实现:spawn MCP server 子进程,经 stdin/stdout 行分隔 JSON 通信,
   支持 initialize → tools/list → tools/call 全流程,不引入 mcp SDK 依赖。
通信约定:每条 JSON-RPC 消息占一行(以 \\n 结尾);响应按 id 匹配,notification(无 id)跳过。
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time

# MCP 协议版本(初始化握手声明,2024-11-05 是 stdio transport 稳定版本)
_PROTOCOL_VERSION = "2024-11-05"


class McpError(Exception):
    """MCP 协议或传输层错误(server 报错、连接断开、启动失败)。"""


class McpClient:
    """
    单个 MCP server 的 stdio 连接。惰性 spawn,首次调用时完成 initialize 握手。

    线程安全:用锁串行化 stdin 写入与响应读取,避免多请求交错污染 id 匹配。
    """

    def __init__(self, command: list[str], env: dict | None = None, *, timeout_seconds: float = 30):
        self._command = command
        self._env = env
        self._timeout_seconds = timeout_seconds
        self._proc: subprocess.Popen | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._responses: queue.Queue[str | None] = queue.Queue(maxsize=256)

    @property
    def started(self) -> bool:
        return self._proc is not None

    def _ensure(self) -> None:
        if self._proc is not None:
            return
        try:
            self._proc = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self._env,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise McpError(f"Cannot start MCP server: {exc}") from exc
        self._responses = queue.Queue(maxsize=256)
        threading.Thread(
            target=self._read_stdout, args=(self._proc.stdout, self._responses), daemon=True
        ).start()
        self._initialize()

    @staticmethod
    def _read_stdout(pipe, responses: queue.Queue[str | None]) -> None:
        def publish(item: str | None) -> None:
            while True:
                try:
                    responses.put_nowait(item)
                    return
                except queue.Full:
                    try:
                        responses.get_nowait()
                    except queue.Empty:
                        pass

        for line in pipe:
            publish(line)
        publish(None)

    def _send(self, payload: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise McpError("MCP server not started")
        stdin = self._proc.stdin
        done = threading.Event()
        errors: list[Exception] = []

        def write() -> None:
            try:
                stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                stdin.flush()
            except (OSError, ValueError) as exc:
                errors.append(exc)
            finally:
                done.set()

        threading.Thread(target=write, daemon=True).start()
        if not done.wait(self._timeout_seconds):
            raise McpError("MCP server request timed out")
        if errors:
            raise McpError("MCP server request failed") from errors[0]

    def _recv(self, expected_id: int) -> dict:
        if self._proc is None:
            raise McpError("MCP server not started")
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError("MCP server response timed out")
            try:
                line = self._responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise McpError("MCP server response timed out") from exc
            if line is None:
                raise McpError("MCP server closed the connection")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # 跳过非 JSON 行(stderr 误入、调试输出等)
            if msg.get("id") == expected_id:
                if "error" in msg:
                    raise McpError(f"MCP error: {msg['error']}")
                return msg.get("result") or {}
            # 其余消息(notification 或别的 id 的响应)忽略

    def _call(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            try:
                self._ensure()
                msg_id = self._next_id
                self._next_id += 1
                payload: dict = {"jsonrpc": "2.0", "id": msg_id, "method": method}
                if params is not None:
                    payload["params"] = params
                self._send(payload)
                return self._recv(msg_id)
            except (McpError, OSError):
                self.close()
                raise

    def _initialize(self) -> None:
        # 调用方(_call)已持锁,这里直接用 _send/_recv,不重入 _call(否则 threading.Lock 死锁)
        msg_id = self._next_id
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "initialize",
                "params": {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "lingshu", "version": "1.0"}},
            }
        )
        result = self._recv(msg_id)
        # 握手收尾:发 initialized 通知(无 id、无结果),告知 server 可开始工作
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        caps = (result or {}).get("capabilities") or {}
        if "tools" not in caps:
            # 不强制:有些 server 不声明能力也支持 tools/list
            pass

    def list_tools(self) -> list[dict]:
        """返回 server 暴露的工具列表:[{name, description, inputSchema}]。"""
        result = self._call("tools/list")
        return (result or {}).get("tools") or []

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        """调用工具,聚合 text 类型 content 返回纯文本(喂给 LLM 作 tool result)。"""
        result = self._call("tools/call", {"name": name, "arguments": arguments or {}})
        content = (result or {}).get("content") or []
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(t for t in texts if t)

    def close(self) -> None:
        """终止 server 子进程。幂等。"""
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
            self._proc = None


# 🧠 client 池:按 server_id 复用连接,避免每次 tool 调用都 spawn 子进程。
# 进程级 dict,web worker 间不共享(每 worker 缓存自己的);配置变更需重启或显式失效。
_CLIENTS: dict[int, object] = {}
_CLIENTS_LOCK = threading.Lock()


def get_mcp_client(server):
    """按 server id + 配置版本复用官方 SDK 连接。"""
    sid = getattr(server, "id", None)
    if sid is None:
        raise McpError("MCP server has no id")
    from core.integrations.mcp_sdk_client import SdkMcpClient

    version = getattr(server, "config_version", None) or 1
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(sid)
        if client is not None and client.config.get("config_version") == version:
            return client
        if client is not None:
            client.close()
        config = {
            "id": sid,
            "config_version": version,
            "transport": getattr(server, "transport", None) or "stdio",
            "protocol_mode": getattr(server, "protocol_mode", None) or "legacy",
            "url": getattr(server, "url", None) or "",
            "command": getattr(server, "command", None) or [],
            "env": getattr(server, "env", None) or {},
            "encrypted_env": getattr(server, "encrypted_env", None) or "",
            "auth_type": getattr(server, "auth_type", None) or "none",
            "oauth_scope": getattr(server, "oauth_scope", None) or "",
            "encrypted_auth": getattr(server, "encrypted_auth", None) or "",
        }
        client = SdkMcpClient(config)
        _CLIENTS[sid] = client
        return client


def invalidate_mcp_client(server_id: int) -> None:
    """配置变更或删除 server 时关闭并移除缓存连接。"""
    with _CLIENTS_LOCK:
        client = _CLIENTS.pop(server_id, None)
    if client is not None:
        client.close()


def close_all_mcp_clients() -> None:
    """Release stdio children and remote sessions during API shutdown."""
    with _CLIENTS_LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in clients:
        client.close()
