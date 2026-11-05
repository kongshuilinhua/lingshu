"""触发器：webhook 唤起、定时计算出下一次、手动试触发。

只覆盖核心路径——触发一次 = 用发布版本跑一轮对话，产物落成普通会话与 Run。
"""

from datetime import datetime, timedelta, timezone

from core.db.models import Trigger
from core.services.triggers import next_run_after


def _published_agent(client, headers, name="被触发的智能体"):
    agent = client.post(
        "/api/agents", headers=headers, json={"name": name, "system_prompt": "你是测试助手。"}
    ).json()["agent"]
    assert client.post(f"/api/agents/{agent['id']}/publish", headers=headers).status_code == 200
    return agent


def _create_trigger(client, headers, **overrides):
    payload = {
        "name": "测试触发器",
        "target_type": "agent",
        "target_id": overrides.pop("target_id"),
        "kind": "webhook",
        "message_template": "订单 {{order}} 到了吗",
        **overrides,
    }
    return client.post("/api/triggers", headers=headers, json=payload)


def test_webhook_trigger_runs_chat_and_records_session(client, auth_headers):
    agent = _published_agent(client, auth_headers)
    created = _create_trigger(client, auth_headers, target_id=agent["id"])
    assert created.status_code == 200, created.text
    trigger = created.json()["trigger"]
    assert trigger["webhook_url"].endswith(trigger["token"])

    # 外部系统调用不带登录态，token 就是凭证
    fired = client.post(f"/api/triggers/hook/{trigger['token']}", json={"order": "SO-1928"})
    assert fired.status_code == 200, fired.text
    result = fired.json()
    assert result["status"] == "succeeded"
    assert result["answer"]

    # 触发产物是普通会话，能在会话列表里看到，并带 trigger_id 回溯
    sessions = client.get(f"/api/agents/{agent['id']}/sessions", headers=auth_headers).json()["items"]
    assert sessions[0]["id"] == result["session_id"]
    assert "SO-1928" in sessions[0]["title"]


def test_hook_rejects_disabled_or_unknown_token(client, auth_headers):
    agent = _published_agent(client, auth_headers)
    trigger = _create_trigger(client, auth_headers, target_id=agent["id"]).json()["trigger"]
    assert client.patch(
        f"/api/triggers/{trigger['id']}", headers=auth_headers, json={"enabled": False}
    ).status_code == 200

    blocked = client.post(f"/api/triggers/hook/{trigger['token']}", json={})
    assert blocked.status_code == 404
    assert client.post("/api/triggers/hook/not-a-real-token", json={}).status_code == 404


def test_manual_run_endpoint(client, auth_headers):
    agent = _published_agent(client, auth_headers)
    trigger = _create_trigger(client, auth_headers, target_id=agent["id"]).json()["trigger"]

    result = client.post(f"/api/triggers/{trigger['id']}/run", headers=auth_headers)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "succeeded"

    listed = client.get("/api/triggers", headers=auth_headers).json()
    assert listed["items"][0]["last_fired_at"] is not None
    # 下拉数据里不出现工作流的宿主智能体
    workflow_hosts = {item["id"] for item in client.get("/api/workflows", headers=auth_headers).json()["items"] if item["agent_id"]}
    assert not ({item["id"] for item in listed["targets"]["agents"]} & workflow_hosts)


def test_schedule_trigger_computes_next_run(client, auth_headers):
    agent = _published_agent(client, auth_headers)
    created = _create_trigger(client, auth_headers, target_id=agent["id"], kind="schedule", cron="0 9 * * *")
    assert created.status_code == 200, created.text
    trigger = created.json()["trigger"]
    assert trigger["next_run_at"]

    assert client.patch(
        f"/api/triggers/{trigger['id']}", headers=auth_headers, json={"enabled": False}
    ).json()["trigger"]["next_run_at"] is None  # 停用就不该再被扫到

    bad = _create_trigger(client, auth_headers, target_id=agent["id"], kind="schedule", cron="not a cron")
    assert bad.status_code == 400
    missing = _create_trigger(client, auth_headers, target_id=agent["id"], kind="schedule", cron="")
    assert missing.status_code == 400


def test_trigger_requires_published_target(client, auth_headers):
    unpublished = client.post("/api/agents", headers=auth_headers, json={"name": "没发布"}).json()["agent"]
    trigger = _create_trigger(client, auth_headers, target_id=unpublished["id"]).json()["trigger"]
    fired = client.post(f"/api/triggers/hook/{trigger['token']}", json={})
    assert fired.status_code == 400
    assert "发布" in fired.json()["detail"]


def test_trigger_rejects_cross_workspace_target(client, auth_headers):
    """target_id 是调用方填的整数，指向别的工作区的智能体必须被拒。

    少了这道判断，触发器就成了「按 id 调用他人智能体」的入口：
    跑的是对方的发布版本，花的是对方的模型配额，答案却落在自己的工作区。
    """
    registered = client.post(
        "/api/auth/register",
        json={"email": "trigger-outsider@example.com", "name": "Outsider", "password": "password123"},
    ).json()
    outsider_headers = {"Authorization": f"Bearer {registered['access_token']}"}
    outsider_agent = _published_agent(client, outsider_headers, name="别人工作区的智能体")

    created = _create_trigger(client, auth_headers, target_id=outsider_agent["id"])
    assert created.status_code == 400, created.text
    assert "不属于当前工作区" in created.json()["detail"]

    # 已有触发器也不能改到别人的智能体上
    own_agent = _published_agent(client, auth_headers, name="自己工作区的智能体")
    trigger = _create_trigger(client, auth_headers, target_id=own_agent["id"]).json()["trigger"]
    patched = client.patch(
        f"/api/triggers/{trigger['id']}",
        headers=auth_headers,
        json={"target_id": outsider_agent["id"]},
    )
    assert patched.status_code == 400, patched.text
    assert "不属于当前工作区" in patched.json()["detail"]


def test_scoped_target_rejects_foreign_agent():
    """不依赖数据库地守住核心判断：目标归属不同工作区必须取不到。"""
    from types import SimpleNamespace

    from core.db.models import Agent, Workflow
    from core.services.triggers import scoped_target

    own = SimpleNamespace(id=1, workspace_id=10, name="自己的智能体")
    foreign = SimpleNamespace(id=2, workspace_id=20, name="别人的智能体")
    workflows = {
        100: SimpleNamespace(id=100, workspace_id=10, agent_id=1),
        200: SimpleNamespace(id=200, workspace_id=20, agent_id=2),
    }

    class _StubDB:
        def get(self, model, pk):
            if model is Agent:
                return next((row for row in (own, foreign) if row.id == pk), None)
            if model is Workflow:
                return workflows.get(pk)
            return None

    db = _StubDB()
    assert scoped_target(db, workspace_id=10, target_type="agent", target_id=1) is own
    assert scoped_target(db, workspace_id=10, target_type="agent", target_id=2) is None
    # 工作流型触发借它的宿主智能体，宿主归属同样要判
    assert scoped_target(db, workspace_id=10, target_type="workflow", target_id=100) is own
    assert scoped_target(db, workspace_id=10, target_type="workflow", target_id=200) is None
    assert scoped_target(db, workspace_id=10, target_type="agent", target_id=999) is None


def test_next_run_after_uses_timezone():
    now = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)  # 北京时间 08:00
    # 每天 09:00 北京时间 = 01:00 UTC
    following = next_run_after("0 9 * * *", after=now)
    assert following == datetime(2026, 1, 1, 1, 0)
    assert following > now.replace(tzinfo=None)
    assert next_run_after("bad cron") is None


def test_due_triggers_and_claim_are_atomic(client, auth_headers):
    from core.db.session import SessionLocal
    from core.services.triggers import claim_trigger, due_triggers

    agent = _published_agent(client, auth_headers)
    trigger = _create_trigger(client, auth_headers, target_id=agent["id"], kind="schedule", cron="0 9 * * *").json()["trigger"]

    with SessionLocal() as db:
        row = db.get(Trigger, trigger["id"])
        row.next_run_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        db.commit()
        assert [item.id for item in due_triggers(db)] == [trigger["id"]]

        # 第一次抢占成功、第二次失败：多副本同时扫描时只有一个能真正执行
        assert claim_trigger(db, row) is True
        db.refresh(row)
        assert claim_trigger(db, row) is False
