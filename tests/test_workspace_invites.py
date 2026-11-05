"""
工作区成员与邀请流程测试。

覆盖三件此前走不通的事：
1. 接受邀请后成员真的进了新工作区，且下次登录不会被退回旧工作区；
2. 邀请链接会过期，过期后注册与接受都失败；
3. 成员角色调整与移除有守卫（不能动自己、不能动 owner、不能删到没有管理员）。
"""

import pytest
from datetime import timedelta


@pytest.fixture()
def invites_enabled(monkeypatch):
    """打开邀请开关。settings 是模块级单例，直接改属性比 setenv 更可靠。"""
    from api.routes import workspace as workspace_route

    monkeypatch.setattr(workspace_route.settings, "invite_api_enabled", True)
    from core.config import get_settings

    monkeypatch.setattr(get_settings(), "invite_api_enabled", True)
    monkeypatch.setattr(get_settings(), "invite_expire_hours", 168)


@pytest.fixture()
def second_user_token(client, auth_headers, invites_enabled):
    invite = _invite(client, auth_headers, email="member@example.com")
    response = client.post(
        "/api/auth/register",
        json={"email": "member@example.com", "name": "Member", "password": "password123", "invite_token": invite["token"]},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _workspace_id(client, headers) -> int:
    return client.get("/api/workspaces/current", headers=headers).json()["workspace"]["id"]


def _invite(client, headers, email="newbie@example.com", role="user"):
    response = client.post("/api/workspaces/invites", headers=headers, json={"email": email, "role": role})
    assert response.status_code == 200, response.text
    return response.json()["invite"]


def test_invite_register_lands_in_inviter_workspace(client, auth_headers, invites_enabled):
    owner_workspace = _workspace_id(client, auth_headers)
    invite = _invite(client, auth_headers)

    response = client.post(
        "/api/auth/register",
        json={
            "email": "newbie@example.com",
            "name": "Newbie",
            "password": "password123",
            "invite_token": invite["token"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workspace"]["id"] == owner_workspace
    assert body["workspace"]["role"] == "user"

    # 关键回归：新账号下次登录不能掉回自己的（不存在的）默认工作区
    login = client.post("/api/auth/login", json={"email": "newbie@example.com", "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert _workspace_id(client, headers) == owner_workspace


def test_invite_is_single_use(client, auth_headers, invites_enabled):
    invite = _invite(client, auth_headers)
    payload = {
        "email": "newbie@example.com",
        "name": "Newbie",
        "password": "password123",
        "invite_token": invite["token"],
    }
    assert client.post("/api/auth/register", json=payload).status_code == 200

    # token 用过即失效，换个邮箱也不能再用
    replay = client.post("/api/auth/register", json={**payload, "email": "other@example.com"})
    assert replay.status_code == 404


def test_invite_email_must_match(client, auth_headers, invites_enabled):
    invite = _invite(client, auth_headers, email="newbie@example.com")
    response = client.post(
        "/api/auth/register",
        json={
            "email": "someone-else@example.com",
            "name": "Someone",
            "password": "password123",
            "invite_token": invite["token"],
        },
    )
    assert response.status_code == 404


def test_expired_invite_is_rejected(client, auth_headers, invites_enabled):
    invite = _invite(client, auth_headers)

    # 把签发时间往前挪过有效期（默认 168 小时），比压有效期更贴近真实场景：
    # 链接是几天前发的，今天才点开。
    from core.db.models import WorkspaceInvite
    from core.db.session import SessionLocal
    from core.services.workspaces import db_now

    with SessionLocal() as db:
        row = db.query(WorkspaceInvite).filter(WorkspaceInvite.token == invite["token"]).one()
        row.created_at = db_now() - timedelta(days=8)
        db.commit()

    response = client.post(
        "/api/auth/register",
        json={
            "email": "newbie@example.com",
            "name": "Newbie",
            "password": "password123",
            "invite_token": invite["token"],
        },
    )
    assert response.status_code == 410


def test_invite_preview_reports_expiry_without_leaking_token(client, auth_headers, invites_enabled):
    invite = _invite(client, auth_headers)

    ok = client.get(f"/api/workspaces/invites/{invite['token']}")
    assert ok.status_code == 200
    assert ok.json()["email"] == "newbie@example.com"
    assert "token" not in ok.json()

    from core.db.models import WorkspaceInvite
    from core.db.session import SessionLocal
    from core.services.workspaces import db_now

    with SessionLocal() as db:
        row = db.query(WorkspaceInvite).filter(WorkspaceInvite.token == invite["token"]).one()
        row.created_at = db_now() - timedelta(days=8)
        db.commit()

    assert client.get(f"/api/workspaces/invites/{invite['token']}").status_code == 410


def test_disabling_invites_blocks_joining_but_not_registration(client, auth_headers, invites_enabled, monkeypatch):
    from core.config import get_settings
    from api.routes import workspace as workspace_route

    recipient = client.post("/api/auth/register", json={"email": "existing@example.com", "name": "Existing", "password": "password123"}).json()
    existing_invite = _invite(client, auth_headers, email="existing@example.com")
    new_invite = _invite(client, auth_headers, email="new@example.com")
    monkeypatch.setattr(get_settings(), "invite_api_enabled", False)
    monkeypatch.setattr(workspace_route.settings, "invite_api_enabled", False)
    registration = {"email": "new@example.com", "name": "New", "password": "password123"}
    assert client.post("/api/auth/register", json={**registration, "invite_token": new_invite["token"]}).status_code == 404
    assert client.post("/api/workspaces/invites/accept", headers={"Authorization": f"Bearer {recipient['access_token']}"}, json={"token": existing_invite["token"]}).status_code == 404
    registered = client.post("/api/auth/register", json=registration)
    assert registered.status_code == 200
    assert registered.json()["workspace"]["id"] != _workspace_id(client, auth_headers)


def test_register_without_invite_is_open_when_enabled(client, invites_enabled):
    # 开启工作区邀请后，首个账号和后续账号都可以独立注册。
    owner = client.post("/api/auth/register", json={"email": "owner@example.com", "name": "Owner", "password": "password123"})
    assert owner.status_code == 200, owner.text
    response = client.post(
        "/api/auth/register",
        json={"email": "walkin@example.com", "name": "Walkin", "password": "password123"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == "walkin@example.com"
    assert response.json()["workspace"]["role"] == "admin"
    assert response.json()["workspace"]["id"] != owner.json()["workspace"]["id"]
    login = client.post("/api/auth/login", json={"email": "walkin@example.com", "password": "password123"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200


def test_register_is_open_when_invites_disabled(client, auth_headers):
    """开关关着的时候注册必须照常放行，否则老部署升级后没人能再注册。"""
    response = client.post(
        "/api/auth/register",
        json={"email": "free@example.com", "name": "Free", "password": "password123"},
    )
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert _workspace_id(client, headers) != _workspace_id(client, auth_headers)


def test_existing_account_accepts_invite_and_receives_target_workspace_token(client, auth_headers, invites_enabled):
    registered = client.post("/api/auth/register", json={"email": "existing@example.com", "name": "Existing", "password": "password123"}).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    own_workspace = _workspace_id(client, headers)
    target_workspace = _workspace_id(client, auth_headers)
    assert own_workspace != target_workspace
    invite = _invite(client, auth_headers, email="existing@example.com")
    accepted = client.post("/api/workspaces/invites/accept", headers=headers, json={"token": invite["token"]})
    assert accepted.status_code == 200, accepted.text
    joined_headers = {"Authorization": f"Bearer {accepted.json()['access_token']}"}
    assert _workspace_id(client, joined_headers) == target_workspace
    assert client.get("/api/auth/me", headers=joined_headers).json()["membership"]["role"] == "user"
    # 原有工作区保留，加入目标工作区不改动原成员身份。
    assert _workspace_id(client, headers) == own_workspace
    login = client.post("/api/auth/login", json={"email": "existing@example.com", "password": "password123"})
    assert _workspace_id(client, {"Authorization": f"Bearer {login.json()['access_token']}"}) == target_workspace
    assert client.post("/api/workspaces/invites/accept", headers=joined_headers, json={"token": invite["token"]}).status_code == 404
    workspaces = client.get("/api/workspaces", headers=joined_headers).json()["items"]
    assert {row["id"] for row in workspaces} == {own_workspace, target_workspace}
    switched = client.post(f"/api/workspaces/{own_workspace}/switch", headers=joined_headers)
    assert switched.status_code == 200
    assert _workspace_id(client, {"Authorization": f"Bearer {switched.json()['access_token']}"}) == own_workspace
    assert client.post(f"/api/workspaces/{own_workspace}/switch", headers=auth_headers).status_code == 404


def test_accept_invite_rejects_wrong_account_without_consuming_invite(client, auth_headers, invites_enabled):
    invite = _invite(client, auth_headers, email="recipient@example.com")
    rejected = client.post("/api/workspaces/invites/accept", headers=auth_headers, json={"token": invite["token"]})
    assert rejected.status_code == 404
    assert client.get(f"/api/workspaces/invites/{invite['token']}").status_code == 200


def test_inviting_current_member_is_rejected(client, auth_headers, second_user_token):
    response = client.post("/api/workspaces/invites", headers=auth_headers, json={"email": "member@example.com", "role": "admin"})
    assert response.status_code == 409


def test_registration_does_not_expose_other_workspace_resources(client, auth_headers):
    agent = client.post("/api/agents", headers=auth_headers, json={"name": "Private agent"}).json()["agent"]
    registered = client.post("/api/auth/register", json={"email": "separate@example.com", "name": "Separate", "password": "password123"}).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    assert client.get(f"/api/agents/{agent['id']}", headers=headers).status_code == 404
    members = client.get("/api/workspaces/members", headers=headers).json()["items"]
    assert [row["user"]["email"] for row in members] == ["separate@example.com"]


def test_personal_workspace_admin_cannot_manage_platform_models_or_stdio(client, auth_headers):
    registered = client.post("/api/auth/register", json={"email": "personal@example.com", "name": "Personal", "password": "password123"}).json()
    headers = {"Authorization": f"Bearer {registered['access_token']}"}
    assert registered["workspace"]["role"] == "admin"
    assert client.get("/api/models?include_disabled=true", headers=headers).status_code == 403
    model = client.get("/api/models", headers=auth_headers).json()["items"][0]
    assert client.patch(f"/api/admin/models/{model['id']}", headers=headers, json={"enabled": False}).status_code == 403
    assert client.get("/api/mcp/connection-options", headers=headers).json()["can_register_stdio"] is False
    response = client.post("/api/mcp/servers", headers=headers, json={"name": "Local", "transport": "stdio", "command": "python", "args": ["server.py"]})
    assert response.status_code == 400


def test_member_role_update_and_guards(client, auth_headers, second_user_token):
    members = client.get("/api/workspaces/members", headers=auth_headers).json()["items"]
    assert len(members) == 2

    member = next(row for row in members if row["user"]["email"] == "member@example.com")
    promoted = client.patch(
        f"/api/workspaces/members/{member['id']}",
        headers=auth_headers,
        json={"role": "admin"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["member"]["role"] == "admin"

    # owner 自己那条不能被改：这里操作的成员是 owner，应被 400 拦下
    owner_row = next(row for row in members if row["user"]["email"] == "owner@example.com")
    assert client.patch(
        f"/api/workspaces/members/{owner_row['id']}",
        headers=auth_headers,
        json={"role": "user"},
    ).status_code == 400


def test_cannot_remove_self(client, auth_headers):
    members = client.get("/api/workspaces/members", headers=auth_headers).json()["items"]
    self_row = next(row for row in members if row["user"]["email"] == "owner@example.com")
    assert client.delete(f"/api/workspaces/members/{self_row['id']}", headers=auth_headers).status_code == 400


def test_remove_member_revokes_workspace_access(client, auth_headers, second_user_token):
    owner_workspace = _workspace_id(client, auth_headers)
    members = client.get("/api/workspaces/members", headers=auth_headers).json()["items"]
    member = next(row for row in members if row["user"]["email"] == "member@example.com")

    assert client.delete(f"/api/workspaces/members/{member['id']}", headers=auth_headers).status_code == 200

    # 被移除的人手里的老令牌不能再进这个工作区
    member_headers = {"Authorization": f"Bearer {second_user_token}"}
    response = client.get("/api/workspaces/current", headers=member_headers)
    assert response.status_code in (403, 404) or response.json()["workspace"]["id"] != owner_workspace
    # 账号仍存在，移出最后一个工作区后也能验证账号并接受新的邀请。
    assert client.get("/api/auth/account", headers=member_headers).status_code == 200


def test_non_manager_cannot_manage_members(client, auth_headers, second_user_token):
    member_headers = {"Authorization": f"Bearer {second_user_token}"}
    assert client.get("/api/workspaces/members", headers=member_headers).status_code == 403
    assert client.post(
        "/api/workspaces/invites",
        headers=member_headers,
        json={"email": "x@example.com", "role": "user"},
    ).status_code == 403


def test_invite_reissue_rotates_token(client, auth_headers, invites_enabled):
    first = _invite(client, auth_headers, email="newbie@example.com")
    second = _invite(client, auth_headers, email="newbie@example.com")

    assert first["id"] == second["id"]
    assert first["token"] != second["token"]

    # 旧链接当场作废
    assert client.post(
        "/api/auth/register",
        json={
            "email": "newbie@example.com",
            "name": "Newbie",
            "password": "password123",
            "invite_token": first["token"],
        },
    ).status_code == 404
