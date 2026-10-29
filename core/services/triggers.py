"""灵枢 Agent 平台 —— 触发器服务。

🎯 设计取舍：
    触发一次 = 用目标智能体的发布版本跑一轮普通对话，产物落成正常的会话、消息与 Run。
    因此不另起执行引擎，也不新增一套运行记录——触发后的效果在「会话」和「运行详情」
    里和用户手动发起的完全一样，能直接看到每个节点的输入输出。

两种触发方式：
    webhook  外部系统 POST /api/triggers/hook/{token}，body 的字段可填进消息模板
    schedule 平台内调度线程每 30 秒扫描到期记录，到点唤起
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from core.db.models import Agent, Message, Session as ChatSession, Trigger, Workflow

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")
SCHEDULER_POLL_SECONDS = 30


def trigger_payload(trigger: Trigger, *, base_url: str = "") -> dict:
    return {
        "id": trigger.id,
        "name": trigger.name,
        "target_type": trigger.target_type,
        "target_id": trigger.target_id,
        "kind": trigger.kind,
        "token": trigger.token,
        "webhook_url": f"{base_url}/api/triggers/hook/{trigger.token}" if trigger.kind == "webhook" else "",
        "cron": trigger.cron,
        "timezone": trigger.timezone,
        "message_template": trigger.message_template,
        "enabled": bool(trigger.enabled),
        # 前端按它决定启停/立即执行/删除按钮该不该给：后端只允许创建者或管理员操作，
        # 不下发归属的话普通成员会看到一堆点了必 403 的按钮
        "created_by": trigger.created_by,
        "last_fired_at": trigger.last_fired_at.isoformat() if trigger.last_fired_at else None,
        "next_run_at": trigger.next_run_at.isoformat() if trigger.next_run_at else None,
        "created_at": trigger.created_at.isoformat() if trigger.created_at else None,
    }


def render_message(template: str, payload: dict) -> str:
    """把 `{{字段}}` 替换成 payload 里的值；缺失的占位原样保留，便于排查模板写错。"""
    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key in payload:
            value = payload[key]
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return match.group(0)

    rendered = PLACEHOLDER.sub(substitute, template or "")
    return rendered.strip()


def next_run_after(cron: str, *, after: datetime | None = None, timezone_name: str = "Asia/Shanghai") -> datetime | None:
    """
    算出下一次执行时间（存 UTC）。

    croniter 只在调度线程里用到；配错表达式时返回 None 而不是抛异常，
    免得一条坏记录把整个调度循环打断。
    """
    if not cron:
        return None
    try:
        from croniter import croniter
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(timezone_name or "Asia/Shanghai")
        base = (after or datetime.now(timezone.utc)).astimezone(tz)
        nxt = croniter(cron, base).get_next(datetime)
        return nxt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


def resolve_target(db: Session, trigger: Trigger) -> Agent | None:
    """触发器指向的智能体。工作流型触发借它的宿主智能体跑。"""
    if trigger.target_type == "workflow":
        workflow = db.get(Workflow, trigger.target_id)
        if not workflow or not workflow.agent_id:
            return None
        return db.get(Agent, workflow.agent_id)
    return db.get(Agent, trigger.target_id)


# 用发布快照跑，和用户在对话页发消息完全同一条路径
def run_trigger(db: Session, trigger: Trigger, payload: dict | None = None) -> dict:
    """
    执行一次触发：建会话 → 跑一轮 → 落消息。

    返回 session_id / run_id / answer，供 webhook 调用方直接取回答。
    目标未发布或已删除时抛 ValueError，路由层转成 400。
    """
    from core.runtime.workflow import WorkflowRunner

    agent = resolve_target(db, trigger)
    if not agent:
        raise ValueError("触发目标不存在或还没有运行配置")
    if not agent.published_version_id:
        raise ValueError("目标智能体还没有发布版本，无法执行")

    message = render_message(trigger.message_template, payload or {}) or (trigger.name or "定时触发")
    session = ChatSession(
        workspace_id=trigger.workspace_id,
        agent_id=agent.id,
        user_id=trigger.created_by,
        title=f"[{trigger.name or '触发器'}] {message[:40]}",
        is_debug=False,
        trigger_id=trigger.id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    db.add(Message(session_id=session.id, role="user", content=message, sources=[]))
    db.commit()

    run, answer, sources, _steps = WorkflowRunner(db).run(
        agent=agent,
        chat_session=session,
        user_message=message,
        mode="published",
    )
    assistant = Message(session_id=session.id, role="assistant", content=answer, sources=sources, run_id=run.id)
    db.add(assistant)
    trigger.last_fired_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if trigger.kind == "schedule":
        trigger.next_run_at = next_run_after(trigger.cron, timezone_name=trigger.timezone)
    db.commit()
    return {"session_id": session.id, "run_id": run.id, "status": run.status, "answer": answer}


def create_trigger(db: Session, *, workspace_id: int, user_id: int, payload: dict) -> Trigger:
    kind = payload.get("kind") or "webhook"
    if kind == "schedule" and not (payload.get("cron") or "").strip():
        raise ValueError("定时触发需要 cron 表达式")
    trigger = Trigger(
        workspace_id=workspace_id,
        created_by=user_id,
        name=(payload.get("name") or "未命名触发器").strip()[:160],
        target_type=payload.get("target_type") or "agent",
        target_id=int(payload.get("target_id") or 0),
        kind=kind,
        token=secrets.token_urlsafe(24),
        cron=(payload.get("cron") or "").strip(),
        timezone=(payload.get("timezone") or "Asia/Shanghai").strip(),
        message_template=payload.get("message_template") or "",
        enabled=bool(payload.get("enabled", True)),
    )
    if not trigger.target_id:
        raise ValueError("必须指定触发目标")
    if trigger.kind == "schedule":
        trigger.next_run_at = next_run_after(trigger.cron, timezone_name=trigger.timezone)
        if trigger.next_run_at is None:
            raise ValueError("cron 表达式无法解析")
    db.add(trigger)
    db.commit()
    db.refresh(trigger)
    return trigger


def update_trigger(db: Session, trigger: Trigger, payload: dict) -> Trigger:
    for key in ("name", "target_type", "cron", "timezone", "message_template"):
        if payload.get(key) is not None:
            setattr(trigger, key, payload[key])
    if payload.get("target_id") is not None:
        trigger.target_id = int(payload["target_id"])
    if payload.get("enabled") is not None:
        trigger.enabled = bool(payload["enabled"])
    if trigger.kind == "schedule":
        # cron 改了或重新启用，都按新表达式重算下一次；停用则清空，避免调度线程空转
        trigger.next_run_at = (
            next_run_after(trigger.cron, timezone_name=trigger.timezone) if trigger.enabled else None
        )
    db.commit()
    db.refresh(trigger)
    return trigger


def list_workspace_triggers(db: Session, workspace_id: int) -> list[Trigger]:
    return (
        db.query(Trigger)
        .filter(Trigger.workspace_id == workspace_id)
        .order_by(Trigger.created_at.desc())
        .all()
    )


def due_triggers(db: Session, *, now: datetime | None = None) -> list[Trigger]:
    moment = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return (
        db.query(Trigger)
        .filter(
            Trigger.kind == "schedule",
            Trigger.enabled.is_(True),
            Trigger.next_run_at.is_not(None),
            Trigger.next_run_at <= moment,
        )
        .order_by(Trigger.next_run_at.asc())
        .all()
    )


def claim_trigger(db: Session, trigger: Trigger) -> bool:
    """
    原子抢占一条到期记录：把 next_run_at 推后到下一次。

    多进程同时扫描时，只有拿到行级条件更新的一方返回 True，避免同一时刻重复触发。
    条件里必须带「仍然到期」：超时之后再抢一次时，内存里的对象可能已经陈旧，
    只比对值会让第二次也成功，等于重复触发。
    """
    moment = datetime.now(timezone.utc).replace(tzinfo=None)
    following = next_run_after(trigger.cron, timezone_name=trigger.timezone)
    if following is None:
        # cron 已经解析不了：停用，别再让它每分钟重试
        trigger.enabled = False
        trigger.next_run_at = None
        db.commit()
        return False
    updated = (
        db.query(Trigger)
        .filter(
            Trigger.id == trigger.id,
            Trigger.enabled.is_(True),
            Trigger.next_run_at.is_not(None),
            Trigger.next_run_at <= moment,
        )
        .update({Trigger.next_run_at: following}, synchronize_session=False)
    )
    db.commit()
    return bool(updated)


def find_by_token(db: Session, token: str) -> Trigger | None:
    return db.query(Trigger).filter(Trigger.token == token).first()


def trigger_targets(db: Session, workspace_id: int) -> dict:
    """前端下拉用：工作区内可作为触发目标的智能体与工作流。"""
    agents = (
        db.query(Agent.id, Agent.name)
        .filter(Agent.workspace_id == workspace_id)
        .order_by(Agent.name.asc())
        .all()
    )
    host_ids = {
        row.agent_id
        for row in db.query(Workflow.agent_id).filter(Workflow.workspace_id == workspace_id).all()
        if row.agent_id
    }
    workflows = (
        db.query(Workflow.id, Workflow.name)
        .filter(Workflow.workspace_id == workspace_id)
        .order_by(Workflow.name.asc())
        .all()
    )
    return {
        "agents": [{"id": row.id, "name": row.name} for row in agents if row.id not in host_ids],
        "workflows": [{"id": row.id, "name": row.name} for row in workflows],
    }
