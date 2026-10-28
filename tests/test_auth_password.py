"""修改密码：必须验证旧密码，改完旧令牌失效。"""


def _register(client, email="pw@example.com", password="password123"):
    response = client.post(
        "/api/auth/register", json={"email": email, "name": "PW", "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _login(client, email="pw@example.com", password="password123"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_change_password_requires_current_password(client):
    token = _register(client)
    headers = {"Authorization": f"Bearer {token}"}

    wrong = client.post(
        "/api/auth/password", headers=headers,
        json={"current_password": "not-the-password", "new_password": "newpassword123"},
    )
    assert wrong.status_code == 400
    assert "当前密码不正确" in wrong.json()["detail"]
    # 密码没被改掉，旧密码仍能登录
    assert _login(client).status_code == 200


def test_change_password_then_old_password_fails(client):
    token = _register(client)
    headers = {"Authorization": f"Bearer {token}"}

    changed = client.post(
        "/api/auth/password", headers=headers,
        json={"current_password": "password123", "new_password": "newpassword123"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["changed"] is True

    assert _login(client).status_code == 401
    assert _login(client, password="newpassword123").status_code == 200


def test_change_password_rejects_same_password(client):
    token = _register(client)
    response = client.post(
        "/api/auth/password", headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "password123", "new_password": "password123"},
    )
    assert response.status_code == 400


def test_change_password_requires_min_length(client):
    token = _register(client)
    response = client.post(
        "/api/auth/password", headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "password123", "new_password": "short"},
    )
    assert response.status_code == 422


def test_change_password_requires_login(client):
    response = client.post(
        "/api/auth/password",
        json={"current_password": "password123", "new_password": "newpassword123"},
    )
    assert response.status_code == 401
