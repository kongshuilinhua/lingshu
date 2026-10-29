"""
灵枢 Agent 平台 —— 工作空间与邀请路由。
"""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.deps import get_current_membership, get_current_user, require_manager
from api.schemas import InviteAcceptRequest, InviteCreateRequest
from api.serializers import (
    invite_payload,
    membership_payload,
    user_payload,
    workspace_payload,
)
from core.config import get_settings
from core.db.models import User, WorkspaceInvite, WorkspaceMember
from core.db.session import get_db
from core.security.permissions import normalize_role

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])
settings = get_settings()


@router.get("/current")
def current_workspace(membership: WorkspaceMember = Depends(get_current_membership)):
    return {"workspace": workspace_payload(membership.workspace, membership.role), "membership": membership_payload(membership)}


@router.get("/invites")
def list_invites(membership: WorkspaceMember = Depends(require_manager), db: Session = Depends(get_db)):
    if not settings.invite_api_enabled:
        raise HTTPException(status_code=404, detail="Invite API is disabled")
    invites = db.query(WorkspaceInvite).filter(WorkspaceInvite.workspace_id == membership.workspace_id).all()
    return {"items": [invite_payload(invite, include_token=False) for invite in invites]}


@router.get("/members")
def list_members(membership: WorkspaceMember = Depends(require_manager), db: Session = Depends(get_db)):
    rows = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == membership.workspace_id)
        .order_by(WorkspaceMember.id.asc())
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "role": normalize_role(row.role),
                "user": user_payload(row.user),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }


@router.post("/invites")
def create_invite(request: InviteCreateRequest, membership: WorkspaceMember = Depends(require_manager), db: Session = Depends(get_db)):
    if not settings.invite_api_enabled:
        raise HTTPException(status_code=404, detail="Invite API is disabled")
    if normalize_role(request.role) != "user":
        raise HTTPException(status_code=400, detail="Invite role must be user")
    invite = WorkspaceInvite(
        workspace_id=membership.workspace_id,
        email=request.email.lower(),
        role="user",
        token=secrets.token_urlsafe(24),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    # 🎯 创建时这一次必须把 token 交给调用方：`POST /invites/accept` 只认 token，
    #    而列表接口永远不回显（避免一次列表把全工作区的钥匙都抖出来）。
    #    创建时不给，整个邀请流程就没有任何一处能拿到它——开关打开也永远走不通。
    return {"invite": invite_payload(invite, include_token=True)}


@router.post("/invites/accept")
def accept_invite(request: InviteAcceptRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not settings.invite_api_enabled:
        raise HTTPException(status_code=404, detail="Invite API is disabled")
    invite = db.query(WorkspaceInvite).filter(WorkspaceInvite.token == request.token, WorkspaceInvite.accepted_at.is_(None)).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    existing = db.query(WorkspaceMember).filter(WorkspaceMember.workspace_id == invite.workspace_id, WorkspaceMember.user_id == current_user.id).first()
    if not existing:
        db.add(WorkspaceMember(workspace_id=invite.workspace_id, user_id=current_user.id, role=normalize_role(invite.role)))
    invite.accepted_at = datetime.now(timezone.utc)
    db.commit()
    return {"accepted": True}
