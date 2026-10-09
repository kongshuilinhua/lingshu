from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
import base64
import json
import pytest
from io import BytesIO
from zipfile import ZipFile


def _sse_payloads(body: str, event_name: str) -> list[dict]:
    payloads = []
    current_event = None
    data_lines = []
    for line in body.splitlines():
        if line.startswith("event: "):
            current_event = line.removeprefix("event: ").strip()
            data_lines = []
        elif line.startswith("data: "):
            data_lines.append(line.removeprefix("data: "))
        elif not line:
            if current_event == event_name and data_lines:
                payloads.append(json.loads("\n".join(data_lines)))
            current_event = None
            data_lines = []
    if current_event == event_name and data_lines:
        payloads.append(json.loads("\n".join(data_lines)))
    return payloads


def _create_custom_model(client, headers, model_name: str, **overrides):
    payload = {
        "provider": "openai-compatible",
        "model_name": model_name,
        "display_name": model_name.replace("-", " ").title(),
        "supports_text": True,
        "supports_image": False,
        "supports_document": True,
        "max_context": 8192,
        "default_temperature": 0.4,
        "enabled": True,
    }
    payload.update(overrides)
    response = client.post("/api/admin/models", headers=headers, json=payload)
    assert response.status_code == 200
    return response.json()["model"]


def _create_user_model(client, headers, display_name: str = "My Qwen", **overrides):
    payload = {
        "display_name": display_name,
        "provider": "openai-compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-private-test-key",
        "chat_model": "qwen-plus",
        "supports_image": False,
        "supports_document": True,
        "max_context": 131072,
        "default_temperature": 0.4,
        "enabled": True,
        "is_default": False,
    }
    payload.update(overrides)
    response = client.post("/api/user-models", headers=headers, json=payload)
    assert response.status_code == 200
    return response.json()["model_config"]


def _register_regular_user(client, *, email: str, name: str = "User"):
    response = client.post(
        "/api/auth/register",
        json={"email": email, "name": name, "password": "password123"},
    )
    assert response.status_code == 200
    assert response.json()["workspace"]["role"] == "user"
    return response, {"Authorization": f"Bearer {response.json()['access_token']}"}


def _docx_bytes(text_value: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>"
                f"{text_value}"
                "</w:t></w:r></w:p></w:body></w:document>"
            ),
        )
    return buffer.getvalue()


def test_second_registered_user_becomes_regular_user(client):
    admin = client.post(
        "/api/auth/register",
        json={"email": "first-admin@example.com", "name": "First Admin", "password": "password123"},
    )
    assert admin.status_code == 200
    assert admin.json()["workspace"]["role"] == "admin"

    user = client.post(
        "/api/auth/register",
        json={"email": "regular-user@example.com", "name": "Regular User", "password": "password123"},
    )
    assert user.status_code == 200
    assert user.json()["workspace"]["role"] == "user"

    user_headers = {"Authorization": f"Bearer {user.json()['access_token']}"}
    me = client.get("/api/auth/me", headers=user_headers)
    assert me.status_code == 200
    assert me.json()["membership"]["role"] == "user"


def test_invite_api_is_disabled_by_default(client, auth_headers):
    list_response = client.get("/api/workspaces/invites", headers=auth_headers)
    create_response = client.post(
        "/api/workspaces/invites",
        headers=auth_headers,
        json={"email": "disabled-invite@example.com", "role": "user"},
    )

    assert list_response.status_code == 404
    assert list_response.json()["detail"] == "Invite API is disabled"
    assert create_response.status_code == 404
    assert "token" not in create_response.text


def test_register_first_user_creates_workspace_and_template(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "name": "Owner", "password": "password123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace"]["role"] == "admin"
    assert payload["access_token"]

    agents = client.get("/api/agents", headers={"Authorization": f"Bearer {payload['access_token']}"})
    assert agents.status_code == 200
    assert any(item["is_template"] for item in agents.json()["items"])
    template = next(item for item in agents.json()["items"] if item["is_template"])
    assert template["status"] == "published"
    assert template["published_version_id"]


def test_login_and_me(client, owner_token):
    login = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "password123"})
    assert login.status_code == 200

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["membership"]["role"] == "admin"
    assert me.json()["user"]["avatar_url"] == ""


def test_health_exposes_model_status(client, owner_token):
    response = client.get("/api/health")

    assert response.status_code == 200
    model = response.json()["dependencies"]["model"]
    assert model["provider"] == "openai-compatible"
    assert model["base_url"]
    assert model["mock"] is True
    embedding = response.json()["dependencies"]["embedding"]
    assert embedding["provider"] == "openai-compatible"
    assert embedding["model"] == "text-embedding-v3"
    assert embedding["configured"] is False
    assert embedding["available"] is False
    assert response.json()["status"] == "degraded"
    assert response.json()["dependencies"]["vector_store"]["active_backend"] == "memory"


def test_dashscope_key_alias_uses_dashscope_compatible_base(monkeypatch):
    from core.config import get_settings
    from core.integrations.llm import DASHSCOPE_COMPATIBLE_BASE, OpenAICompatibleProvider

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope-test")
    monkeypatch.setenv("OPENAI_API_BASE", DASHSCOPE_COMPATIBLE_BASE)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        provider = OpenAICompatibleProvider()

        assert provider._api_key(settings) == "sk-dashscope-test"
        assert provider._api_base(settings) == DASHSCOPE_COMPATIBLE_BASE
    finally:
        get_settings.cache_clear()


def test_default_backend_model_seed_prefers_qwen_and_keeps_gpt_explicit(client, auth_headers):
    models = client.get("/api/models", headers=auth_headers)

    assert models.status_code == 200
    items = models.json()["items"]
    assert [item["model_name"] for item in items[:2]] == ["qwen-plus", "qwen-vl-plus"]
    assert not any(item["model_name"].startswith("gpt-") for item in items)
    assert items[0]["supports_reasoning"] is True
    assert items[0]["reasoning_type"] == "prompt"
    assert items[0]["reasoning_label"] == "提示词增强"

    explicit_gpt = _create_custom_model(client, auth_headers, "gpt-4o-mini")
    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "Explicit GPT Gateway", "model_id": explicit_gpt["id"]},
    )

    assert agent.status_code == 200
    assert agent.json()["agent"]["model"] == "gpt-4o-mini"


def test_user_profile_avatar_can_be_updated(client, owner_token):
    headers = {"Authorization": f"Bearer {owner_token}"}
    avatar = "data:image/png;base64,iVBORw0KGgo="

    updated = client.patch(
        "/api/auth/me",
        headers=headers,
        json={"name": "New Owner", "avatar_url": avatar},
    )

    assert updated.status_code == 200
    assert updated.json()["user"]["name"] == "New Owner"
    assert updated.json()["user"]["avatar_url"] == avatar

    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["user"]["name"] == "New Owner"
    assert me.json()["user"]["avatar_url"] == avatar


def test_legacy_team_copy_is_normalized(client, auth_headers):
    from core.db.session import engine, init_db

    with engine.begin() as connection:
        connection.execute(text("UPDATE workspaces SET name = 'Owner 的团队' WHERE id = 1"))
        connection.execute(
            text(
                "UPDATE agents SET description = '面向团队内部使用的自定义智能体。', "
                "opening_message = '你好，我是你的团队智能体。', "
                "system_prompt = '你是一个谨慎、清晰的团队智能体。优先使用绑定知识库和工具输出回答。' "
                "WHERE id = 1"
            )
        )

    init_db()

    workspace = client.get("/api/workspaces/current", headers=auth_headers)
    assert workspace.status_code == 200
    assert workspace.json()["workspace"]["name"] == "Owner 的工作台"

    agents = client.get("/api/agents", headers=auth_headers)
    assert agents.status_code == 200
    assert "团队" not in agents.json()["items"][0]["description"]

    detail = client.get(f"/api/agents/{agents.json()['items'][0]['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert "团队" not in detail.json()["agent"]["opening_message"]
    assert "团队" not in detail.json()["agent"]["system_prompt"]


def test_user_model_config_schema_supports_agent_binding(client):
    from core.db.models import Agent, User, UserModelConfig, Workspace
    from core.db.session import SessionLocal, engine

    registered = client.post(
        "/api/auth/register",
        json={"email": "schema-owner@example.com", "name": "Schema Owner", "password": "password123"},
    )
    assert registered.status_code == 200

    inspector = inspect(engine)
    assert "user_model_configs" in inspector.get_table_names()
    agent_columns = {column["name"] for column in inspector.get_columns("agents")}
    assert "user_model_config_id" in agent_columns
    config_columns = {column["name"] for column in inspector.get_columns("user_model_configs")}
    assert {
        "user_id",
        "display_name",
        "provider",
        "base_url",
        "encrypted_api_key",
        "chat_model",
        "supports_image",
        "supports_document",
        "supports_reasoning",
        "reasoning_type",
        "reasoning_label",
        "max_context",
        "default_temperature",
        "enabled",
        "is_default",
        "created_at",
        "updated_at",
    }.issubset(config_columns)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "schema-owner@example.com").one()
        workspace = db.query(Workspace).filter(Workspace.slug == "default").one()
        config = UserModelConfig(
            user_id=user.id,
            display_name="My Qwen",
            provider="openai-compatible",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            encrypted_api_key="encrypted:test-key",
            chat_model="qwen-plus",
            is_default=True,
        )
        db.add(config)
        db.flush()
        agent = Agent(
            workspace_id=workspace.id,
            user_model_config_id=config.id,
            name="Private Model Agent",
            created_by=user.id,
        )
        db.add(agent)
        db.commit()

        saved = db.get(Agent, agent.id)
        assert saved.user_model_config_id == config.id
    finally:
        db.close()


def test_user_model_configs_allow_only_one_default_per_user(client):
    from core.db.models import User, UserModelConfig
    from core.db.session import SessionLocal

    registered = client.post(
        "/api/auth/register",
        json={"email": "default-owner@example.com", "name": "Default Owner", "password": "password123"},
    )
    assert registered.status_code == 200

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "default-owner@example.com").one()
        db.add(
            UserModelConfig(
                user_id=user.id,
                display_name="Default Qwen",
                provider="openai-compatible",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                encrypted_api_key="encrypted:first",
                chat_model="qwen-plus",
                is_default=True,
            )
        )
        db.commit()

        db.add(
            UserModelConfig(
                user_id=user.id,
                display_name="Second Default Qwen",
                provider="openai-compatible",
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                encrypted_api_key="encrypted:second",
                chat_model="qwen-max",
                is_default=True,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError("Expected one default user model config per user")
    finally:
        db.close()


def test_compat_migration_adds_user_model_config_id_to_existing_agents(client):
    from core.db.session import engine, init_db

    is_mysql = engine.dialect.name == "mysql"
    with engine.begin() as connection:
        # MySQL 忽略 DROP TABLE ... CASCADE 且 FK 会挡住删表（agent_memory_profiles 等引用 agents），
        # 故先临时关闭外键检查（与 conftest 同款），让模拟旧 schema 的 drop/recreate 能跑通。
        if is_mysql:
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        connection.execute(
            text(
                "DROP TABLE IF EXISTS "
                "feedback, run_steps, session_memory, messages, sessions, runs, "
                "workflow_definitions, agent_tools, agent_knowledge_bases, agent_settings, agent_versions "
                "CASCADE"
            )
        )
        connection.execute(text("DROP TABLE IF EXISTS agents CASCADE"))
        connection.execute(
            text(
                "CREATE TABLE agents ("
                "id INTEGER PRIMARY KEY, "
                "workspace_id INTEGER, "
                "model_id INTEGER, "
                "name VARCHAR(160), "
                "avatar VARCHAR(40), "
                "description TEXT, "
                "opening_message TEXT, "
                "system_prompt TEXT, "
                "model VARCHAR(120), "
                "temperature FLOAT, "
                "status VARCHAR(20), "
                "published_version_id INTEGER, "
                "is_template BOOLEAN, "
                "created_by INTEGER, "
                "created_at TIMESTAMP, "
                "updated_at TIMESTAMP"
                ")"
            )
        )
        # 复位外键检查：连接归还连接池后该会话变量会被复用，必须在本连接内还原，避免污染后续查询。
        if is_mysql:
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    assert "user_model_config_id" not in {column["name"] for column in inspect(engine).get_columns("agents")}

    init_db()

    agent_columns = {column["name"] for column in inspect(engine).get_columns("agents")}
    agent_indexes = {index["name"] for index in inspect(engine).get_indexes("agents")}
    assert "user_model_config_id" in agent_columns
    assert "ix_agents_user_model_config_id" in agent_indexes


def test_memory_profile_schema_and_compat_migration(client, auth_headers):
    from core.db.models import AgentMemoryProfile
    from core.db.session import SessionLocal, engine, init_db

    inspector = inspect(engine)
    assert "agent_memory_profiles" in inspector.get_table_names()
    memory_columns = {column["name"] for column in inspector.get_columns("agent_memory_profiles")}
    assert {
        "workspace_id",
        "user_id",
        "agent_id",
        "enabled",
        "summary",
        "facts",
        "preferences",
        "created_at",
        "updated_at",
    }.issubset(memory_columns)

    created = client.post("/api/agents", headers=auth_headers, json={"name": "Memory Schema"})
    agent_id = created.json()["agent"]["id"]
    client.patch(
        f"/api/agents/{agent_id}/memory-profile",
        headers=auth_headers,
        json={"enabled": True, "summary": "schema memory"},
    )

    db = SessionLocal()
    try:
        profile = db.query(AgentMemoryProfile).filter(AgentMemoryProfile.agent_id == agent_id).one()
        duplicate = AgentMemoryProfile(
            workspace_id=profile.workspace_id,
            user_id=profile.user_id,
            agent_id=profile.agent_id,
            enabled=False,
        )
        db.add(duplicate)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError("Expected one memory profile per workspace/user/agent")
    finally:
        db.close()

    with engine.begin() as connection:
        dialect_name = engine.dialect.name
        if dialect_name == "mysql":
            inspector = inspect(engine)
            existing = {col["name"] for col in inspector.get_columns("agent_memory_profiles")}
            if "preferences" in existing:
                connection.execute(text("ALTER TABLE agent_memory_profiles DROP COLUMN preferences"))
            if "updated_at" in existing:
                connection.execute(text("ALTER TABLE agent_memory_profiles DROP COLUMN updated_at"))
        else:
            connection.execute(text("ALTER TABLE agent_memory_profiles DROP COLUMN IF EXISTS preferences"))
            connection.execute(text("ALTER TABLE agent_memory_profiles DROP COLUMN IF EXISTS updated_at"))

    init_db()
    migrated_columns = {column["name"] for column in inspect(engine).get_columns("agent_memory_profiles")}
    assert {"preferences", "updated_at"}.issubset(migrated_columns)


def test_user_model_crud_never_returns_raw_api_key(client, auth_headers):
    created = _create_user_model(
        client,
        auth_headers,
        is_default=True,
        supports_reasoning=True,
        reasoning_type="prompt",
        reasoning_label="提示词增强",
    )

    assert created["has_api_key"] is True
    assert created["supports_reasoning"] is True
    assert created["reasoning_type"] == "prompt"
    assert created["reasoning_label"] == "提示词增强"
    assert "api_key" not in created
    assert "embedding_api_key" not in created
    assert "encrypted_api_key" not in created
    assert "encrypted_embedding_api_key" not in created

    listed = client.get("/api/user-models", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == created["id"]
    assert "encrypted_api_key" not in listed.text
    assert "encrypted_embedding_api_key" not in listed.text
    assert "sk-private-test-key" not in listed.text

    patched = client.patch(
        f"/api/user-models/{created['id']}",
        headers=auth_headers,
        json={"display_name": "Renamed Qwen"},
    )
    assert patched.status_code == 200
    assert patched.json()["model_config"]["display_name"] == "Renamed Qwen"
    assert "sk-private-test-key" not in patched.text

    deleted = client.delete(f"/api/user-models/{created['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}


def test_user_model_image_support_is_backend_detected(client, auth_headers):
    text_model = _create_user_model(
        client,
        auth_headers,
        "Claimed Vision Text Model",
        chat_model="qwen-plus",
        supports_image=True,
    )

    assert text_model["supports_image"] is False
    assert text_model["image_detection"]["tested"] is True
    assert text_model["image_detection"]["confirmed"] is False
    assert text_model["image_detection"]["source"] == "backend_probe"

    patched = client.patch(
        f"/api/user-models/{text_model['id']}",
        headers=auth_headers,
        json={"chat_model": "qwen-vl-plus"},
    )
    assert patched.status_code == 200
    patched_model = patched.json()["model_config"]
    assert patched_model["supports_image"] is True
    assert patched_model["image_detection"]["confirmed"] is True

    downgraded = client.patch(
        f"/api/user-models/{text_model['id']}",
        headers=auth_headers,
        json={"supports_image": True, "chat_model": "qwen-plus"},
    )
    assert downgraded.status_code == 200
    downgraded_model = downgraded.json()["model_config"]
    assert downgraded_model["supports_image"] is False
    assert downgraded_model["image_detection"]["confirmed"] is False


def test_user_model_cross_user_access_is_hidden(client, auth_headers):
    config = _create_user_model(client, auth_headers)
    _, user_headers = _register_regular_user(client, email="private-model-user@example.com", name="Private Model User")

    patch_response = client.patch(f"/api/user-models/{config['id']}", headers=user_headers, json={})
    assert patch_response.status_code == 404
    assert patch_response.json()["detail"] == "Model config not found"

    delete_response = client.delete(f"/api/user-models/{config['id']}", headers=user_headers)
    assert delete_response.status_code == 404
    assert delete_response.json()["detail"] == "Model config not found"

    test_response = client.post(f"/api/user-models/{config['id']}/test", headers=user_headers)
    assert test_response.status_code == 404
    assert test_response.json()["detail"] == "Model config not found"


def test_user_model_rejects_empty_api_key(client, auth_headers):
    empty_create = client.post(
        "/api/user-models",
        headers=auth_headers,
        json={
            "display_name": "Bad",
            "provider": "openai-compatible",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": " ",
            "chat_model": "qwen-plus",
        },
    )
    assert empty_create.status_code == 400
    assert empty_create.json()["detail"] == "API key cannot be empty"

    config = _create_user_model(client, auth_headers)
    empty_patch = client.patch(f"/api/user-models/{config['id']}", headers=auth_headers, json={"api_key": ""})
    assert empty_patch.status_code == 400
    assert empty_patch.json()["detail"] == "API key cannot be empty"


def test_user_model_allows_document_attachment_without_embedding_model(client, auth_headers):
    response = client.post(
        "/api/user-models",
        headers=auth_headers,
        json={
            "display_name": "Document Attachment Model",
            "provider": "openai-compatible",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-chat-only",
            "chat_model": "deepseek-v4-flash",
            "supports_document": True,
        },
    )

    assert response.status_code == 200
    config = response.json()["model_config"]
    assert "embedding_model" not in config
    assert "embedding_base_url" not in config
    assert config["supports_document"] is True


def test_user_model_default_switching_and_delete_in_use(client, auth_headers):
    first = _create_user_model(client, auth_headers, "First Qwen", is_default=True)
    second = _create_user_model(client, auth_headers, "Second Qwen", chat_model="qwen-max", is_default=True)

    models = client.get("/api/user-models", headers=auth_headers).json()["items"]
    assert next(item for item in models if item["id"] == first["id"])["is_default"] is False
    assert next(item for item in models if item["id"] == second["id"])["is_default"] is True

    agent = client.post("/api/agents", headers=auth_headers, json={"name": "Private Model Agent", "user_model_config_id": second["id"]})
    assert agent.status_code == 200
    assert agent.json()["agent"]["user_model_config_id"] == second["id"]
    assert agent.json()["agent"]["model"] == "qwen-max"

    blocked = client.delete(f"/api/user-models/{second['id']}", headers=auth_headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Model config is in use"


def test_agent_with_text_only_chat_model_can_still_enable_backend_default_rag(client, auth_headers):
    chat = _create_user_model(
        client,
        auth_headers,
        "DeepSeek Chat",
        chat_model="deepseek-chat",
        supports_document=False,
        is_default=True,
    )

    created = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "Split Model Agent",
            "user_model_config_id": chat["id"],
            "rag": {"enabled_by_default": True, "top_k": 2},
        },
    )

    assert created.status_code == 200
    agent = created.json()["agent"]
    assert agent["user_model_config_id"] == chat["id"]
    assert "rag_user_model_config_id" not in agent
    assert "rag_user_model_config" not in agent
    assert agent["model"] == "deepseek-chat"
    assert "supports_rag" not in agent["user_model_config"]


def test_private_model_publish_snapshot_is_non_secret_and_runtime_uses_private_model(client, auth_headers):
    config = _create_user_model(client, auth_headers, "Runtime Qwen", chat_model="qwen-runtime", default_temperature=0.25)
    created = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "Runtime Private", "user_model_config_id": config["id"], "memory": {"enabled": True, "strategy": "session_summary", "max_messages": 6}},
    )
    assert created.status_code == 200
    agent_id = created.json()["agent"]["id"]

    published = client.post(f"/api/agents/{agent_id}/publish", headers=auth_headers)
    assert published.status_code == 200
    snapshot = published.json()["version"]["snapshot"]
    assert snapshot["user_model_config_id"] == config["id"]
    assert snapshot["user_model_config"]["chat_model"] == "qwen-runtime"
    assert "api_key" not in str(snapshot)
    assert "embedding_api_key" not in str(snapshot)
    assert "encrypted_api_key" not in str(snapshot)
    assert "encrypted_embedding_api_key" not in str(snapshot)
    assert "sk-private-test-key" not in str(snapshot)

    chat = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "private runtime", "mode": "published"},
    )
    assert chat.status_code == 200
    assert "event: done" in chat.text
    assert '"model": "qwen-runtime"' in chat.text
    assert "sk-private-test-key" not in chat.text


def test_system_model_runtime_ignores_default_private_model(client, auth_headers):
    default_private = _create_user_model(
        client,
        auth_headers,
        "Default Private",
        chat_model="private-default-runtime",
        is_default=True,
    )
    system_model = _create_custom_model(client, auth_headers, "system-runtime-model")
    created = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "System Runtime", "model_id": system_model["id"], "user_model_config_id": None},
    )
    assert created.status_code == 200
    agent_id = created.json()["agent"]["id"]

    draft_chat = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "draft runtime", "mode": "draft"},
    )
    assert draft_chat.status_code == 200
    assert '"model": "system-runtime-model"' in draft_chat.text
    assert default_private["chat_model"] not in draft_chat.text

    published = client.post(f"/api/agents/{agent_id}/publish", headers=auth_headers)
    assert published.status_code == 200
    assert published.json()["version"]["snapshot"]["user_model_config_id"] is None

    published_chat = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "published runtime", "mode": "published"},
    )
    assert published_chat.status_code == 200
    assert '"model": "system-runtime-model"' in published_chat.text
    assert default_private["chat_model"] not in published_chat.text


def test_rag_no_evidence_still_uses_model_answer(client, auth_headers):
    kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "No Evidence KB"})
    assert kb.status_code == 200
    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "No Evidence Agent",
            "knowledge_base_ids": [kb.json()["knowledge_base"]["id"]],
            "rag": {"enabled_by_default": True, "refuse_when_no_evidence": True},
        },
    )
    assert agent.status_code == 200

    response = client.post(
        f"/api/agents/{agent.json()['agent']['id']}/chat/stream",
        headers=auth_headers,
        json={"message": "what can you do", "mode": "draft", "rag_enabled": True},
    )

    assert response.status_code == 200
    assert "event: rag_status" in response.text
    assert '"no_evidence": true' in response.text
    assert "event: token" in response.text
    assert "event: done" in response.text
    assert "No reliable evidence was found" not in response.text


def test_user_model_provider_test_is_sanitized(client, auth_headers):
    config = _create_user_model(client, auth_headers)

    result = client.post(f"/api/user-models/{config['id']}/test", headers=auth_headers)

    assert result.status_code == 200
    payload = result.json()
    assert payload["model"] == "qwen-plus"
    assert "ok" in payload
    assert "checks" in payload
    assert "chat" in payload["checks"]
    assert "sk-private-test-key" not in result.text
    assert "Authorization" not in result.text


def test_user_model_draft_test_checks_capabilities_without_saving(client, auth_headers):
    result = client.post(
        "/api/user-models/test",
        headers=auth_headers,
        json={
            "display_name": "Draft Qwen",
            "provider": "openai-compatible",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-draft-chat",
            "chat_model": "qwen-plus",
            "supports_image": False,
            "supports_document": True,
        },
    )

    assert result.status_code == 200
    payload = result.json()
    assert "checks" in payload
    assert payload["checks"]["chat"]["required"] is True
    assert payload["checks"]["image"]["required"] is False
    assert "sk-draft-chat" not in result.text
    listed = client.get("/api/user-models", headers=auth_headers)
    assert all(item["display_name"] != "Draft Qwen" for item in listed.json()["items"])


def test_user_model_draft_test_reports_image_probe_without_overriding_declared_capability(client, auth_headers):
    text_result = client.post(
        "/api/user-models/test",
        headers=auth_headers,
        json={
            "display_name": "Text Qwen",
            "provider": "openai-compatible",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-text-detect",
            "chat_model": "qwen-plus",
            "supports_image": False,
            "supports_document": True,
            "detect_image": True,
        },
    )

    assert text_result.status_code == 200
    text_payload = text_result.json()
    assert text_payload["ok"] is True
    assert text_payload["detected_capabilities"]["supports_text"] is True
    assert text_payload["detected_capabilities"]["supports_image"] is False
    assert text_payload["detected_capabilities"]["image_confirmed"] is False
    assert text_payload["detected_capabilities"]["image_declared"] is False
    assert text_payload["detected_capabilities"]["image_status"] == "failed"
    assert text_payload["detected_capabilities"]["image_error_code"] in {"image_probe_failed", "image_payload_rejected", "mock_model_name_not_vision"}
    assert text_payload["detected_capabilities"]["image_error"]
    assert text_payload["checks"]["image"]["required"] is False
    assert text_payload["checks"]["image"]["detected"] is True
    assert text_payload["checks"]["image"]["tested"] is True

    vision_result = client.post(
        "/api/user-models/test",
        headers=auth_headers,
        json={
            "display_name": "Vision Qwen",
            "provider": "openai-compatible",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-vision-detect",
            "chat_model": "qwen-vl-plus",
            "supports_image": True,
            "supports_document": True,
            "detect_image": True,
        },
    )

    assert vision_result.status_code == 200
    vision_payload = vision_result.json()
    assert vision_payload["ok"] is True
    assert vision_payload["detected_capabilities"]["supports_image"] is True
    assert vision_payload["detected_capabilities"]["image_confirmed"] is True
    assert vision_payload["detected_capabilities"]["image_declared"] is True
    assert vision_payload["detected_capabilities"]["image_status"] == "confirmed"
    assert vision_payload["checks"]["image"]["required"] is False
    assert "sk-vision-detect" not in vision_result.text


def test_user_model_image_probe_support_requires_confirmation(client, auth_headers):
    result = client.post(
        "/api/user-models/test",
        headers=auth_headers,
        json={
            "display_name": "Claimed Vision Qwen",
            "provider": "openai-compatible",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-claimed-vision",
            "chat_model": "qwen-plus",
            "supports_image": True,
            "supports_document": True,
            "detect_image": True,
        },
    )

    assert result.status_code == 200
    payload = result.json()
    assert payload["detected_capabilities"]["image_declared"] is True
    assert payload["detected_capabilities"]["image_confirmed"] is False
    assert payload["detected_capabilities"]["supports_image"] is False
    assert payload["detected_capabilities"]["image_status"] == "failed"
    assert payload["detected_capabilities"]["image_error_code"] in {"image_probe_failed", "image_payload_rejected", "mock_model_name_not_vision"}
    assert "sk-claimed-vision" not in result.text


def test_tool_crud_http_security_and_secret_redaction(client, auth_headers):
    created = client.post(
        "/api/tools",
        headers=auth_headers,
        json={
            "type": "http",
            "name": "custom_weather_lookup",
            "label": "Weather lookup",
            "description": "Fetches weather data",
            "method": "GET",
            "url": "https://example.com/weather",
            "query_schema": {"city": {"type": "string", "required": True}},
            "auth": {"type": "bearer", "secret": "tool-secret-token"},
            "timeout_seconds": 5,
        },
    )

    assert created.status_code == 200
    tool = created.json()["tool"]
    assert tool["type"] == "http"
    assert tool["auth"]["has_secret"] is True
    assert "tool-secret-token" not in created.text
    assert "encrypted_secret" not in created.text

    listed = client.get("/api/tools", headers=auth_headers)
    assert listed.status_code == 200
    listed_items = listed.json()["items"]
    assert any(item["id"] == tool["id"] and item["auth"]["has_secret"] for item in listed_items)
    tool_names = {item["name"] for item in listed_items}
    assert {"web_search", "current_time", "calculator", "web_reader", "wikipedia", "arxiv_search"}.issubset(tool_names)
    assert {
        "image_search",
        "news_search",
        "qr_generator",
        "currency_converter",
        "ip_lookup",
        "url_shortener",
        "horoscope",
        "joke_generator",
        "advice_slip",
        "bored_activity",
    }.isdisjoint(tool_names)
    assert "tool-secret-token" not in listed.text
    assert "encrypted_secret" not in listed.text

    patched = client.patch(
        f"/api/tools/{tool['id']}",
        headers=auth_headers,
        json={"label": "Renamed weather", "auth": {"type": "bearer"}},
    )
    assert patched.status_code == 200
    assert patched.json()["tool"]["label"] == "Renamed weather"
    assert patched.json()["tool"]["auth"]["has_secret"] is True
    assert "tool-secret-token" not in patched.text

    insecure = client.post(
        "/api/tools",
        headers=auth_headers,
        json={"type": "http", "name": "insecure", "label": "Insecure", "url": "http://example.com"},
    )
    assert insecure.status_code == 400
    assert insecure.json()["detail"] == "Public HTTPS URL required"

    blocked = client.post(
        "/api/tools",
        headers=auth_headers,
        json={"type": "http", "name": "metadata", "label": "Metadata", "url": "https://169.254.169.254/latest/meta-data"},
    )
    assert blocked.status_code == 400
    assert "HTTP tool target is blocked" in blocked.json()["detail"]

    deleted = client.delete(f"/api/tools/{tool['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}


def test_tool_access_binding_runtime_and_tool_call_events(client, auth_headers):
    from core.services import web_search

    def fake_search(query, *, top_k=None, timeout_seconds=None):
        return {
            "query": query,
            "provider": "fake",
            "latency_ms": 7,
            "items": [
                {"title": "Brush Fix", "url": "https://example.com/brush", "snippet": "Use the brush reset flow."},
            ][: top_k or 1],
        }

    web_search.search_web = fake_search
    search = client.post(
        "/api/tools",
        headers=auth_headers,
        json={
            "type": "builtin_search",
            "name": "kb_search",
            "label": "KB Search",
            "description": "Search test",
            "search_options": {"top_k": 2},
        },
    )
    assert search.status_code == 200
    tool_id = search.json()["tool"]["id"]

    test_result = client.post(f"/api/tools/{tool_id}/test", headers=auth_headers, json={"input": {"query": "brush error"}})
    assert test_result.status_code == 200
    assert test_result.json()["ok"] is True
    assert test_result.json()["tool_type"] == "builtin_search"

    agent = client.post("/api/agents", headers=auth_headers, json={"name": "Tool Agent", "tool_ids": [tool_id], "tool_policy": {"web_search_enabled": True}})
    assert agent.status_code == 200
    assert agent.json()["agent"]["tools"][0]["id"] == tool_id
    assert "encrypted_secret" not in agent.text

    chat = client.post(
        f"/api/agents/{agent.json()['agent']['id']}/chat/stream",
        headers=auth_headers,
        json={"message": "search brush error", "mode": "draft"},
    )
    assert chat.status_code == 200
    assert "event: tool_call" in chat.text
    assert '"tool_name": "kb_search"' in chat.text
    assert "https://example.com/brush" in chat.text
    assert "encrypted_secret" not in chat.text

    blocked_delete = client.delete(f"/api/tools/{tool_id}", headers=auth_headers)
    assert blocked_delete.status_code == 409
    assert blocked_delete.json()["detail"] == "Tool is in use"


def test_tool_cross_user_access_is_hidden(client, auth_headers):
    created = client.post(
        "/api/tools",
        headers=auth_headers,
        json={"type": "builtin_search", "name": "owner_search", "label": "Owner Search"},
    )
    assert created.status_code == 200
    tool_id = created.json()["tool"]["id"]
    _, user_headers = _register_regular_user(client, email="tool-user@example.com", name="Tool User")

    assert client.patch(f"/api/tools/{tool_id}", headers=user_headers, json={"label": "stolen"}).status_code == 404
    assert client.post(f"/api/tools/{tool_id}/test", headers=user_headers, json={"input": {"query": "x"}}).status_code == 404
    assert client.delete(f"/api/tools/{tool_id}", headers=user_headers).status_code == 404


def test_prompt_template_crud_and_builtin_copy(client, auth_headers):
    listed = client.get("/api/prompt-templates", headers=auth_headers)
    assert listed.status_code == 200
    builtin_items = [item for item in listed.json()["items"] if item["source"] == "builtin"]
    assert {item["title"] for item in builtin_items} >= {"通用结构", "任务执行", "角色扮演"}
    assert all(item["editable"] is False for item in builtin_items)
    assert all(item["id"].startswith("builtin:") for item in builtin_items)

    created = client.post(
        "/api/prompt-templates",
        headers=auth_headers,
        json={
            "title": "我的客服模板",
            "description": "售前客服",
            "content": "你是客服助手，请基于事实回答。",
            "category": "support",
            "tags": ["客服", "售前"],
        },
    )
    assert created.status_code == 200
    template = created.json()["template"]
    assert template["source"] == "mine"
    assert template["editable"] is True
    assert template["id"] == f"user:{template['db_id']}"
    assert template["tags"] == ["客服", "售前"]

    duplicate = client.post(
        "/api/prompt-templates",
        headers=auth_headers,
        json={"title": "我的客服模板", "content": "duplicate"},
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "Prompt template title already exists"

    patched = client.patch(
        f"/api/prompt-templates/{template['db_id']}",
        headers=auth_headers,
        json={"title": "我的客服模板 v2", "enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["template"]["title"] == "我的客服模板 v2"
    assert patched.json()["template"]["enabled"] is False

    hidden_disabled = client.get("/api/prompt-templates", headers=auth_headers)
    assert all(item.get("db_id") != template["db_id"] for item in hidden_disabled.json()["items"])
    with_disabled = client.get("/api/prompt-templates?include_disabled=true", headers=auth_headers)
    assert any(item.get("db_id") == template["db_id"] for item in with_disabled.json()["items"])

    copied = client.post(
        "/api/prompt-templates/copy-builtin",
        headers=auth_headers,
        json={"builtin_id": "general", "title": "通用结构副本"},
    )
    assert copied.status_code == 200
    assert copied.json()["template"]["source"] == "mine"
    assert copied.json()["template"]["title"] == "通用结构副本"

    missing_builtin = client.post(
        "/api/prompt-templates/copy-builtin",
        headers=auth_headers,
        json={"builtin_id": "missing"},
    )
    assert missing_builtin.status_code == 404

    deleted = client.delete(f"/api/prompt-templates/{template['db_id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_prompt_template_cross_user_access_is_hidden(client, auth_headers):
    created = client.post(
        "/api/prompt-templates",
        headers=auth_headers,
        json={"title": "Owner Only", "content": "owner-private-token"},
    )
    assert created.status_code == 200
    template_id = created.json()["template"]["db_id"]
    _, user_headers = _register_regular_user(client, email="template-user@example.com", name="Template User")

    listed = client.get("/api/prompt-templates?include_disabled=true", headers=user_headers)
    assert listed.status_code == 200
    assert "owner-private-token" not in listed.text
    assert client.patch(f"/api/prompt-templates/{template_id}", headers=user_headers, json={"title": "stolen"}).status_code == 404
    assert client.delete(f"/api/prompt-templates/{template_id}", headers=user_headers).status_code == 404


def test_agent_create_publish_and_workflow(client, auth_headers):
    created = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "合同助手",
            "avatar": "CT",
            "description": "帮助团队检索合同知识。",
            "system_prompt": "你是合同助手。",
            "knowledge_base_ids": [],
            "tool_ids": [],
        },
    )
    assert created.status_code == 200
    agent_id = created.json()["agent"]["id"]

    workflow = client.get(f"/api/agents/{agent_id}/workflow", headers=auth_headers)
    assert workflow.status_code == 200
    assert [node["type"] for node in workflow.json()["nodes"]] == ["Start", "Knowledge", "Tool", "LLM", "Answer"]

    published = client.post(f"/api/agents/{agent_id}/publish", headers=auth_headers)
    assert published.status_code == 200
    assert published.json()["version"]["version"] == 1


def test_knowledge_upload_and_chat_sse(client, auth_headers):
    kb = client.post(
        "/api/knowledge-bases",
        headers=auth_headers,
        json={"name": "产品知识", "description": "测试知识库"},
    )
    assert kb.status_code == 200
    kb_id = kb.json()["knowledge_base"]["id"]

    document = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={"filename": "guide.txt", "text": "回充失败时，请先检查充电座供电、摆放位置和路径遮挡。"},
    )
    assert document.status_code == 200

    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "客服助手", "system_prompt": "基于知识库回答。", "knowledge_base_ids": [kb_id]},
    )
    assert agent.status_code == 200
    agent_id = agent.json()["agent"]["id"]

    response = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "机器人不回充怎么办？"},
    )

    assert response.status_code == 200
    body = response.text
    assert "event: run_step" in body
    assert "event: rag_status" in body
    assert "event: token" in body
    assert "event: sources" in body
    assert "event: done" in body


def test_chat_stream_sanitizes_unexpected_runtime_errors(client, auth_headers, monkeypatch):
    from core.integrations.llm import OpenAICompatibleProvider

    def fail_stream(self, *args, **kwargs):
        raise RuntimeError("Model call failed: cannot connect to https://gateway.example/v1 with sk-secret-123")
        yield ""

    monkeypatch.setattr(OpenAICompatibleProvider, "chat_stream", fail_stream)
    agent = client.post("/api/agents", headers=auth_headers, json={"name": "Failing Agent"})
    response = client.post(
        f"/api/agents/{agent.json()['agent']['id']}/chat/stream",
        headers=auth_headers,
        json={"message": "trigger failure", "mode": "draft"},
    )

    assert response.status_code == 200
    errors = _sse_payloads(response.text, "error")
    assert errors
    assert errors[-1]["error_code"] == "model_provider_error"
    assert "Model call failed" in errors[-1]["message"]
    assert "sk-secret" not in response.text


def test_user_can_create_own_agent_but_cannot_edit_others(client, auth_headers):
    second, user_headers = _register_regular_user(client, email="user@example.com", name="User")

    members = client.get("/api/workspaces/members", headers=auth_headers)
    assert members.status_code == 200
    assert any(item["user"]["email"] == "user@example.com" and item["role"] == "user" for item in members.json()["items"])

    own = client.post(
        "/api/agents",
        headers=user_headers,
        json={"name": "用户自己的智能体"},
    )
    assert own.status_code == 200

    admin_agent = client.post("/api/agents", headers=auth_headers, json={"name": "管理员智能体"})
    forbidden = client.patch(
        f"/api/agents/{admin_agent.json()['agent']['id']}",
        headers=user_headers,
        json={"name": "越权修改"},
    )
    assert forbidden.status_code == 403


def test_agent_can_be_deleted_with_related_runtime_data(client, auth_headers):
    created = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "待删除智能体", "system_prompt": "你是临时测试智能体。"},
    )
    assert created.status_code == 200
    agent_id = created.json()["agent"]["id"]

    chatted = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "创建一条运行记录", "mode": "draft"},
    )
    assert chatted.status_code == 200
    assert client.get(f"/api/agents/{agent_id}/sessions", headers=auth_headers).json()["items"]

    deleted = client.delete(f"/api/agents/{agent_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    agents = client.get("/api/agents", headers=auth_headers)
    assert all(item["id"] != agent_id for item in agents.json()["items"])
    assert client.get(f"/api/agents/{agent_id}", headers=auth_headers).status_code == 404
    assert client.get(f"/api/agents/{agent_id}/sessions", headers=auth_headers).status_code == 404


def test_template_agent_cannot_be_deleted(client, auth_headers):
    agents = client.get("/api/agents", headers=auth_headers).json()["items"]
    template_id = next(item["id"] for item in agents if item["is_template"])

    deleted = client.delete(f"/api/agents/{template_id}", headers=auth_headers)

    assert deleted.status_code == 400
    assert "Template agents cannot be deleted" in deleted.json()["detail"]


def test_user_cannot_delete_other_users_agent(client, auth_headers):
    _, user_headers = _register_regular_user(client, email="deleter@example.com", name="Deleter")
    admin_agent = client.post("/api/agents", headers=auth_headers, json={"name": "管理员保留智能体"})
    agent_id = admin_agent.json()["agent"]["id"]

    forbidden = client.delete(f"/api/agents/{agent_id}", headers=user_headers)

    assert forbidden.status_code == 403
    assert client.get(f"/api/agents/{agent_id}", headers=auth_headers).status_code == 200


def test_workflow_validation(client, auth_headers):
    agents = client.get("/api/agents", headers=auth_headers).json()["items"]
    agent_id = agents[0]["id"]

    response = client.patch(
        f"/api/agents/{agent_id}/workflow",
        headers=auth_headers,
        json={"nodes": [{"id": "bad", "type": "Code", "name": "Code", "config": {}}]},
    )
    assert response.status_code == 400


def test_session_list_and_detail_after_chat(client, auth_headers):
    agents = client.get("/api/agents", headers=auth_headers).json()["items"]
    agent_id = agents[0]["id"]
    response = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "hello"},
    )
    assert response.status_code == 200

    sessions = client.get(f"/api/agents/{agent_id}/sessions", headers=auth_headers)
    assert sessions.status_code == 200
    assert sessions.json()["items"][0]["message_count"] == 2

    session_id = sessions.json()["items"][0]["id"]
    detail = client.get(f"/api/sessions/{session_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert [message["role"] for message in detail.json()["messages"]] == ["user", "assistant"]


def test_chat_can_continue_existing_session_and_feedback(client, auth_headers):
    agents = client.get("/api/agents", headers=auth_headers).json()["items"]
    agent_id = agents[0]["id"]

    first = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "first turn"},
    )
    assert first.status_code == 200
    sessions = client.get(f"/api/agents/{agent_id}/sessions", headers=auth_headers)
    session_id = sessions.json()["items"][0]["id"]

    second = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "second turn", "session_id": session_id},
    )
    assert second.status_code == 200

    detail = client.get(f"/api/sessions/{session_id}", headers=auth_headers)
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "user", "assistant"]

    assistant_message_id = messages[-1]["id"]
    feedback = client.post(
        f"/api/messages/{assistant_message_id}/feedback",
        headers=auth_headers,
        json={"rating": "positive", "comment": ""},
    )
    assert feedback.status_code == 200
    assert feedback.json()["feedback"]["rating"] == "positive"


def test_session_title_can_be_updated(client, auth_headers):
    agents = client.get("/api/agents", headers=auth_headers).json()["items"]
    agent_id = agents[0]["id"]
    client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "rename this session"},
    )
    session_id = client.get(f"/api/agents/{agent_id}/sessions", headers=auth_headers).json()["items"][0]["id"]

    renamed = client.patch(
        f"/api/sessions/{session_id}",
        headers=auth_headers,
        json={"title": "产品客服验收会话"},
    )

    assert renamed.status_code == 200
    assert renamed.json()["session"]["title"] == "产品客服验收会话"


def test_session_can_be_deleted_with_related_records(client, auth_headers):
    agents = client.get("/api/agents", headers=auth_headers).json()["items"]
    agent_id = agents[0]["id"]
    client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "delete this session"},
    )
    sessions = client.get(f"/api/agents/{agent_id}/sessions", headers=auth_headers).json()["items"]
    session_id = sessions[0]["id"]
    detail = client.get(f"/api/sessions/{session_id}", headers=auth_headers).json()
    assistant_message_id = detail["messages"][-1]["id"]
    feedback = client.post(
        f"/api/messages/{assistant_message_id}/feedback",
        headers=auth_headers,
        json={"rating": "positive", "comment": "remove with session"},
    )
    assert feedback.status_code == 200

    deleted = client.delete(f"/api/sessions/{session_id}", headers=auth_headers)

    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get(f"/api/sessions/{session_id}", headers=auth_headers).status_code == 404
    remaining = client.get(f"/api/agents/{agent_id}/sessions", headers=auth_headers)
    assert session_id not in [item["id"] for item in remaining.json()["items"]]
    hidden_feedback_message = client.post(
        f"/api/messages/{assistant_message_id}/feedback",
        headers=auth_headers,
        json={"rating": "positive", "comment": ""},
    )
    assert hidden_feedback_message.status_code == 404


def test_knowledge_documents_can_be_listed_and_deleted(client, auth_headers):
    kb = client.post(
        "/api/knowledge-bases",
        headers=auth_headers,
        json={"name": "删除测试知识库", "description": ""},
    )
    kb_id = kb.json()["knowledge_base"]["id"]
    document = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={"filename": "delete-me.txt", "text": "这是一段可以删除的测试知识。"},
    )
    document_id = document.json()["document"]["id"]

    documents = client.get(f"/api/knowledge-bases/{kb_id}/documents", headers=auth_headers)
    assert documents.status_code == 200
    assert documents.json()["items"][0]["filename"] == "delete-me.txt"
    assert documents.json()["items"][0]["chunk_count"] >= 1

    deleted = client.delete(f"/api/knowledge-bases/{kb_id}/documents/{document_id}", headers=auth_headers)
    assert deleted.status_code == 200

    empty = client.get(f"/api/knowledge-bases/{kb_id}/documents", headers=auth_headers)
    assert empty.status_code == 200
    assert empty.json()["items"] == []


def _document_after_upload(client, auth_headers, kb_id, document_id):
    """入库已异步化：上传请求立即返回 status=indexing，后台完成后再拉取最终落库状态。

    TestClient 会在返回响应前同步跑完 BackgroundTasks，因此上传调用返回后该文档已落库完成，
    重新 GET 即可拿到 indexed/failed 终态。"""
    listed = client.get(f"/api/knowledge-bases/{kb_id}/documents", headers=auth_headers)
    return next(item for item in listed.json()["items"] if item["id"] == document_id)


def test_knowledge_document_text_contract_returns_day03_metadata(client, auth_headers):
    kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Day03 Text KB"})
    kb_id = kb.json()["knowledge_base"]["id"]

    document = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={
            "title": "Warranty policy",
            "content": "Warranty terms and repair rules with day03-text-token.",
            "source_type": "text",
        },
    )

    assert document.status_code == 200
    payload = document.json()["document"]
    assert payload["filename"] == "Warranty policy"
    assert payload["title"] == "Warranty policy"
    assert payload["source_type"] == "text"
    assert "day03-text-token" in payload["text_preview"]
    assert payload["updated_at"]
    indexed = _document_after_upload(client, auth_headers, kb_id, payload["id"])
    assert indexed["status"] == "indexed"
    assert indexed["chunk_count"] >= 1
    assert indexed["error_message"] is None


def test_knowledge_file_ingestion_supports_txt_md_csv_pdf_and_docx(client, auth_headers):
    kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Day03 File KB"})
    kb_id = kb.json()["knowledge_base"]["id"]
    cases = [
        ("manual.txt", "text/plain", b"txt day03-file-token"),
        ("manual.md", "text/markdown", b"# Title\nmd day03-file-token"),
        ("data.csv", "text/csv", b"name,value\ncsv,day03-file-token"),
        ("manual.pdf", "application/pdf", b"%PDF-1.4\nBT (pdf day03-file-token) Tj ET"),
        (
            "manual.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx_bytes("docx day03-file-token"),
        ),
    ]

    for filename, content_type, raw in cases:
        document = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            headers=auth_headers,
            json={
                "filename": filename,
                "content_type": content_type,
                "content_base64": base64.b64encode(raw).decode("ascii"),
                "source_type": "file",
            },
        )

        assert document.status_code == 200
        payload = document.json()["document"]
        assert payload["filename"] == filename
        assert payload["source_type"] == "file"
        assert "day03-file-token" in payload["text_preview"]
        indexed = _document_after_upload(client, auth_headers, kb_id, payload["id"])
        assert indexed["status"] == "indexed"
        assert indexed["chunk_count"] >= 1
        assert indexed["error_message"] is None


def test_knowledge_document_strips_nul_characters(client, auth_headers):
    kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "NUL KB"})
    kb_id = kb.json()["knowledge_base"]["id"]

    text_document = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={
            "filename": "nul-text.txt",
            "content": "alpha\x00beta",
            "source_type": "text",
        },
    )
    assert text_document.status_code == 200
    text_payload = text_document.json()["document"]
    assert "\x00" not in text_payload["text_preview"]
    assert "alphabeta" in text_payload["text_preview"]
    assert _document_after_upload(client, auth_headers, kb_id, text_payload["id"])["status"] == "indexed"

    file_document = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={
            "filename": "nul-file.txt",
            "content_type": "text/plain",
            "content_base64": base64.b64encode(b"file\x00token").decode("ascii"),
            "source_type": "file",
        },
    )
    assert file_document.status_code == 200
    file_payload = file_document.json()["document"]
    assert "\x00" not in file_payload["text_preview"]
    assert "filetoken" in file_payload["text_preview"]
    assert _document_after_upload(client, auth_headers, kb_id, file_payload["id"])["status"] == "indexed"


def test_knowledge_file_ingestion_errors_are_statused_and_sanitized(client, auth_headers):
    kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Day03 Error KB"})
    kb_id = kb.json()["knowledge_base"]["id"]

    unsupported = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={
            "filename": "script.exe",
            "content_type": "application/octet-stream",
            "content_base64": base64.b64encode(b"not allowed").decode("ascii"),
            "source_type": "file",
        },
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["detail"] == "Unsupported file type"

    failed = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={
            "filename": "broken.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "content_base64": base64.b64encode(b"not a zip").decode("ascii"),
            "source_type": "file",
        },
    )
    assert failed.status_code == 422
    detail = failed.json()["detail"]
    assert detail["message"] == "Document text extraction failed"
    assert detail["document"]["status"] == "failed"
    assert detail["document"]["chunk_count"] == 0
    assert "Traceback" not in detail["document"]["error_message"]
    assert "sk-" not in detail["document"]["error_message"]

    documents = client.get(f"/api/knowledge-bases/{kb_id}/documents", headers=auth_headers)
    assert documents.status_code == 200
    assert documents.json()["items"][0]["status"] == "failed"


def test_regular_user_can_manage_own_knowledge_base(client, auth_headers):
    _, user_headers = _register_regular_user(client, email="kb-user@example.com", name="KB User")
    kb = client.post("/api/knowledge-bases", headers=user_headers, json={"name": "User KB"})

    assert kb.status_code == 200
    kb_id = kb.json()["knowledge_base"]["id"]

    document = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=user_headers,
        json={"filename": "user-kb.txt", "text": "regular user owned knowledge base token"},
    )
    assert document.status_code == 200
    document_id = document.json()["document"]["id"]

    listed = client.get(f"/api/knowledge-bases/{kb_id}/documents", headers=user_headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == document_id

    indexed = client.post(f"/api/knowledge-bases/{kb_id}/index", headers=user_headers)
    assert indexed.status_code == 200
    # 整库重建已异步化：接口返回 running job，后台完成后查询 job 终态。
    job = client.get(f"/api/knowledge/jobs/{indexed.json()['job_id']}", headers=user_headers)
    assert job.json()["status"] in {"succeeded", "unknown"}

    deleted = client.delete(f"/api/knowledge-bases/{kb_id}/documents/{document_id}", headers=user_headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}


def test_regular_user_cannot_write_other_users_knowledge_base(client, auth_headers):
    kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Owner KB"})
    kb_id = kb.json()["knowledge_base"]["id"]
    _, user_headers = _register_regular_user(client, email="kb-denied@example.com", name="KB Denied")

    upload = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=user_headers,
        json={"filename": "stolen.txt", "text": "should not write"},
    )
    index = client.post(f"/api/knowledge-bases/{kb_id}/index", headers=user_headers)

    assert upload.status_code == 403
    assert upload.json()["detail"] == "Knowledge base edit denied"
    assert index.status_code == 403


def test_agent_coze_settings_can_be_saved_and_published(client, auth_headers):
    created = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "Coze 风格助手", "system_prompt": "你是草稿版。"},
    )
    agent_id = created.json()["agent"]["id"]

    updated = client.patch(
        f"/api/agents/{agent_id}",
        headers=auth_headers,
        json={
            "suggested_questions": ["怎么开始？", "能检索知识库吗？"],
            "variables": [
                {"key": "city", "label": "城市", "type": "string", "required": True, "default_value": "杭州"},
                {"key": "vip", "label": "VIP", "type": "boolean", "required": False, "default_value": False},
            ],
            "memory": {"enabled": True, "strategy": "session_summary", "max_messages": 8},
            "tool_policy": {"mode": "auto", "allowed_tool_names": ["weather"]},
        },
    )
    assert updated.status_code == 200
    detail = updated.json()["agent"]
    assert detail["suggested_questions"] == ["怎么开始？", "能检索知识库吗？"]
    assert detail["variables"][0]["key"] == "city"
    assert detail["memory"]["enabled"] is True
    assert detail["tool_policy"]["allowed_tool_names"] == ["weather"]

    published = client.post(f"/api/agents/{agent_id}/publish", headers=auth_headers)
    assert published.status_code == 200
    snapshot = published.json()["version"]["snapshot"]
    assert snapshot["suggested_questions"] == ["怎么开始？", "能检索知识库吗？"]
    assert snapshot["memory"]["enabled"] is True


def test_user_publish_requires_admin_review_then_market_copy(client, auth_headers):
    user, user_headers = _register_regular_user(client, email="maker@example.com", name="Maker")

    created = client.post(
        "/api/agents",
        headers=user_headers,
        json={"name": "待审核智能体", "description": "普通用户提交的智能体", "system_prompt": "你是市场智能体。"},
    )
    agent_id = created.json()["agent"]["id"]

    submitted = client.post(f"/api/agents/{agent_id}/publish", headers=user_headers)
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "pending_review"
    assert submitted.json()["review_required"] is True

    market_before = client.get("/api/market/agents", headers=user_headers)
    assert all(item["id"] != agent_id for item in market_before.json()["items"])

    blocked_chat = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=user_headers,
        json={"message": "审核前不能正式对话", "mode": "published"},
    )
    assert blocked_chat.status_code == 200
    assert "当前智能体还没有发布版本" in blocked_chat.text

    forbidden = client.post(f"/api/admin/agent-reviews/{agent_id}/approve", headers=user_headers)
    assert forbidden.status_code == 403

    reviews = client.get("/api/admin/agent-reviews", headers=auth_headers)
    assert reviews.status_code == 200
    assert any(item["id"] == agent_id for item in reviews.json()["items"])

    approved = client.post(f"/api/admin/agent-reviews/{agent_id}/approve", headers=auth_headers)
    assert approved.status_code == 200
    assert approved.json()["agent"]["status"] == "published"

    market_after = client.get("/api/market/agents", headers=user_headers)
    assert any(item["id"] == agent_id for item in market_after.json()["items"])

    approved_chat = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=user_headers,
        json={"message": "审核后可以正式对话", "mode": "published"},
    )
    assert approved_chat.status_code == 200
    assert "event: done" in approved_chat.text

    copy = client.post(f"/api/market/agents/{agent_id}/copy", headers=user_headers)
    assert copy.status_code == 200
    copied = copy.json()["agent"]
    assert copied["name"].endswith("副本")
    assert copied["status"] == "draft"
    assert copied["created_by"] == user.json()["user"]["id"]


def test_published_mode_uses_snapshot_and_unpublished_errors(client, auth_headers):
    unpublished = client.post("/api/agents", headers=auth_headers, json={"name": "未发布助手"})
    unpublished_id = unpublished.json()["agent"]["id"]
    response = client.post(
        f"/api/agents/{unpublished_id}/chat/stream",
        headers=auth_headers,
        json={"message": "hi", "mode": "published"},
    )
    assert response.status_code == 200
    assert "当前智能体还没有发布版本" in response.text

    created = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "发布隔离助手", "system_prompt": "你是已发布快照。"},
    )
    agent_id = created.json()["agent"]["id"]
    publish = client.post(f"/api/agents/{agent_id}/publish", headers=auth_headers)
    assert publish.status_code == 200
    changed = client.patch(
        f"/api/agents/{agent_id}",
        headers=auth_headers,
        json={"system_prompt": "你是草稿改动。"},
    )
    assert changed.status_code == 200

    response = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "检查版本", "mode": "published"},
    )
    assert response.status_code == 200
    assert "已发布快照" in response.text
    assert "草稿改动" not in response.text


def test_chat_variables_and_memory_are_observable(client, auth_headers):
    created = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "变量记忆助手",
            "variables": [{"key": "city", "label": "城市", "type": "string", "default_value": "上海"}],
            "memory": {"enabled": True, "strategy": "session_summary", "max_messages": 6},
        },
    )
    agent_id = created.json()["agent"]["id"]

    first = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "第一轮", "mode": "draft", "variables": {"city": "杭州"}},
    )
    assert first.status_code == 200
    assert '"variables": {"city": "杭州"}' in first.text

    session_id = client.get(f"/api/agents/{agent_id}/sessions", headers=auth_headers).json()["items"][0]["id"]
    second = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "第二轮", "mode": "draft", "session_id": session_id},
    )
    assert second.status_code == 200
    assert '"used_memory": true' in second.text


def test_memory_profile_crud_normalizes_and_deletes_current_user_profile(client, auth_headers):
    created = client.post("/api/agents", headers=auth_headers, json={"name": "Memory Profile"})
    assert created.status_code == 200
    agent_id = created.json()["agent"]["id"]

    default_profile = client.get(f"/api/agents/{agent_id}/memory-profile", headers=auth_headers)
    assert default_profile.status_code == 200
    assert default_profile.json()["profile"] == {
        "agent_id": agent_id,
        "enabled": False,
        "summary": "",
        "facts": [],
        "preferences": {},
        "updated_at": None,
    }

    patched = client.patch(
        f"/api/agents/{agent_id}/memory-profile",
        headers=auth_headers,
        json={
            "enabled": True,
            "summary": "x" * 4100,
            "facts": [f"fact-{index}" for index in range(60)] + ["   "],
            "preferences": {
                "language": "zh-CN",
                "answer_style": "concise",
                "bad_object": {"nested": "value"},
                "allowed_list": ["a", 1, True, {"skip": True}],
            },
        },
    )

    assert patched.status_code == 200
    profile = patched.json()["profile"]
    assert profile["enabled"] is True
    assert len(profile["summary"]) == 4000
    assert len(profile["facts"]) == 50
    for index, fact in enumerate(profile["facts"]):
        assert fact["text"] == f"fact-{index}"
        assert len(fact["id"]) == 8
        assert fact["source"] == "user"
        assert fact["score"] == 1.0
        assert "created_at" in fact
    assert profile["preferences"]["language"] == "zh-CN"
    assert profile["preferences"]["answer_style"] == "concise"
    assert "bad_object" not in profile["preferences"]
    assert profile["preferences"]["allowed_list"] == ["a", 1, True]
    assert profile["updated_at"]

    deleted = client.delete(f"/api/agents/{agent_id}/memory-profile", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}

    after_delete = client.get(f"/api/agents/{agent_id}/memory-profile", headers=auth_headers)
    assert after_delete.status_code == 200
    assert after_delete.json()["profile"]["enabled"] is False
    assert after_delete.json()["profile"]["summary"] == ""


def test_memory_profile_is_scoped_to_current_user_and_agent(client, auth_headers):
    owner_agent = client.post("/api/agents", headers=auth_headers, json={"name": "Owner Memory"})
    assert owner_agent.status_code == 200
    owner_agent_id = owner_agent.json()["agent"]["id"]
    client.patch(
        f"/api/agents/{owner_agent_id}/memory-profile",
        headers=auth_headers,
        json={
            "enabled": True,
            "summary": "owner-only-memory-token",
            "facts": ["owner fact"],
            "preferences": {"language": "zh-CN"},
        },
    )

    _, user_headers = _register_regular_user(client, email="memory-user@example.com", name="Memory User")

    hidden = client.get(f"/api/agents/{owner_agent_id}/memory-profile", headers=user_headers)
    assert hidden.status_code == 403

    user_agent = client.post("/api/agents", headers=user_headers, json={"name": "User Memory"})
    assert user_agent.status_code == 200
    user_agent_id = user_agent.json()["agent"]["id"]
    user_default = client.get(f"/api/agents/{user_agent_id}/memory-profile", headers=user_headers)
    assert user_default.status_code == 200
    assert user_default.json()["profile"]["summary"] == ""

    user_patch = client.patch(
        f"/api/agents/{user_agent_id}/memory-profile",
        headers=user_headers,
        json={"enabled": True, "summary": "user-only-memory-token"},
    )
    assert user_patch.status_code == 200

    owner_missing = client.get(f"/api/agents/{user_agent_id}/memory-profile", headers=auth_headers)
    assert owner_missing.status_code == 200
    assert owner_missing.json()["profile"]["summary"] == ""


def test_chat_emits_memory_used_and_injects_enabled_profile_only(client, auth_headers):
    created = client.post("/api/agents", headers=auth_headers, json={"name": "Runtime Profile Memory"})
    assert created.status_code == 200
    agent_id = created.json()["agent"]["id"]
    patched = client.patch(
        f"/api/agents/{agent_id}/memory-profile",
        headers=auth_headers,
        json={
            "enabled": True,
            "summary": "profile-runtime-token",
            "facts": ["The user owns an S10 sweeper."],
            "preferences": {"language": "zh-CN", "answer_style": "concise"},
        },
    )
    assert patched.status_code == 200

    enabled_chat = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "use my memory", "mode": "draft"},
    )
    assert enabled_chat.status_code == 200
    memory_events = _sse_payloads(enabled_chat.text, "memory_used")
    assert memory_events
    assert memory_events[0] == {
        "enabled": True,
        "profile_found": True,
        "summary_used": True,
        "facts_count": 1,
        "facts_total": 1,
        "facts_recalled": 0,
        "preferences_keys": ["answer_style", "language"],
        "session_summary_used": False,
    }
    assert '"used_profile_memory": true' in enabled_chat.text
    assert "profile-runtime-token" in enabled_chat.text
    assert "event: done" in enabled_chat.text

    disabled = client.patch(
        f"/api/agents/{agent_id}/memory-profile",
        headers=auth_headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    disabled_chat = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "do not use memory", "mode": "draft"},
    )
    assert disabled_chat.status_code == 200
    disabled_events = _sse_payloads(disabled_chat.text, "memory_used")
    assert disabled_events[0]["enabled"] is False
    assert disabled_events[0]["profile_found"] is True
    assert disabled_events[0]["summary_used"] is False
    assert disabled_events[0]["facts_count"] == 0
    assert disabled_events[0]["preferences_keys"] == []
    assert '"used_profile_memory": false' in disabled_chat.text


def test_memory_used_reports_session_summary_without_merging_long_term_memory(client, auth_headers):
    created = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "Session And Long Term Memory",
            "memory": {"enabled": True, "strategy": "session_summary", "max_messages": 6},
        },
    )
    assert created.status_code == 200
    agent_id = created.json()["agent"]["id"]
    first = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "first turn", "mode": "draft"},
    )
    assert first.status_code == 200
    session_id = client.get(f"/api/agents/{agent_id}/sessions", headers=auth_headers).json()["items"][0]["id"]

    client.patch(
        f"/api/agents/{agent_id}/memory-profile",
        headers=auth_headers,
        json={"enabled": True, "summary": "separate-long-term-token"},
    )
    second = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "second turn", "mode": "draft", "session_id": session_id},
    )
    assert second.status_code == 200
    events = _sse_payloads(second.text, "memory_used")
    assert events[0]["enabled"] is True
    assert events[0]["profile_found"] is True
    assert events[0]["summary_used"] is True
    assert events[0]["session_summary_used"] is False
    assert '"used_memory": true' in second.text
    assert '"used_profile_memory": true' in second.text


def test_model_management_and_multimodal_validation(client, auth_headers):
    models = client.get("/api/models", headers=auth_headers)
    assert models.status_code == 200
    default_model_id = models.json()["items"][0]["id"]

    created_model = client.post(
        "/api/admin/models",
        headers=auth_headers,
        json={
            "provider": "openai-compatible",
            "model_name": "vision-test-model",
            "display_name": "Vision Test",
            "supports_text": True,
            "supports_image": True,
            "supports_document": True,
            "max_context": 128000,
            "default_temperature": 0.2,
            "enabled": True,
        },
    )
    assert created_model.status_code == 200
    vision_model_id = created_model.json()["model"]["id"]

    disabled = client.patch(
        f"/api/admin/models/{vision_model_id}",
        headers=auth_headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    enabled_only = client.get("/api/models", headers=auth_headers).json()["items"]
    assert all(item["id"] != vision_model_id for item in enabled_only)
    all_models = client.get("/api/models?include_disabled=true", headers=auth_headers).json()["items"]
    assert any(item["id"] == vision_model_id and item["enabled"] is False for item in all_models)

    blocked_create = client.post("/api/agents", headers=auth_headers, json={"name": "Disabled Model", "model_id": vision_model_id})
    assert blocked_create.status_code == 400
    assert blocked_create.json()["detail"] == "Model is not available"
    patch_target = client.post("/api/agents", headers=auth_headers, json={"name": "Patch Disabled", "model_id": default_model_id})
    blocked_update = client.patch(
        f"/api/agents/{patch_target.json()['agent']['id']}",
        headers=auth_headers,
        json={"model_id": vision_model_id},
    )
    assert blocked_update.status_code == 400
    assert blocked_update.json()["detail"] == "Model is not available"

    reenabled = client.patch(
        f"/api/admin/models/{vision_model_id}",
        headers=auth_headers,
        json={"enabled": True},
    )
    assert reenabled.status_code == 200
    _, user_headers = _register_regular_user(client, email="model-user@example.com", name="Model User")
    forbidden = client.get("/api/models?include_disabled=true", headers=user_headers)
    assert forbidden.status_code == 403
    forbidden_create = client.post(
        "/api/admin/models",
        headers=user_headers,
        json={
            "provider": "openai-compatible",
            "model_name": "user-created-model",
            "display_name": "User Created",
        },
    )
    assert forbidden_create.status_code == 403
    forbidden_update = client.patch(
        f"/api/admin/models/{default_model_id}",
        headers=user_headers,
        json={"display_name": "User Edited"},
    )
    assert forbidden_update.status_code == 403

    image = client.post(
        "/api/uploads",
        headers=auth_headers,
        json={
            "filename": "fault.png",
            "content_type": "image/png",
            "content_base64": base64.b64encode(b"fake-png").decode("ascii"),
        },
    )
    assert image.status_code == 200
    upload_id = image.json()["upload"]["id"]

    text_agent = client.post("/api/agents", headers=auth_headers, json={"name": "Text Only", "model_id": default_model_id})
    text_response = client.post(
        f"/api/agents/{text_agent.json()['agent']['id']}/chat/stream",
        headers=auth_headers,
        json={"message": "look", "mode": "draft", "attachments": [{"id": upload_id, "type": "image", "mime_type": "image/png"}]},
    )
    assert text_response.status_code == 200
    assert "Selected model does not support image input" not in text_response.text
    assert "event: done" in text_response.text

    vision_agent = client.post("/api/agents", headers=auth_headers, json={"name": "Vision", "model_id": vision_model_id})
    accepted = client.post(
        f"/api/agents/{vision_agent.json()['agent']['id']}/chat/stream",
        headers=auth_headers,
        json={"message": "look", "mode": "draft", "attachments": [{"id": upload_id, "type": "image", "mime_type": "image/png"}]},
    )
    assert accepted.status_code == 200
    assert "event: done" in accepted.text


def test_custom_model_can_be_deleted(client, auth_headers):
    model = _create_custom_model(client, auth_headers, "delete-unused-model")

    deleted = client.delete(f"/api/admin/models/{model['id']}", headers=auth_headers)

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    all_models = client.get("/api/models?include_disabled=true", headers=auth_headers).json()["items"]
    assert all(item["id"] != model["id"] for item in all_models)


def test_user_cannot_delete_model(client, auth_headers):
    model = _create_custom_model(client, auth_headers, "delete-forbidden-model")
    _, user_headers = _register_regular_user(client, email="model-delete-user@example.com", name="Model Delete User")

    forbidden = client.delete(f"/api/admin/models/{model['id']}", headers=user_headers)

    assert forbidden.status_code == 403
    body = forbidden.json()
    assert body.get("detail") == "Admin role required" or body.get("message") == "Admin role required"


def test_delete_missing_model_returns_404(client, auth_headers):
    missing = client.delete("/api/admin/models/999999", headers=auth_headers)

    assert missing.status_code == 404
    assert missing.json()["detail"] == "Model not found"


def test_default_model_cannot_be_deleted(client, auth_headers):
    default_model = client.get("/api/models", headers=auth_headers).json()["items"][0]

    protected = client.delete(f"/api/admin/models/{default_model['id']}", headers=auth_headers)

    assert protected.status_code == 409
    assert protected.json()["detail"] == "Model is protected"


def test_last_enabled_text_model_cannot_be_deleted(client, auth_headers):
    custom = _create_custom_model(client, auth_headers, "last-enabled-text-model")
    for model in client.get("/api/models?include_disabled=true", headers=auth_headers).json()["items"]:
        if model["id"] != custom["id"] and model["enabled"] and model["supports_text"]:
            disabled = client.patch(f"/api/admin/models/{model['id']}", headers=auth_headers, json={"enabled": False})
            assert disabled.status_code == 200

    protected = client.delete(f"/api/admin/models/{custom['id']}", headers=auth_headers)

    assert protected.status_code == 409
    assert protected.json()["detail"] == "Model is protected"


def test_model_used_by_current_agent_cannot_be_deleted(client, auth_headers):
    model = _create_custom_model(client, auth_headers, "delete-agent-used-model")
    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "Model Bound Agent", "model_id": model["id"]},
    )
    assert agent.status_code == 200

    blocked = client.delete(f"/api/admin/models/{model['id']}", headers=auth_headers)

    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Model is in use"


def test_model_used_by_published_snapshot_cannot_be_deleted(client, auth_headers):
    default_model = client.get("/api/models", headers=auth_headers).json()["items"][0]
    model = _create_custom_model(client, auth_headers, "delete-snapshot-used-model")
    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "Snapshot Bound Agent", "model_id": model["id"]},
    )
    assert agent.status_code == 200
    agent_id = agent.json()["agent"]["id"]
    published = client.post(f"/api/agents/{agent_id}/publish", headers=auth_headers)
    assert published.status_code == 200
    patched = client.patch(
        f"/api/agents/{agent_id}",
        headers=auth_headers,
        json={"model_id": default_model["id"]},
    )
    assert patched.status_code == 200

    blocked = client.delete(f"/api/admin/models/{model['id']}", headers=auth_headers)

    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "Model is in use"


def test_upload_rejects_unsupported_type_and_large_file(client, auth_headers):
    unsupported = client.post(
        "/api/uploads",
        headers=auth_headers,
        json={
            "filename": "payload.exe",
            "content_type": "application/octet-stream",
            "content_base64": base64.b64encode(b"not-supported").decode("ascii"),
        },
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["detail"] == "Only image and document uploads are supported"

    too_large = client.post(
        "/api/uploads",
        headers=auth_headers,
        json={
            "filename": "huge.txt",
            "content_type": "text/plain",
            "content_base64": base64.b64encode(b"x" * (8 * 1024 * 1024 + 1)).decode("ascii"),
        },
    )
    assert too_large.status_code == 400
    assert too_large.json()["detail"] == "Upload file cannot exceed 8MB"


def test_upload_strips_nul_characters_from_document_text(client, auth_headers):
    upload = client.post(
        "/api/uploads",
        headers=auth_headers,
        json={
            "filename": "nul.txt",
            "content_type": "text/plain",
            "content_base64": base64.b64encode(b"hello\x00world").decode("ascii"),
        },
    )

    assert upload.status_code == 200
    payload = upload.json()["upload"]
    assert payload["type"] == "document"
    assert "\x00" not in payload["text_preview"]
    assert payload["text_preview"] == "helloworld"


def test_chat_rejects_upload_from_another_workspace(client, auth_headers):
    from core.db.models import User, Workspace, WorkspaceMember
    from core.db.session import SessionLocal
    from core.security.auth import create_access_token, hash_password

    db = SessionLocal()
    try:
        other_user = User(email="other-owner@example.com", name="Other Owner", password_hash=hash_password("password123"))
        other_workspace = Workspace(name="Other Workspace", slug="other-workspace")
        db.add_all([other_user, other_workspace])
        db.flush()
        db.add(WorkspaceMember(workspace_id=other_workspace.id, user_id=other_user.id, role="admin"))
        db.commit()
        db.refresh(other_user)
        db.refresh(other_workspace)
        other_token = create_access_token({"sub": str(other_user.id), "workspace_id": other_workspace.id})
    finally:
        db.close()

    other_headers = {"Authorization": f"Bearer {other_token}"}
    upload = client.post(
        "/api/uploads",
        headers=other_headers,
        json={
            "filename": "other.txt",
            "content_type": "text/plain",
            "content_base64": base64.b64encode(b"other workspace text").decode("ascii"),
        },
    )
    assert upload.status_code == 200

    agent = client.post("/api/agents", headers=auth_headers, json={"name": "Workspace Isolation"})
    response = client.post(
        f"/api/agents/{agent.json()['agent']['id']}/chat/stream",
        headers=auth_headers,
        json={
            "message": "use other upload",
            "mode": "draft",
            "attachments": [{"id": upload.json()["upload"]["id"], "type": "document", "mime_type": "text/plain"}],
        },
    )
    assert response.status_code == 200
    assert "Upload not found or not accessible" in response.text
    assert "event: done" not in response.text


def test_document_attachment_and_rag_toggle(client, auth_headers):
    kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "RAG KB"})
    kb_id = kb.json()["knowledge_base"]["id"]
    client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={"filename": "kb.txt", "text": "知识库里有唯一词 rag-source-token"},
    )
    upload = client.post(
        "/api/uploads",
        headers=auth_headers,
        json={
            "filename": "note.txt",
            "content_type": "text/plain",
            "content_base64": base64.b64encode("附件里有唯一词 attachment-token".encode("utf-8")).decode("ascii"),
        },
    )
    assert upload.status_code == 200
    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "Attachment RAG",
            "knowledge_base_ids": [kb_id],
            "rag": {"enabled_by_default": True, "top_k": 4},
        },
    )
    assert agent.status_code == 200
    agent_id = agent.json()["agent"]["id"]

    response = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={
            "message": "请回答",
            "mode": "draft",
            "rag_enabled": False,
            "attachments": [{"id": upload.json()["upload"]["id"], "type": "document", "mime_type": "text/plain"}],
        },
    )
    assert response.status_code == 200
    assert "event: rag_status" in response.text
    assert '"enabled": false' in response.text
    assert '"reason": "disabled"' in response.text
    assert '"rag_enabled": false' in response.text
    assert "event: sources" not in response.text
    assert "attachment-token" in response.text


def test_agent_web_search_setting_emits_status_and_injects_sources(client, auth_headers):
    from core.services import web_search

    def fake_search(query, *, top_k=None, timeout_seconds=None):
        return {
            "query": query,
            "provider": "fake",
            "latency_ms": 11,
            "items": [
                {
                    "title": "Lingshu Search Result",
                    "url": "https://example.com/lingshu-search",
                    "snippet": "A current web result for Lingshu Agent.",
                }
            ],
        }

    web_search.search_web = fake_search
    agent = client.post("/api/agents", headers=auth_headers, json={"name": "Search Agent", "tool_policy": {"web_search_enabled": True}})
    agent_id = agent.json()["agent"]["id"]

    response = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "search current lingshu", "mode": "draft", "rag_enabled": False},
    )

    assert response.status_code == 200
    status_events = _sse_payloads(response.text, "search_status")
    assert status_events
    assert status_events[0]["enabled"] is True
    assert status_events[-1]["matched_results"] == 1
    assert status_events[-1]["items"][0]["url"] == "https://example.com/lingshu-search"
    assert "event: sources" in response.text
    assert "Web search results for this turn" in response.text
    run_steps = _sse_payloads(response.text, "run_step")
    start_step = next(step for step in run_steps if step["node_type"] == "Start")
    assert start_step["output"]["search_enabled"] is True
    llm_step = next(step for step in run_steps if step["node_type"] == "LLM")
    assert llm_step["output"]["search_enabled"] is True
    assert llm_step["output"]["search_result_count"] == 1


def test_agent_search_setting_is_persisted_and_published_snapshot_is_fixed(client, auth_headers):
    created = client.post('/api/agents', headers=auth_headers, json={'name': 'Search config', 'tool_policy': {'web_search_enabled': True}})
    aid = created.json()['agent']['id']
    assert client.get(f'/api/agents/{aid}', headers=auth_headers).json()['agent']['tool_policy']['web_search_enabled'] is True
    client.post(f'/api/agents/{aid}/publish', headers=auth_headers)
    client.patch(f'/api/agents/{aid}', headers=auth_headers, json={'tool_policy': {'web_search_enabled': False}})
    assert client.get(f'/api/agents/{aid}', headers=auth_headers).json()['agent']['tool_policy']['web_search_enabled'] is False
    from core.db.models import Agent, AgentVersion
    from core.db.session import SessionLocal
    with SessionLocal() as db:
        agent = db.get(Agent, aid)
        assert db.get(AgentVersion, agent.published_version_id).snapshot['tool_policy']['web_search_enabled'] is True


def test_chat_web_search_off_does_not_call_provider(client, auth_headers):
    from core.services import web_search

    def forbidden_search(*args, **kwargs):
        raise AssertionError("search provider should not be called")

    web_search.search_web = forbidden_search
    agent = client.post("/api/agents", headers=auth_headers, json={"name": "Search Off Agent", "tool_policy": {"web_search_enabled": True}})
    agent_id = agent.json()["agent"]["id"]

    response = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "do not search", "mode": "draft", "search_enabled": False, "rag_enabled": False},
    )

    assert response.status_code == 200
    status_events = _sse_payloads(response.text, "search_status")
    assert status_events
    assert status_events[0]["enabled"] is False
    assert status_events[0]["reason"] == "not_requested"
    assert "event: sources" not in response.text


def test_chat_model_without_document_support_rejects_document_attachment(client, auth_headers):
    text_model = _create_user_model(
        client,
        auth_headers,
        "Text Only Attachment",
        chat_model="deepseek-chat",
        supports_image=False,
        supports_document=False,
        is_default=True,
    )
    upload = client.post(
        "/api/uploads",
        headers=auth_headers,
        json={
            "filename": "text-only-note.txt",
            "content_type": "text/plain",
            "content_base64": base64.b64encode("文档附件里的唯一词 text-only-attachment-token".encode("utf-8")).decode("ascii"),
        },
    )
    assert upload.status_code == 200
    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "Text Only Attachment",
            "user_model_config_id": text_model["id"],
        },
    )
    agent_id = agent.json()["agent"]["id"]

    response = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={
            "message": "读取文档附件",
            "mode": "draft",
            "attachments": [{"id": upload.json()["upload"]["id"], "type": "document", "mime_type": "text/plain"}],
        },
    )

    assert response.status_code == 200
    assert "Selected model does not support document input" in response.text
    assert "text-only-attachment-token" not in response.text


def test_text_only_chat_model_can_use_backend_default_rag(client, auth_headers):
    text_model = _create_user_model(
        client,
        auth_headers,
        "Text Only Chat",
        chat_model="deepseek-chat",
        supports_document=False,
        is_default=True,
    )
    kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Text Only RAG KB"})
    kb_id = kb.json()["knowledge_base"]["id"]
    client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={"filename": "text-only.txt", "text": "text-only-rag-source-token"},
    )
    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "Text Only RAG",
            "user_model_config_id": text_model["id"],
            "knowledge_base_ids": [kb_id],
            "rag": {"enabled_by_default": True, "top_k": 4},
        },
    )
    agent_id = agent.json()["agent"]["id"]

    response = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "should not retrieve", "mode": "draft", "rag_enabled": True},
    )

    assert response.status_code == 200
    assert "event: rag_status" in response.text
    assert '"enabled": true' in response.text
    assert '"rag_enabled": true' in response.text
    assert '"rag_model": "environment"' in response.text
    assert "event: sources" in response.text


@pytest.mark.parametrize('source', ['user', 'system'])
@pytest.mark.parametrize('mode', ['draft', 'published'])
def test_chat_model_selection_is_request_scoped(client, auth_headers, monkeypatch, source, mode):
    from core.integrations.llm import OpenAICompatibleProvider
    override = (_create_user_model(client, auth_headers, 'Chat choice', chat_model='override-chat') if source == 'user'
                else _create_custom_model(client, auth_headers, 'override-chat'))
    agent = client.post('/api/agents', headers=auth_headers, json={'name': 'Model picker'}).json()['agent']
    agent_id = agent['id']
    client.post(f'/api/agents/{agent_id}/publish', headers=auth_headers)
    before = client.get(f'/api/agents/{agent_id}', headers=auth_headers).json()['agent']
    packets = []
    def stream(self, messages, **kwargs):
        packets.append(kwargs)
        yield {'type': 'content', 'text': 'Selected model answer'}
    monkeypatch.setattr(OpenAICompatibleProvider, 'chat_stream', stream)
    response = client.post(f'/api/agents/{agent_id}/chat/stream', headers=auth_headers,
        json={'message': 'Use the selected model', 'mode': mode, 'rag_enabled': False,
              'model_override': {'source': source, 'id': override['id']}})
    assert response.status_code == 200
    assert not _sse_payloads(response.text, 'error')
    assert packets[0]['model'] == 'override-chat'
    assert bool(packets[0]['runtime_config']) == (source == 'user')
    llm_step = next(step for step in _sse_payloads(response.text, 'run_step') if step['node_type'] == 'LLM')
    assert llm_step['output']['model'] == 'override-chat'
    after = client.get(f'/api/agents/{agent_id}', headers=auth_headers).json()['agent']
    for key in ['model', 'model_id', 'user_model_config_id', 'system_prompt', 'published_version_id', 'mcp_bindings', 'skill_bindings']:
        assert after[key] == before[key]
    default = client.post(f'/api/agents/{agent_id}/chat/stream', headers=auth_headers,
        json={'message': 'Use default again', 'mode': mode, 'rag_enabled': False})
    assert not _sse_payloads(default.text, 'error')
    assert packets[-1]['model'] == before['model']


def test_chat_model_selection_rejects_inaccessible_disabled_and_invalid_models(client, auth_headers):
    private = _create_user_model(client, auth_headers, 'Private model')
    disabled = _create_custom_model(client, auth_headers, 'disabled-chat', enabled=False)
    _, other_headers = _register_regular_user(client, email='picker-other@example.com', name='Other')
    agent_id = client.post('/api/agents', headers=other_headers, json={'name': 'Other picker'}).json()['agent']['id']
    for choice in [{'source': 'user', 'id': private['id']}, {'source': 'system', 'id': disabled['id']},
                   {'source': 'system', 'id': 999999}, {'source': 'user', 'id': 999999}]:
        response = client.post(f'/api/agents/{agent_id}/chat/stream', headers=other_headers,
            json={'message': 'Must be rejected', 'model_override': choice})
        assert response.status_code == 400
        assert 'sk-' not in response.text
    for choice in [{'source': 'user', 'id': 0}, {'source': 'http', 'id': private['id']}, {'source': 'user'}]:
        response = client.post(f'/api/agents/{agent_id}/chat/stream', headers=other_headers,
            json={'message': 'Invalid selection', 'model_override': choice})
        assert response.status_code == 422
    sessions = client.get(f'/api/agents/{agent_id}/sessions', headers=other_headers)
    assert sessions.status_code == 200
    assert sessions.json()['items'] == []


def test_chat_thinking_status_respects_model_reasoning_capability(client, auth_headers):
    unsupported_model = _create_user_model(
        client,
        auth_headers,
        "No Reasoning Model",
        chat_model="no-reasoning-model",
        supports_reasoning=False,
        reasoning_type="none",
        reasoning_label="不支持",
    )
    unsupported_agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "No Reasoning Agent", "user_model_config_id": unsupported_model["id"]},
    )
    unsupported_response = client.post(
        f"/api/agents/{unsupported_agent.json()['agent']['id']}/chat/stream",
        headers=auth_headers,
        json={"message": "需要深度思考", "mode": "draft", "thinking_enabled": True},
    )

    assert unsupported_response.status_code == 200
    unsupported_events = _sse_payloads(unsupported_response.text, "thinking_status")
    assert unsupported_events
    assert unsupported_events[0]["enabled"] is False
    assert unsupported_events[0]["reason"] == "model_not_supported"
    assert unsupported_events[0]["type"] == "none"
    run_steps = _sse_payloads(unsupported_response.text, "run_step")
    llm_step = next(step for step in run_steps if step["node_type"] == "LLM")
    assert llm_step["output"]["thinking_enabled"] is False
    assert llm_step["output"]["thinking_type"] == "none"

    prompt_model = _create_user_model(
        client,
        auth_headers,
        "Prompt Reasoning Model",
        chat_model="prompt-reasoning-model",
        supports_reasoning=True,
        reasoning_type="prompt",
        reasoning_label="提示词增强",
    )
    prompt_agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "Prompt Reasoning Agent", "user_model_config_id": prompt_model["id"]},
    )
    prompt_response = client.post(
        f"/api/agents/{prompt_agent.json()['agent']['id']}/chat/stream",
        headers=auth_headers,
        json={"message": "需要深度思考", "mode": "draft", "thinking_enabled": True},
    )

    assert prompt_response.status_code == 200
    prompt_events = _sse_payloads(prompt_response.text, "thinking_status")
    assert prompt_events
    assert prompt_events[0]["enabled"] is True
    assert prompt_events[0]["type"] == "prompt"
    assert prompt_events[0]["label"] == "提示词增强"
    assert "本轮已开启深度思考模式" in prompt_response.text


def test_publish_snapshot_includes_model_and_rag(client, auth_headers):
    model = client.get("/api/models", headers=auth_headers).json()["items"][0]
    created = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "Snapshot Model",
            "model_id": model["id"],
            "rag": {"enabled_by_default": False, "top_k": 2},
            "memory": {"enabled": True, "strategy": "session_summary", "max_messages": 5},
        },
    )
    agent_id = created.json()["agent"]["id"]
    published = client.post(f"/api/agents/{agent_id}/publish", headers=auth_headers)

    assert published.status_code == 200
    snapshot = published.json()["version"]["snapshot"]
    assert snapshot["model_id"] == model["id"]
    assert snapshot["model"] == model["model_name"]
    assert snapshot["rag"]["enabled_by_default"] is False
    assert snapshot["rag"]["top_k"] == 2
    assert snapshot["rag"]["dense_top_k"] >= 1
    assert snapshot["memory"]["enabled"] is True


def test_published_chat_uses_snapshot_model_rag_and_memory_after_draft_changes(client, auth_headers):
    default_model = client.get("/api/models", headers=auth_headers).json()["items"][0]
    vision_model = client.post(
        "/api/admin/models",
        headers=auth_headers,
        json={
            "provider": "openai-compatible",
            "model_name": "snapshot-vision-model",
            "display_name": "Snapshot Vision",
            "supports_text": True,
            "supports_image": True,
            "supports_document": True,
            "max_context": 128000,
            "default_temperature": 0.2,
            "enabled": True,
        },
    )
    assert vision_model.status_code == 200
    vision_model_id = vision_model.json()["model"]["id"]
    kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Snapshot KB"})
    kb_id = kb.json()["knowledge_base"]["id"]
    document = client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth_headers,
        json={"filename": "snapshot.txt", "text": "snapshot-rag-source-token"},
    )
    assert document.status_code == 200
    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "Snapshot Runtime",
            "model_id": default_model["id"],
            "knowledge_base_ids": [kb_id],
            "rag": {"enabled_by_default": False, "top_k": 1},
            "memory": {"enabled": True, "strategy": "session_summary", "max_messages": 6},
        },
    )
    agent_id = agent.json()["agent"]["id"]
    published = client.post(f"/api/agents/{agent_id}/publish", headers=auth_headers)
    assert published.status_code == 200
    patched = client.patch(
        f"/api/agents/{agent_id}",
        headers=auth_headers,
        json={
            "model_id": vision_model_id,
            "rag": {"enabled_by_default": True, "top_k": 4},
            "memory": {"enabled": False, "strategy": "session_summary", "max_messages": 6},
        },
    )
    assert patched.status_code == 200

    first = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "first published turn", "mode": "published"},
    )
    assert first.status_code == 200
    assert '"rag_enabled": false' in first.text
    assert "event: sources" not in first.text

    session_id = client.get(f"/api/agents/{agent_id}/sessions", headers=auth_headers).json()["items"][0]["id"]
    second = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "second published turn", "mode": "published", "session_id": session_id},
    )
    assert second.status_code == 200
    assert '"used_memory": true' in second.text

    upload = client.post(
        "/api/uploads",
        headers=auth_headers,
        json={
            "filename": "snapshot.png",
            "content_type": "image/png",
            "content_base64": base64.b64encode(b"fake-png").decode("ascii"),
        },
    )
    response = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={
            "message": "published image should use frozen text model",
            "mode": "published",
            "attachments": [{"id": upload.json()["upload"]["id"], "type": "image", "mime_type": "image/png"}],
        },
    )
    assert response.status_code == 200
    assert "Selected model does not support image input" not in response.text
    assert "event: done" in response.text


def test_chat_preserves_recent_roles_when_summary_is_disabled(client, auth_headers, monkeypatch):
    from core.integrations.llm import OpenAICompatibleProvider
    captured = []
    def stream(self, messages, **kwargs):
        captured.append(messages)
        self.last_chat_mock = True
        yield {'type': 'content', 'text': 'The project is CONTEXT_PROJECT.'}
    monkeypatch.setattr(OpenAICompatibleProvider, 'chat_stream', stream)
    agent = client.post('/api/agents', headers=auth_headers, json={'name': 'Recent context', 'memory': {'enabled': False, 'max_messages': 12}}).json()['agent']
    first = client.post(f"/api/agents/{agent['id']}/chat/stream", headers=auth_headers,
                        json={'message': 'My project is CONTEXT_PROJECT.', 'mode': 'draft'})
    assert first.status_code == 200
    session_id = _sse_payloads(first.text, 'done')[0]['session_id']
    second = client.post(f"/api/agents/{agent['id']}/chat/stream", headers=auth_headers,
        json={'message': 'Continue that project.', 'mode': 'draft', 'session_id': session_id})
    assert second.status_code == 200
    assert 'event: done' in second.text
    assert [message['role'] for message in captured[1]] == ['system', 'user', 'assistant', 'user']
    assert captured[1][1]['content'] == 'My project is CONTEXT_PROJECT.'
    assert captured[1][-1]['content'] == 'Continue that project.'
    assert 'CONTEXT_PROJECT' not in captured[1][0]['content']
    new_session = client.post(f"/api/agents/{agent['id']}/chat/stream", headers=auth_headers,
                             json={'message': 'New session.', 'mode': 'draft'})
    assert 'event: done' in new_session.text
    assert [message['role'] for message in captured[2]] == ['system', 'user']


def test_published_chat_uses_snapshot_knowledge_bindings_after_draft_changes(client, auth_headers):
    first_kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Published KB"})
    first_kb_id = first_kb.json()["knowledge_base"]["id"]
    client.post(
        f"/api/knowledge-bases/{first_kb_id}/documents",
        headers=auth_headers,
        json={"filename": "published.txt", "text": "published-source-token"},
    )
    second_kb = client.post("/api/knowledge-bases", headers=auth_headers, json={"name": "Draft KB"})
    second_kb_id = second_kb.json()["knowledge_base"]["id"]
    client.post(
        f"/api/knowledge-bases/{second_kb_id}/documents",
        headers=auth_headers,
        json={"filename": "draft.txt", "text": "draft-source-token"},
    )
    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "Snapshot Knowledge",
            "knowledge_base_ids": [first_kb_id],
            "rag": {"enabled_by_default": True, "top_k": 1},
        },
    )
    agent_id = agent.json()["agent"]["id"]
    assert client.post(f"/api/agents/{agent_id}/publish", headers=auth_headers).status_code == 200
    patched = client.patch(
        f"/api/agents/{agent_id}",
        headers=auth_headers,
        json={"knowledge_base_ids": [second_kb_id]},
    )
    assert patched.status_code == 200

    published = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "which kb", "mode": "published"},
    )
    assert published.status_code == 200
    assert "published-source-token" in published.text
    assert "draft-source-token" not in published.text


def test_agent_detail_includes_query_understanding(client, auth_headers):
    created = client.post(
        "/api/agents",
        headers=auth_headers,
        json={"name": "QU Agent", "model": "qwen-plus"},
    )
    assert created.status_code == 200
    agent_id = created.json()["agent"]["id"]
    detail = client.get(f"/api/agents/{agent_id}", headers=auth_headers)
    assert detail.status_code == 200
    qu = detail.json()["agent"]["query_understanding"]
    assert qu["enabled"] is True
    assert qu["confidence_threshold"] == 0.5


def test_session_memory_async_compression(client, auth_headers):
    agent = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "name": "Async Memory Agent",
            "memory": {"enabled": True, "strategy": "session_summary", "max_messages": 6},
        },
    )
    assert agent.status_code == 200
    agent_id = agent.json()["agent"]["id"]
    res = client.post(
        f"/api/agents/{agent_id}/chat/stream",
        headers=auth_headers,
        json={"message": "hello memory", "mode": "draft"},
    )
    assert res.status_code == 200
    assert "event: done" in res.text
    sessions = client.get(f"/api/agents/{agent_id}/sessions", headers=auth_headers).json()["items"]
    session_id = sessions[0]["id"]
    from core.db.session import SessionLocal
    from core.db.models import SessionMemory
    import json
    db = SessionLocal()
    try:
        memory = db.query(SessionMemory).filter(SessionMemory.session_id == session_id).first()
        assert memory is not None
        data = json.loads(memory.summary)
        assert len(data["turns"]) == 1
        assert data["turns"][0]["user"] == "hello memory"
    finally:
        db.close()
