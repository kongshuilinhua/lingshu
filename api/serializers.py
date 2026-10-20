"""
灵枢 Agent 平台 —— API 序列化辅助函数。

🎯 架构角色：
    从 api/main.py 中提取所有实体到 dict 的序列化函数。
    main.py 原先混杂了路由逻辑和数据转换逻辑，拆分后路由文件统一导入此模块。
"""

from sqlalchemy.orm import Session

from core.db.models import (
    Agent,
    AgentVersion,
    Message,
    Session as ChatSessionModel,
    User,
    WorkspaceMember,
    WorkspaceInvite,
)
from core.security.permissions import normalize_role
from core.services.agents import agent_summary


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


def session_payload(session: ChatSessionModel, db: Session) -> dict:
    count = db.query(Message).filter(Message.session_id == session.id).count()
    return {
        "id": session.id,
        "agent_id": session.agent_id,
        "title": session.title,
        "message_count": count,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


def message_payload(message: Message) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "sources": message.sources or [],
        "run_id": message.run_id,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def review_payload(db: Session, agent: Agent) -> dict:
    version = db.query(AgentVersion).filter(AgentVersion.agent_id == agent.id).order_by(AgentVersion.version.desc()).first()
    return {
        **agent_summary(agent),
        "submitted_version": version.version if version else None,
        "submitted_at": version.created_at.isoformat() if version and version.created_at else None,
    }
