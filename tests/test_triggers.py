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
