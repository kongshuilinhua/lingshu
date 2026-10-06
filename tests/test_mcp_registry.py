"""MCP catalog ownership and multi-server agent tool selection."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.db.base import Base
from core.db.models import Agent, AgentMcpBinding, AgentVersion, User, Workspace, WorkspaceMember
from core.services import mcp_registry
from core.services.agents import copy_agent_from_market


@pytest.fixture()
def catalog_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        workspaces = [Workspace(name="One", slug="one"), Workspace(name="Two", slug="two")]
        users = [User(email="one@example.test", name="One", password_hash="test"),
                 User(email="two@example.test", name="Two", password_hash="test")]
        db.add_all(workspaces + users)
        db.flush()
        memberships = [WorkspaceMember(workspace_id=workspaces[i].id, user_id=users[i].id, role="admin") for i in range(2)]
        agent = Agent(workspace_id=workspaces[0].id, created_by=users[0].id, name="A")
        db.add_all([*memberships, agent])
        db.commit()
        yield db, memberships, agent
    engine.dispose()


def test_agent_can_select_tools_from_multiple_mcp_services(catalog_db):
    db, memberships, agent = catalog_db
    first = mcp_registry.create_server(db, memberships[0], {
        "name": "Search", "transport": "streamable_http", "url": "https://search.example.test/mcp",
    })
    second = mcp_registry.create_server(db, memberships[0], {
        "name": "Files", "transport": "streamable_http", "url": "https://files.example.test/mcp",
    })
    first.catalog = {"tools": [{"name": "find"}, {"name": "summarize"}]}
    second.catalog = {"tools": [{"name": "read"}]}
    db.commit()
    mcp_registry.replace_agent_bindings(db, memberships[0], agent.id, [
        {"server_id": first.id, "selected_tools": ["find"]},
        {"server_id": second.id, "selected_tools": ["read"]},
    ])
    bindings = db.query(AgentMcpBinding).filter(AgentMcpBinding.agent_id == agent.id).all()
    assert len(bindings) == 2
    assert {row.mcp_server_id: row.config["selected_tools"] for row in bindings} == {
        first.id: ["find"], second.id: ["read"],
    }
    with pytest.raises(ValueError, match="stale"):
        mcp_registry.replace_agent_bindings(db, memberships[0], agent.id, [
            {"server_id": first.id, "selected_tools": ["unknown"]},
        ])


def test_catalog_is_workspace_scoped_and_stdio_uses_approved_template(catalog_db, monkeypatch):
    db, memberships, agent = catalog_db
    server = mcp_registry.create_server(db, memberships[1], {
        "name": "Other workspace", "transport": "streamable_http", "url": "https://other.example.test/mcp",
    })
    assert mcp_registry.accessible_server(db, memberships[0], server.id) is None
    with pytest.raises(ValueError, match="unavailable"):
        mcp_registry.replace_agent_bindings(db, memberships[0], agent.id, [
            {"server_id": server.id, "selected_tools": ["x"]},
        ])
    monkeypatch.setattr(mcp_registry, "get_settings", lambda: SimpleNamespace(
        mcp_stdio_templates_json='{"approved": {"command": ["python", "server.py"], "env_keys": ["TOKEN"]}}'
    ))
    monkeypatch.setattr(mcp_registry, "encrypt_api_key", lambda value: "encrypted-test-value")
    local = mcp_registry.create_server(db, memberships[0], {
        "name": "Approved", "transport": "stdio", "template_id": "approved", "env": {"TOKEN": "test"},
    })
    assert local.command == ["python", "server.py"]
    assert local.env == {}
    assert local.encrypted_env == "encrypted-test-value"


def test_custom_stdio_permission_and_secret_preserving_edit(catalog_db, monkeypatch):
    import json
    db, memberships, _ = catalog_db
    monkeypatch.setattr(mcp_registry, 'get_settings', lambda: SimpleNamespace(mcp_stdio_templates_json='{}', mcp_allow_custom_stdio=False))
    payload = {'name': 'Custom', 'transport': 'stdio', 'command': 'python', 'args': ['server.py'], 'env': {'TOKEN': 'test-token', 'OTHER': 'test-value'}}
    with pytest.raises(ValueError, match='未开启'):
        mcp_registry.create_server(db, memberships[0], payload)
    monkeypatch.setattr(mcp_registry, 'get_settings', lambda: SimpleNamespace(mcp_stdio_templates_json='{}', mcp_allow_custom_stdio=True))
    monkeypatch.setattr(mcp_registry, 'encrypt_api_key', lambda value: 'enc:' + value)
    monkeypatch.setattr(mcp_registry, 'decrypt_api_key', lambda value: value.removeprefix('enc:'))
    local = mcp_registry.create_server(db, memberships[0], payload)
    assert local.command == ['python', 'server.py']
    public = mcp_registry.server_payload(local)
    assert public['stdio_config'] is None
    editable = mcp_registry.server_payload(local, include_launch=True)
    assert editable['stdio_config']['env_keys'] == ['TOKEN', 'OTHER']
    assert 'test-token' not in json.dumps(editable)
    mcp_registry.update_server(db, local, memberships[0], {'args': ['new.py'], 'env': {'OTHER': 'updated'}})
    env = json.loads(mcp_registry.decrypt_api_key(local.encrypted_env))
    assert env == {'TOKEN': 'test-token', 'OTHER': 'updated'}
    assert local.command == ['python', 'new.py']
    mcp_registry.update_server(db, local, memberships[0], {'env_remove': ['TOKEN']})
    assert json.loads(mcp_registry.decrypt_api_key(local.encrypted_env)) == {'OTHER': 'updated'}
    memberships[0].role = 'user'
    with pytest.raises(ValueError, match='managers'):
        mcp_registry.update_server(db, local, memberships[0], {'command': 'another-program'})
    with pytest.raises(ValueError, match='managers'):
        mcp_registry.create_server(db, memberships[0], payload)


@pytest.mark.parametrize('extra', [{'args': ['x\x00y']}, {'env': {'BAD KEY': 'x'}}, {'args': ['x' * 4097]}], ids=['nul', 'invalid-env', 'oversized-arg'])
def test_custom_stdio_rejects_invalid_launch_config(catalog_db, monkeypatch, extra):
    db, memberships, _ = catalog_db
    monkeypatch.setattr(mcp_registry, 'get_settings', lambda: SimpleNamespace(mcp_stdio_templates_json='{}', mcp_allow_custom_stdio=True))
    with pytest.raises(ValueError):
        mcp_registry.create_server(db, memberships[0], {'name': 'Invalid', 'transport': 'stdio', 'command': 'python', **extra})


@pytest.mark.parametrize("url", ["http://example.test/mcp", "https://127.0.0.1/mcp", "https://user:secret@example.test/mcp"])
def test_remote_registration_rejects_unsafe_urls(catalog_db, url):
    db, memberships, _ = catalog_db
    with pytest.raises(ValueError, match="public HTTPS"):
        mcp_registry.create_server(db, memberships[0], {
            "name": "Unsafe", "transport": "streamable_http", "url": url,
        })


def test_sse_registration_preserves_endpoint_and_registered_oauth_app(catalog_db, monkeypatch):
    import json
    db, memberships, _ = catalog_db
    monkeypatch.setattr(mcp_registry, "encrypt_api_key", lambda value: "enc:" + value)
    monkeypatch.setattr(mcp_registry, "decrypt_api_key", lambda value: value.removeprefix("enc:"))
    server = mcp_registry.create_server(db, memberships[0], {
        "name": "OAuth SSE", "transport": "sse", "url": "https://mcp.example.test/sse/",
        "auth_type": "oauth", "client_id": "registered-id", "client_secret": "test-secret",
    })
    assert server.transport == "sse"
    assert server.url.endswith('/sse/')
    assert server.protocol_mode == "legacy"
    payload = mcp_registry.server_payload(server)
    assert payload['auth_config']['client_id'] == 'registered-id'
    assert payload['auth_config']['has_client_secret'] is True
    assert 'test-secret' not in json.dumps(payload)
    state = json.loads(mcp_registry.decrypt_api_key(server.encrypted_auth))
    state['tokens'] = {'access_token': 'old-token'}
    server.encrypted_auth = mcp_registry.encrypt_api_key(json.dumps(state))
    db.commit()
    mcp_registry.update_server(db, server, memberships[0], {'scope': 'read'})
    state = json.loads(mcp_registry.decrypt_api_key(server.encrypted_auth))
    assert 'tokens' not in state
    assert state['oauth_client']['client_secret'] == 'test-secret'
    mcp_registry.update_server(db, server, memberships[0], {'client_id': 'new-id'})
    state = json.loads(mcp_registry.decrypt_api_key(server.encrypted_auth))
    assert 'client_secret' not in state['oauth_client']


def test_copying_published_agent_preserves_listed_mcp_selection(catalog_db):
    db, memberships, agent = catalog_db
    server = mcp_registry.create_server(db, memberships[0], {
        "name": "Published service", "transport": "streamable_http",
        "url": "https://published.example.test/mcp", "is_listed": True,
    })
    server.catalog = {"tools": [{"name": "search"}]}
    version = AgentVersion(agent_id=agent.id, version=1, created_by=memberships[0].user_id,
                           snapshot={"name": "Published", "mcp_bindings": [
                               {"server_id": server.id, "selected_tools": ["search"], "enabled": True},
                           ]})
    db.add(version)
    db.flush()
    agent.published_version_id = version.id
    agent.status = "published"
    db.commit()
    copy = copy_agent_from_market(db, source=agent, user_id=memberships[0].user_id,
                                  workspace_id=memberships[0].workspace_id)
    assert mcp_registry.agent_bindings_payload(db, copy.id) == [
        {"server_id": server.id, "selected_tools": ["search"], "enabled": True},
    ]


def test_bearer_credential_is_not_returned_from_catalog(catalog_db, monkeypatch):
    db, memberships, _ = catalog_db
    monkeypatch.setattr(mcp_registry, "encrypt_api_key", lambda value: "encrypted-test-token")
    server = mcp_registry.create_server(db, memberships[0], {
        "name": "Protected", "transport": "streamable_http",
        "url": "https://protected.example.test/mcp", "auth_type": "bearer", "auth_secret": "private-token",
    })
    payload = mcp_registry.server_payload(server)
    assert server.encrypted_auth == "encrypted-test-token"
    assert payload["has_credential"] is True
    assert "private-token" not in str(payload)


def test_catalog_survives_metadata_only_edit(catalog_db):
    db, memberships, _ = catalog_db
    server = mcp_registry.create_server(db, memberships[0], {
        "name": "Before", "transport": "streamable_http", "url": "https://edit.example.test/mcp",
    })
    server.catalog = {"tools": [{"name": "search"}], "checked_at": "test"}
    db.commit()
    version = server.config_version
    mcp_registry.update_server(db, server, memberships[0], {"name": "After"})
    assert server.catalog["tools"][0]["name"] == "search"
    assert server.config_version == version


def test_authentication_status_tracks_credentials_authorization_and_probe(catalog_db, monkeypatch):
    import json
    db, memberships, _ = catalog_db
    monkeypatch.setattr(mcp_registry, 'encrypt_api_key', lambda value: 'enc:' + value)
    monkeypatch.setattr(mcp_registry, 'decrypt_api_key', lambda value: value.removeprefix('enc:'))
    server = mcp_registry.create_server(db, memberships[0], {'name': 'Bearer', 'url': 'https://auth.example.test/mcp',
        'auth_type': 'bearer', 'auth_secret': 'private-token'})
    assert mcp_registry.server_payload(server)['auth_status'] == 'configured'
    server.catalog = {'checked_at': 'previous-check', 'tools': [{'name': 'search'}]}
    db.commit()
    assert mcp_registry.server_payload(server)['auth_status'] == 'verified'
    mcp_registry.record_probe_failure(db, server)
    assert mcp_registry.server_payload(server)['auth_status'] == 'configured'
    assert server.catalog['tools'][0]['name'] == 'search'
    server.auth_type = 'oauth'
    server.encrypted_auth = 'enc:' + json.dumps({'oauth_client': {'client_id': 'app-id', 'client_secret': 'secret'}})
    db.commit()
    assert mcp_registry.server_payload(server)['auth_status'] == 'pending_authorization'
    server.encrypted_auth = 'enc:' + json.dumps({'tokens': {'access_token': 'test-only-token'}})
    db.commit()
    assert mcp_registry.server_payload(server)['auth_status'] == 'authorized'
    server.catalog = {'checked_at': 'new-check', 'probe_status': 'success'}
    db.commit()
    assert mcp_registry.server_payload(server)['auth_status'] == 'verified'
    assert 'test-only-token' not in json.dumps(mcp_registry.server_payload(server))


def test_client_credentials_registration_hides_secret(catalog_db, monkeypatch):
    db, memberships, _ = catalog_db
    monkeypatch.setattr(mcp_registry, "encrypt_api_key", lambda value: "enc:" + value)
    monkeypatch.setattr(mcp_registry, "decrypt_api_key", lambda value: value.removeprefix("enc:"))
    server = mcp_registry.create_server(db, memberships[0], {
        "name": "Service account", "transport": "streamable_http",
        "url": "https://oauth-robot.example.test/mcp", "auth_type": "client_credentials",
        "client_id": "public-id", "client_secret": "private-secret", "scope": "read",
    })
    payload = mcp_registry.server_payload(server)
    assert payload["auth_config"]["client_id"] == "public-id"
    assert payload["auth_config"]["scope"] == "read"
    assert "private-secret" not in str(payload)
