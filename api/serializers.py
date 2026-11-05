"""
灵枢 Agent 平台 —— API 序列化辅助函数。

🎯 架构角色：
    从 api/main.py 中提取所有实体到 dict 的序列化函数。
    main.py 原先混杂了路由逻辑和数据转换逻辑，拆分后路由文件统一导入此模块。
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.db.models import (
    Message,
    Session as ChatSessionModel,
    User,
    WorkspaceMember,
    WorkspaceInvite,
)
from core.security.permissions import normalize_role


def user_payload(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "avatar_url": user.avatar_url or ""}


def workspace_payload(workspace, role: str) -> dict:
    return {"id": workspace.id, "name": workspace.name, "slug": workspace.slug, "role": normalize_role(role)}


def membership_payload(membership: WorkspaceMember) -> dict:
    return {"workspace_id": membership.workspace_id, "user_id": membership.user_id, "role": normalize_role(membership.role)}


def invite_payload(invite: WorkspaceInvite, *, include_token: bool = False) -> dict:
    payload = {"id": invite.id, "email": invite.email, "role": normalize_role(invite.role), "accepted_at": invite.accepted_at.isoformat() if invite.accepted_at else None}
    if include_token:
        payload["token"] = invite.token
    return payload


def session_payload(session: ChatSessionModel, db: Session, *, message_count: int | None = None) -> dict:
    """
    单个会话的响应体。

    `message_count` 可以由调用方传入（列表场景一次 GROUP BY 算好），
    不传时才回退到单条 count —— 列表接口逐条 count 就是 N+1，
    会话一多，打开对话列表会发出几十条查询。
    """
    if message_count is None:
        message_count = db.query(Message).filter(Message.session_id == session.id).count()
    return {
        "id": session.id,
        "agent_id": session.agent_id,
        "title": session.title,
        "message_count": message_count,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


def session_message_counts(db: Session, session_ids: list[int]) -> dict[int, int]:
    """一次性统计多个会话的消息数，避免列表页逐条 count。"""
    if not session_ids:
        return {}
    rows = (
        db.query(Message.session_id, func.count(Message.id))
        .filter(Message.session_id.in_(session_ids))
        .group_by(Message.session_id)
        .all()
    )
    return {session_id: count for session_id, count in rows}


def message_payload(message: Message) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "sources": message.sources or [],
        "run_id": message.run_id,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


