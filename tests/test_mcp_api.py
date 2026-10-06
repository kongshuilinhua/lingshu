"""MCP marketplace API: registration, probe, and per-agent tool selection."""

from types import SimpleNamespace
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.deps import get_current_membership
from api.routes.mcp import router
from core.db.base import Base
from core.db.models import Agent, User, Workspace, WorkspaceMember
from core.db.session import get_db


def test_marketplace_registers_and_binds_multiple_mcp_services(monkeypatch):
    import core.services.mcp_registry as registry
    from core.services.rag_cache import redis_store

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(redis_store, "rate_limit_check", lambda *args, **kwargs: (True, 9))
    with Session(engine) as db:
        workspace = Workspace(name="Test", slug="mcp-api-test")
        user = User(email="mcp-api@example.test", name="Test", password_hash="test")
        db.add_all([workspace, user])
        db.flush()
        membership = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="admin")
        agent = Agent(workspace_id=workspace.id, created_by=user.id, name="Test Agent")
        db.add_all([membership, agent])
        db.commit()

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_membership] = lambda: membership
        monkeypatch.setattr(registry, "resolve_public_https", lambda url: ("example.test", "93.184.216.34"))
        monkeypatch.setattr(registry, "get_mcp_client", lambda server: SimpleNamespace(
            list_tools=lambda: [{"name": "search", "description": "Search"}, {"name": "read", "description": "Read"}]
                               + [{"name": f"tool_{index}"} for index in range(47)],
            list_resources=lambda: [], list_prompts=lambda: [],
        ))
        client = TestClient(app)
        ids = []
        for name, transport in (("First", "streamable_http"), ("Second", "sse")):
            created = client.post("/api/mcp/servers", json={
                "name": name, "transport": transport, "url": f"https://{name.lower()}.example.test/mcp",
            })
            assert created.status_code == 200, created.text
            assert created.json()["server"]["transport"] == transport
            sid = created.json()["server"]["id"]
            ids.append(sid)
            assert client.post(f"/api/mcp/servers/{sid}/probe").status_code == 200
        result = client.put(f"/api/agents/{agent.id}/mcp-bindings", json={"items": [
            {"server_id": ids[0], "selected_tools": ["search"]},
            {"server_id": ids[1], "selected_tools": ["read"]},
        ]})
        assert result.status_code == 200, result.text
        assert len(result.json()["items"]) == 2
        all_tools = ["search", "read"] + [f"tool_{index}" for index in range(47)]
        all_selected = client.put(f"/api/agents/{agent.id}/mcp-bindings", json={"items": [
            {"server_id": ids[0], "selected_tools": all_tools},
        ]})
        assert all_selected.status_code == 200, all_selected.text
        assert all_selected.json()["items"][0]["selected_tools"] == all_tools
        oversized = client.put(f"/api/agents/{agent.id}/mcp-bindings", json={"items": [
            {"server_id": ids[0], "selected_tools": ["search"] * 401},
        ]})
        assert oversized.status_code == 422
        assert client.get(f"/api/agents/{agent.id}/mcp-bindings").json()["items"][0]["selected_tools"] == all_tools
        assert len(client.get("/api/mcp/servers").json()["items"]) == 2
        assert client.get('/api/mcp/connection-options').json()['transports'] == ['streamable_http', 'sse', 'stdio']
        monkeypatch.setattr(registry, 'get_settings', lambda: SimpleNamespace(mcp_allow_custom_stdio=True, mcp_stdio_templates_json='{}'))
        monkeypatch.setattr(registry, 'encrypt_api_key', lambda value: 'enc:' + value)
        monkeypatch.setattr(registry, 'decrypt_api_key', lambda value: value.removeprefix('enc:'))
        local = client.post('/api/mcp/servers', json={'name': 'Local stdio', 'transport': 'stdio', 'command': sys.executable,
                                                      'args': ['server.py'], 'env': {'TOKEN': 'test-secret'}})
        assert local.status_code == 200, local.text
        assert 'test-secret' not in local.text
        assert local.json()['server']['stdio_config']['args'] == ['server.py']
        local_id = local.json()['server']['id']
        assert client.post(f'/api/mcp/servers/{local_id}/probe').status_code == 200
        assert client.patch(f'/api/mcp/servers/{local_id}', json={'args': ['updated.py']}).status_code == 200
        assert client.delete(f'/api/mcp/servers/{local_id}').status_code == 200
        import api.routes.mcp as mcp_routes
        monkeypatch.setattr(mcp_routes, "get_settings", lambda: SimpleNamespace(
            mcp_client_metadata_url="https://lingshu.example.test/api/mcp/oauth/client-metadata",
            mcp_oauth_redirect_url="https://lingshu.example.test/api/mcp/oauth/callback",
        ))
        metadata = client.get("/api/mcp/oauth/client-metadata")
        assert metadata.status_code == 200
        assert metadata.json()["client_id"].endswith("/api/mcp/oauth/client-metadata")
        oauth = client.post("/api/mcp/servers", json={
            "name": "OAuth service", "transport": "streamable_http",
            "url": "https://oauth.example.test/mcp", "auth_type": "oauth",
        })
        assert oauth.status_code == 200
        assert client.post(f"/api/mcp/servers/{oauth.json()['server']['id']}/probe").status_code == 400
    engine.dispose()
