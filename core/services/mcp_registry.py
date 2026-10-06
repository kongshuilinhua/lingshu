"""Workspace MCP catalog and per-agent tool selections."""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.parse
from datetime import datetime, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from core.config import get_settings
from core.db.models import Agent, AgentMcpBinding, AgentVersion, McpServer, WorkspaceMember
from core.integrations.mcp_client import get_mcp_client, invalidate_mcp_client
from core.security.api_keys import decrypt_api_key, encrypt_api_key
from core.security.permissions import can_manage
from core.security.outbound_http import resolve_public_https

MAX_SERVERS_PER_WORKSPACE = 50
MAX_MCP_TOOLS_PER_AGENT = 400


def stdio_templates() -> dict:
    """Commands approved by the deployment operator."""
    try:
        templates = json.loads(get_settings().mcp_stdio_templates_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("MCP stdio templates configuration is invalid") from exc
    return templates if isinstance(templates, dict) else {}


def _template_command(template_id: str, env: dict) -> tuple[list[str], dict]:
    template = stdio_templates().get(template_id)
    if not isinstance(template, dict):
        raise ValueError("MCP stdio template is not available")
    command = template.get("command")
    allowed_env = set(template.get("env_keys") or [])
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("MCP stdio template command is invalid")
    if not isinstance(env, dict) or any(key not in allowed_env or not isinstance(value, str) for key, value in env.items()):
        raise ValueError("MCP stdio environment contains an unknown key")
    return command, env


def custom_stdio_enabled() -> bool:
    return bool(getattr(get_settings(), "mcp_allow_custom_stdio", False))


def _validate_stdio_command(command: list[str], env: dict) -> None:
    if (not command or len(command) > 65 or not all(isinstance(arg, str) and '\x00' not in arg and len(arg) <= 4096 for arg in command)
            or not command[0].strip() or any(char in command[0] for char in '\r\n')):
        raise ValueError("stdio 启动命令或参数不正确；请分别填写可执行程序和参数数组。")
    if not isinstance(env, dict) or len(env) > 128 or any(
        not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or not isinstance(value, str) or '\x00' in value
        for key, value in env.items()
    ):
        raise ValueError("stdio 环境变量名称或值不正确。")
    if len(json.dumps([command, env], ensure_ascii=False).encode()) > 65536:
        raise ValueError("stdio 启动配置不能超过 64 KB。")


def _stdio_launch(payload: dict, current: McpServer | None = None) -> tuple[list[str], dict]:
    template_id = str(payload.get('template_id') or '')
    existing_env = json.loads(decrypt_api_key(current.encrypted_env)) if current and current.encrypted_env else {}
    env = {**existing_env, **(payload.get('env') or {})}
    for key in payload.get('env_remove') or []:
        env.pop(key, None)
    if template_id:
        command, env = _template_command(template_id, env)
    else:
        command = list(current.command or []) if current else []
        if payload.get('command') is not None and str(payload['command']).strip():
            command = [str(payload['command']).strip(), *(payload.get('args') or [])]
        elif 'args' in payload and command:
            command = [command[0], *(payload['args'] or [])]
        approved = any(value.get('command') == command for value in stdio_templates().values() if isinstance(value, dict))
        if not custom_stdio_enabled() and not approved:
            raise ValueError("此部署未开启自定义 stdio 启动配置，请选择已批准的模板。")
        if approved and not custom_stdio_enabled():
            allowed = next(value.get('env_keys') or [] for value in stdio_templates().values() if isinstance(value, dict) and value.get('command') == command)
            if any(key not in allowed for key in env):
                raise ValueError("MCP stdio environment contains an unknown key")
    _validate_stdio_command(command, env)
    return command, env


def _validate_remote_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Remote MCP URL must be a public HTTPS endpoint")
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return value
    if not ip.is_global:
        raise ValueError("Remote MCP URL must be a public HTTPS endpoint")
    return value


def server_payload(server: McpServer, *, include_launch: bool = False) -> dict:
    auth_config = {}
    auth_state = {}
    credential_error = False
    if server.auth_type in {'oauth', 'client_credentials'} and server.encrypted_auth:
        try:
            auth_state = json.loads(decrypt_api_key(server.encrypted_auth))
            if not isinstance(auth_state, dict):
                raise ValueError('Invalid authentication data')
        except (ValueError, json.JSONDecodeError):
            credential_error = True
            auth_state = {}
    if server.auth_type == "oauth":
        auth_config = {"scope": server.oauth_scope or ""}
        if auth_state:
            client = auth_state.get("oauth_client") or {}
            auth_config.update(client_id=client.get("client_id") or "", has_client_secret=bool(client.get("client_secret")),
                               token_endpoint_auth_method=client.get("method") or "client_secret_basic")
    if server.auth_type == "client_credentials" and auth_state:
        credentials = auth_state.get("credentials") or {}
        auth_config = {
            "client_id": credentials.get("client_id") or "",
            "scope": credentials.get("scope") or "",
            "token_endpoint_auth_method": credentials.get("method") or "client_secret_basic",
        }
    if server.auth_type in {None, '', 'none'}:
        auth_status = 'not_required'
    elif credential_error:
        auth_status = 'invalid'
    elif server.auth_type == 'oauth':
        auth_status = 'authorized' if (auth_state.get('tokens') or {}).get('access_token') else 'pending_authorization'
    else:
        auth_status = 'configured' if server.encrypted_auth else 'missing'
    if auth_status in {'configured', 'authorized'} and (server.catalog or {}).get('checked_at') and (server.catalog or {}).get('probe_status') != 'failed':
        auth_status = 'verified'
    return {
        "id": server.id,
        "name": server.name,
        "description": server.description or "",
        "category": server.category or "通用",
        "transport": server.transport,
        "protocol_mode": server.protocol_mode or "legacy",
        "url": server.url or "",
        "auth_type": server.auth_type or "none",
        "auth_config": auth_config,
        "auth_status": auth_status,
        "has_credential": bool(server.encrypted_auth or server.encrypted_env),
        "enabled": bool(server.enabled),
        "is_listed": bool(server.is_listed),
        "created_by": server.created_by,
        "workspace_id": server.workspace_id,
        "catalog": server.catalog or {},
        "updated_at": server.updated_at.isoformat() if server.updated_at else None,
        "stdio_config": ({"command": (server.command or [""])[0], "args": (server.command or [])[1:],
                          "env_keys": list(json.loads(decrypt_api_key(server.encrypted_env))) if server.encrypted_env else []}
                         if include_launch and server.transport == 'stdio' else None),
    }


def list_servers(db: Session, membership: WorkspaceMember) -> list[McpServer]:
    query = db.query(McpServer).filter(
        or_(McpServer.workspace_id == membership.workspace_id,
            and_(McpServer.workspace_id.is_(None), McpServer.is_listed.is_(True)))
    )
    if not can_manage(membership.role):
        query = query.filter(or_(McpServer.is_listed.is_(True), McpServer.created_by == membership.user_id))
    return query.order_by(McpServer.name.asc(), McpServer.id.asc()).all()


def accessible_server(db: Session, membership: WorkspaceMember, server_id: int) -> McpServer | None:
    return next((server for server in list_servers(db, membership) if server.id == server_id), None)


def can_edit_server(server: McpServer, membership: WorkspaceMember) -> bool:
    return server.workspace_id == membership.workspace_id and (
        can_manage(membership.role) or server.created_by == membership.user_id
    )


def create_server(db: Session, membership: WorkspaceMember, payload: dict) -> McpServer:
    count = db.query(McpServer.id).filter(McpServer.workspace_id == membership.workspace_id).count()
    if count >= MAX_SERVERS_PER_WORKSPACE:
        raise ValueError("Workspace MCP server limit reached")
    transport = payload.get("transport") or "streamable_http"
    if transport not in {"streamable_http", "sse", "stdio"}:
        raise ValueError("Unsupported MCP transport")
    server = McpServer(
        workspace_id=membership.workspace_id,
        created_by=membership.user_id,
        name=str(payload.get("name") or "").strip(),
        description=str(payload.get("description") or "").strip(),
        category=str(payload.get("category") or "通用").strip(),
        transport=transport,
        protocol_mode="legacy" if transport == "sse" else "auto",
        enabled=True,
        is_listed=bool(payload.get("is_listed")) if can_manage(membership.role) else False,
        config_version=1,
    )
    if not server.name:
        raise ValueError("MCP server name is required")
    if transport in {"streamable_http", "sse"}:
        server.url = _validate_remote_url(payload.get("url") or "")
        server.auth_type = payload.get("auth_type") or "none"
        if server.auth_type not in {"none", "bearer", "oauth", "client_credentials"}:
            raise ValueError("Unsupported MCP authentication type")
        if server.auth_type in {"oauth", "client_credentials"} and not can_manage(membership.role):
            raise ValueError("Only workspace managers can authorize shared MCP services")
        secret = str(payload.get("auth_secret") or "").strip()
        if server.auth_type == "bearer":
            if not secret:
                raise ValueError("Bearer token is required")
            server.encrypted_auth = encrypt_api_key(secret)
        elif server.auth_type == "client_credentials":
            client_id = str(payload.get("client_id") or "").strip()
            client_secret = str(payload.get("client_secret") or "").strip()
            if not client_id or not client_secret:
                raise ValueError("OAuth client id and secret are required")
            server.encrypted_auth = encrypt_api_key(json.dumps({"credentials": {
                "client_id": client_id, "client_secret": client_secret,
                "scope": str(payload.get("scope") or ""),
                "method": payload.get("token_endpoint_auth_method") or "client_secret_basic",
            }}))
        elif server.auth_type == "oauth":
            server.oauth_scope = str(payload.get("scope") or "").strip()
            _update_oauth_client(server, payload)
    else:
        if not can_manage(membership.role):
            raise ValueError("Only workspace managers can register stdio servers")
        command, env = _stdio_launch(payload)
        server.command = command
        server.encrypted_env = encrypt_api_key(json.dumps(env)) if env else ""
        server.auth_type = "none"
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def update_server(db: Session, server: McpServer, membership: WorkspaceMember, payload: dict) -> McpServer:
    if not can_edit_server(server, membership):
        raise ValueError("MCP server edit denied")
    connection_changed = any(key in payload for key in (
        "url", "auth_type", "auth_secret", "client_id", "client_secret", "scope",
        "token_endpoint_auth_method", "template_id", "command", "args", "env", "env_remove", "enabled"
    ))
    for key in ("name", "description", "category"):
        if key in payload and payload[key] is not None:
            setattr(server, key, str(payload[key]).strip())
    if not server.name:
        raise ValueError("MCP server name is required")
    if "enabled" in payload:
        server.enabled = bool(payload["enabled"])
    if "is_listed" in payload and can_manage(membership.role):
        server.is_listed = bool(payload["is_listed"])
    if server.transport in {"streamable_http", "sse"}:
        if server.auth_type in {"oauth", "client_credentials"} and not can_manage(membership.role) and any(
            key in payload for key in ("client_id", "client_secret", "scope", "token_endpoint_auth_method")
        ):
            raise ValueError("Only workspace managers can authorize shared MCP services")
        if "url" in payload:
            server.url = _validate_remote_url(payload["url"])
        if "auth_type" in payload:
            if payload["auth_type"] not in {"none", "bearer", "oauth", "client_credentials"}:
                raise ValueError("Unsupported MCP authentication type")
            if payload["auth_type"] in {"oauth", "client_credentials"} and not can_manage(membership.role):
                raise ValueError("Only workspace managers can authorize shared MCP services")
            if payload["auth_type"] != server.auth_type:
                server.encrypted_auth = ""
            server.auth_type = payload["auth_type"]
            if server.auth_type == "none":
                server.encrypted_auth = ""
        if "auth_secret" in payload and payload["auth_secret"]:
            server.encrypted_auth = encrypt_api_key(str(payload["auth_secret"]).strip())
        if server.auth_type == "bearer" and not server.encrypted_auth:
            raise ValueError("Bearer token is required")
        if server.auth_type == "client_credentials":
            state = json.loads(decrypt_api_key(server.encrypted_auth)) if server.encrypted_auth else {}
            credentials = dict(state.get("credentials") or {})
            for key in ("client_id", "client_secret"):
                if payload.get(key):
                    credentials[key] = str(payload[key]).strip()
            if "scope" in payload:
                credentials["scope"] = str(payload["scope"] or "").strip()
            if payload.get("token_endpoint_auth_method"):
                credentials["method"] = payload["token_endpoint_auth_method"]
            if not credentials.get("client_id") or not credentials.get("client_secret"):
                raise ValueError("OAuth client id and secret are required")
            if any(key in payload for key in ("client_id", "client_secret", "scope", "token_endpoint_auth_method", "auth_type")):
                server.encrypted_auth = encrypt_api_key(json.dumps({"credentials": credentials}))
        elif server.auth_type == "oauth":
            if "scope" in payload:
                next_scope = str(payload["scope"] or "").strip()
                if next_scope != (server.oauth_scope or ""):
                    state = json.loads(decrypt_api_key(server.encrypted_auth)) if server.encrypted_auth else {}
                    server.encrypted_auth = encrypt_api_key(json.dumps({"oauth_client": state["oauth_client"]})) if state.get("oauth_client") else ""
                server.oauth_scope = next_scope
            _update_oauth_client(server, payload)
    elif any(key in payload for key in ('template_id', 'command', 'args', 'env', 'env_remove')):
        if not can_manage(membership.role):
            raise ValueError("Only workspace managers can register stdio servers")
        command, env = _stdio_launch(payload, server)
        server.command = command
        server.encrypted_env = encrypt_api_key(json.dumps(env)) if env else ""
    if connection_changed:
        server.config_version = (server.config_version or 1) + 1
        server.catalog = {}
    db.commit()
    db.refresh(server)
    if connection_changed:
        invalidate_mcp_client(server.id)
    return server


def _update_oauth_client(server: McpServer, payload: dict) -> None:
    if not any(payload.get(key) for key in ("client_id", "client_secret")):
        if not payload.get("token_endpoint_auth_method"):
            return
    state = json.loads(decrypt_api_key(server.encrypted_auth)) if server.encrypted_auth else {}
    client = dict(state.get("oauth_client") or {})
    if payload.get("client_id") and client.get("client_id") != str(payload["client_id"]).strip():
        client.pop("client_secret", None)
    for key in ("client_id", "client_secret"):
        if payload.get(key):
            client[key] = str(payload[key]).strip()
    if not client.get("client_id") and not payload.get("client_secret"):
        return
    if not client.get("client_id"):
        raise ValueError("OAuth Client Secret 需要同时提供 Client ID。")
    client["method"] = (payload.get("token_endpoint_auth_method") or "client_secret_basic") if client.get("client_secret") else "none"
    # Changing the registered app invalidates tokens and SDK-discovered client information.
    server.encrypted_auth = encrypt_api_key(json.dumps({"oauth_client": client}))


def delete_server(db: Session, server: McpServer, membership: WorkspaceMember) -> None:
    if not can_edit_server(server, membership):
        raise ValueError("MCP server edit denied")
    if db.query(AgentMcpBinding.id).filter(AgentMcpBinding.mcp_server_id == server.id).first():
        raise ValueError("MCP server is bound to an agent")
    active_versions = db.query(AgentVersion).join(
        Agent, Agent.published_version_id == AgentVersion.id
    ).filter(Agent.workspace_id == membership.workspace_id).all()
    if any(
        item.get("server_id") == server.id
        for version in active_versions
        for item in (version.snapshot or {}).get("mcp_bindings") or []
    ):
        raise ValueError("MCP server is used by a published agent")
    sid = server.id
    db.delete(server)
    db.commit()
    invalidate_mcp_client(sid)


def probe_server(db: Session, server: McpServer) -> dict:
    if not server.enabled:
        raise ValueError("MCP server is disabled")
    if server.auth_type == "oauth":
        state = json.loads(decrypt_api_key(server.encrypted_auth)) if server.encrypted_auth else {}
        if not state.get("tokens"):
            raise ValueError("Authorize this MCP server before testing it")
    if server.transport in {"streamable_http", "sse"}:
        resolve_public_https(server.url)
    client = get_mcp_client(server)
    tools = client.refresh_tools() if hasattr(client, "refresh_tools") else client.list_tools()
    capabilities = {"tools": tools}
    for kind in ("resources", "prompts"):
        try:
            capabilities[kind] = getattr(client, f"list_{kind}")()
        except Exception:
            capabilities[kind] = []
    capabilities["checked_at"] = datetime.now(timezone.utc).isoformat()
    capabilities['probe_status'] = 'success'
    if len(json.dumps(capabilities, ensure_ascii=False)) > 2_000_000:
        raise ValueError("MCP capability catalog is too large")
    server.catalog = capabilities
    db.commit()
    return capabilities


def record_probe_failure(db: Session, server: McpServer) -> None:
    server.catalog = {**(server.catalog or {}), 'probe_status': 'failed', 'last_probe_at': datetime.now(timezone.utc).isoformat()}
    db.commit()


def agent_bindings_payload(db: Session, agent_id: int) -> list[dict]:
    rows = db.query(AgentMcpBinding).filter(AgentMcpBinding.agent_id == agent_id).all()
    return [
        {
            "server_id": row.mcp_server_id,
            "selected_tools": list((row.config or {}).get("selected_tools") or []),
            "enabled": bool(row.enabled),
        }
        for row in rows
    ]


def replace_agent_bindings(db: Session, membership: WorkspaceMember, agent_id: int, bindings: list[dict]) -> None:
    if len(bindings) > 20:
        raise ValueError("Too many MCP servers bound to agent")
    seen = set()
    validated = []
    total_tools = 0
    for item in bindings:
        server_id = int(item.get("server_id") or 0)
        if server_id in seen:
            raise ValueError("Duplicate MCP server binding")
        seen.add(server_id)
        server = accessible_server(db, membership, server_id)
        if not server or not server.enabled:
            raise ValueError("MCP server is unavailable")
        selected = item.get("selected_tools") or []
        if not isinstance(selected, list) or not selected or not all(isinstance(name, str) for name in selected):
            raise ValueError("Select at least one MCP tool")
        catalog_names = {tool.get("name") for tool in (server.catalog or {}).get("tools") or []}
        if not set(selected).issubset(catalog_names):
            raise ValueError("MCP tool selection is stale; test the server again")
        total_tools += len(selected)
        validated.append((server_id, selected, bool(item.get("enabled", True))))
    if total_tools > MAX_MCP_TOOLS_PER_AGENT:
        raise ValueError("Too many MCP tools bound to agent")
    db.query(AgentMcpBinding).filter(AgentMcpBinding.agent_id == agent_id).delete(synchronize_session=False)
    for server_id, selected, enabled in validated:
        db.add(AgentMcpBinding(
            agent_id=agent_id, mcp_server_id=server_id, enabled=enabled,
            config={"selected_tools": selected},
        ))
    db.commit()
