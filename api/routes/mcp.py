"""Workspace MCP marketplace and agent bindings."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from mcp.shared.auth import OAuthClientMetadata
from pydantic import AnyUrl
from sqlalchemy.orm import Session

from api.access import require_agent_write_access, require_workspace_agent
from api.deps import get_current_membership
from api.rate_limit import redis_rate_limit
from api.schemas import (
    AgentMcpBindingsRequest, McpPromptGetRequest, McpResourceReadRequest,
    McpServerCreateRequest, McpServerUpdateRequest,
)
from core.db.models import WorkspaceMember
from core.db.session import get_db
from core.config import get_settings
from core.integrations.mcp_client import get_mcp_client
from core.integrations.mcp_oauth import receive_oauth_callback, oauth_redirect_url
from core.integrations.mcp_sdk_client import McpSdkError
from core.security.api_keys import decrypt_api_key
from core.security.permissions import can_manage
from core.services.mcp_registry import (
    accessible_server,
    agent_bindings_payload,
    can_edit_server,
    create_server,
    delete_server,
    list_servers,
    probe_server,
    record_probe_failure,
    replace_agent_bindings,
    server_payload,
    stdio_templates,
    custom_stdio_enabled,
    update_server,
)

router = APIRouter(tags=["mcp"])


@router.get("/api/mcp/connection-options")
def mcp_connection_options(membership: WorkspaceMember = Depends(get_current_membership)):
    try:
        redirect = oauth_redirect_url()
    except ValueError:
        redirect = ""
    return {"transports": ["streamable_http", "sse", "stdio"], "oauth_redirect_url": redirect,
            "can_register_stdio": can_manage(membership.role),
            "can_configure_stdio": can_manage(membership.role) and custom_stdio_enabled()}


@router.get("/api/mcp/servers")
def get_mcp_servers(membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    return {"items": [{**server_payload(server, include_launch=can_manage(membership.role) and can_edit_server(server, membership)),
                       "can_edit": can_edit_server(server, membership)} for server in list_servers(db, membership)]}


@router.get("/api/mcp/stdio-templates")
def get_stdio_templates(membership: WorkspaceMember = Depends(get_current_membership)):
    if not can_manage(membership.role):
        return {"items": []}
    return {"items": [
        {"id": key, "name": value.get("name") or key,
         "description": value.get("description") or "", "env_keys": value.get("env_keys") or []}
        for key, value in stdio_templates().items() if isinstance(value, dict)
    ]}


@router.post("/api/mcp/servers")
def register_mcp_server(request: McpServerCreateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    try:
        server = create_server(db, membership, request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"server": server_payload(server, include_launch=can_manage(membership.role))}


@router.patch("/api/mcp/servers/{server_id}")
def patch_mcp_server(server_id: int, request: McpServerUpdateRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    server = accessible_server(db, membership, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    try:
        server = update_server(db, server, membership, request.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"server": server_payload(server, include_launch=can_manage(membership.role))}


@router.delete("/api/mcp/servers/{server_id}")
def remove_mcp_server(server_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    server = accessible_server(db, membership, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    try:
        delete_server(db, server, membership)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": True}


@router.post("/api/mcp/servers/{server_id}/probe", dependencies=[Depends(redis_rate_limit(10, 60, scope="mcp-probe"))])
def test_mcp_server(server_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    server = accessible_server(db, membership, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    try:
        catalog = probe_server(db, server)
    except ValueError as exc:
        record_probe_failure(db, server)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except McpSdkError as exc:
        record_probe_failure(db, server)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        record_probe_failure(db, server)
        raise HTTPException(status_code=502, detail="MCP service connection failed") from exc
    return {"server": server_payload(server), "catalog": catalog}


@router.post("/api/mcp/servers/{server_id}/resources/read")
def read_mcp_resource(server_id: int, request: McpResourceReadRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    server = accessible_server(db, membership, server_id)
    if not server or not server.enabled:
        raise HTTPException(status_code=404, detail="MCP server not found")
    known = {item.get("uri") for item in (server.catalog or {}).get("resources") or []}
    if request.uri not in known:
        raise HTTPException(status_code=400, detail="Resource is not in this server's catalog")
    try:
        result = get_mcp_client(server).read_resource(request.uri)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="MCP resource read failed") from exc
    if len(json.dumps(result, ensure_ascii=False)) > 1_000_000:
        raise HTTPException(status_code=413, detail="MCP resource is too large")
    return {"result": result}


@router.post("/api/mcp/servers/{server_id}/prompts/get")
def get_mcp_prompt(server_id: int, request: McpPromptGetRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    server = accessible_server(db, membership, server_id)
    if not server or not server.enabled:
        raise HTTPException(status_code=404, detail="MCP server not found")
    known = {item.get("name") for item in (server.catalog or {}).get("prompts") or []}
    if request.name not in known:
        raise HTTPException(status_code=400, detail="Prompt is not in this server's catalog")
    try:
        result = get_mcp_client(server).get_prompt(request.name, request.arguments)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="MCP prompt read failed") from exc
    if len(json.dumps(result, ensure_ascii=False)) > 1_000_000:
        raise HTTPException(status_code=413, detail="MCP prompt is too large")
    return {"result": result}


@router.post("/api/mcp/servers/{server_id}/oauth/start", dependencies=[Depends(redis_rate_limit(5, 60, scope="mcp-oauth"))])
def start_mcp_oauth(server_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    server = accessible_server(db, membership, server_id)
    if not server or not can_edit_server(server, membership):
        raise HTTPException(status_code=404, detail="MCP server not found")
    if server.auth_type != "oauth":
        raise HTTPException(status_code=400, detail="MCP server does not use OAuth")
    if not can_manage(membership.role):
        raise HTTPException(status_code=403, detail="只有工作区管理员可以授权共享 MCP 服务。")
    try:
        return get_mcp_client(server).begin_authorization()
    except (McpSdkError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="MCP OAuth authorization could not start") from exc


@router.get("/api/mcp/servers/{server_id}/oauth/status")
def mcp_oauth_status(server_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    server = accessible_server(db, membership, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    db.refresh(server)
    authorized = False
    if server.auth_type == "oauth" and server.encrypted_auth:
        try:
            authorized = bool(json.loads(decrypt_api_key(server.encrypted_auth)).get("tokens"))
        except (ValueError, json.JSONDecodeError):
            authorized = False
    return {"authorized": authorized}


@router.get("/api/mcp/oauth/callback", response_class=HTMLResponse)
def mcp_oauth_callback(state: str = Query(default=""), code: str = Query(default=""), iss: str | None = Query(default=None)):
    try:
        receive_oauth_callback(state, code, iss)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HTMLResponse("<html><body style='font-family:sans-serif;padding:40px'><h1>MCP 授权已接收</h1><p>请返回灵枢 Agent 等待连接完成。</p></body></html>")


@router.get("/api/mcp/oauth/client-metadata")
def mcp_oauth_client_metadata():
    settings = get_settings()
    if not settings.mcp_client_metadata_url or not settings.mcp_oauth_redirect_url:
        raise HTTPException(status_code=404, detail="MCP client metadata is not configured")
    metadata = OAuthClientMetadata(
        client_name="Lingshu Agent",
        redirect_uris=[AnyUrl(settings.mcp_oauth_redirect_url)],
        scope="",
    ).model_dump(mode="json", by_alias=True, exclude_none=True)
    return {"client_id": settings.mcp_client_metadata_url, **metadata}


@router.get("/api/agents/{agent_id}/mcp-bindings")
def get_agent_mcp_bindings(agent_id: int, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_write_access(agent, membership)
    return {"items": agent_bindings_payload(db, agent_id)}


@router.put("/api/agents/{agent_id}/mcp-bindings")
def put_agent_mcp_bindings(agent_id: int, request: AgentMcpBindingsRequest, membership: WorkspaceMember = Depends(get_current_membership), db: Session = Depends(get_db)):
    agent = require_workspace_agent(db, membership.workspace_id, agent_id)
    require_agent_write_access(agent, membership)
    try:
        replace_agent_bindings(db, membership, agent_id, [item.model_dump() for item in request.items])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"items": agent_bindings_payload(db, agent_id)}
