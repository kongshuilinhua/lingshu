"""OAuth callback state handoff and encrypted token storage contracts."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from mcp.shared.auth import OAuthToken
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.db.base import Base
from core.db.models import McpServer, User, Workspace
from core.integrations import mcp_oauth


def test_mcp_oauth_callback_and_token_storage(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(mcp_oauth, "SessionLocal", sessions)
    monkeypatch.setattr(mcp_oauth, "encrypt_api_key", lambda value: "enc:" + value)
    monkeypatch.setattr(mcp_oauth, "decrypt_api_key", lambda value: value.removeprefix("enc:"))
    monkeypatch.setattr(mcp_oauth, "resolve_public_https", lambda url: ("auth.example.test", "93.184.216.34"))
    with Session(engine) as db:
        user = User(email="oauth@example.test", name="Test", password_hash="test")
        workspace = Workspace(name="Test", slug="oauth-test")
        db.add_all([user, workspace])
        db.flush()
        server = McpServer(name="OAuth", workspace_id=workspace.id, created_by=user.id,
                           transport="streamable_http", url="https://mcp.example.test/mcp", auth_type="oauth")
        db.add(server)
        db.commit()
        server_id = server.id

    bridge = mcp_oauth.DatabaseOAuthBridge(server_id)
    asyncio.run(bridge.redirect_handler("https://auth.example.test/authorize?state=state-123"))
    assert bridge.wait_for_authorization_url().endswith("state=state-123")
    with pytest.raises(ValueError, match="not found"):
        mcp_oauth.receive_oauth_callback("wrong-state", "code", None)
    mcp_oauth.receive_oauth_callback("state-123", "code-123", "https://auth.example.test")
    callback = asyncio.run(bridge.callback_handler())
    assert callback.code == "code-123"
    assert callback.state == "state-123"
    assert callback.iss == "https://auth.example.test"

    storage = mcp_oauth.DatabaseTokenStorage(server_id)
    asyncio.run(storage.set_tokens(OAuthToken(access_token="test-token", token_type="Bearer")))
    assert asyncio.run(storage.get_tokens()).access_token == "test-token"
    with Session(engine) as db:
        server = db.get(McpServer, server_id)
        server.encrypted_auth = mcp_oauth.encrypt_api_key(json.dumps({"oauth_client": {
            "client_id": "registered-id", "client_secret": "test-secret", "method": "client_secret_post",
        }}))
        db.commit()
    monkeypatch.setattr(mcp_oauth, "get_settings", lambda: SimpleNamespace(
        deployment_mode="development", mcp_oauth_redirect_url="",
    ))
    info = asyncio.run(storage.get_client_info())
    assert info.client_id == 'registered-id'
    assert info.client_secret == 'test-secret'
    assert str(info.redirect_uris[0]) == 'http://127.0.0.1:8000/api/mcp/oauth/callback'
    engine.dispose()


def test_production_oauth_requires_explicit_callback(monkeypatch):
    monkeypatch.setattr(mcp_oauth, "get_settings", lambda: SimpleNamespace(deployment_mode="production", mcp_oauth_redirect_url=""))
    with pytest.raises(ValueError, match='MCP_OAUTH_REDIRECT_URL'):
        mcp_oauth.oauth_redirect_url()
