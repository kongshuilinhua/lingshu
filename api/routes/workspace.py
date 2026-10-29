"""
灵枢 Agent 平台 —— 工作空间与邀请路由。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.deps import get_current_membership, get_current_user, require_manager
from api.schemas import InviteAcceptRequest, InviteCreateRequest, MemberRoleUpdateRequest
from api.serializers import (
    invite_payload,
    membership_payload,
    user_payload,
    workspace_payload,
)
from core.config import get_settings
from core.db.models import User, Workspace, WorkspaceInvite, WorkspaceMember
from core.db.session import get_db
from core.security.permissions import normalize_role
from core.services.workspaces import (
    accept_invite,
    create_invite,
    ensure_usable_invite,
    invite_expires_at,
    invite_is_expired,
    remove_member,
    update_member_role,
)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])
settings = get_settings()


@router.get("/current")
def current_workspace(membership: WorkspaceMember = Depends(get_current_membership)):
    return {"workspace": workspace_payload(membership.workspace, membership.role), "membership": membership_payload(membership)}


@router.get("/invites")
def list_invites(membership: WorkspaceMember = Depends(require_manager), db: Session = Depends(get_db)):
    if not settings.invite_api_enabled:
        raise HTTPException(status_code=404, detail="Invite API is disabled")
    invites = (
        db.query(WorkspaceInvite)
        .filter(WorkspaceInvite.workspace_id == membership.workspace_id)
        .order_by(WorkspaceInvite.id.desc())
        .all()
    )
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


@router.get("/invites/{token}")
def preview_invite(token: str, db: Session = Depends(get_db)):
    """
    公开预览一枚邀请：注册页拿到 ?invite=xxx 后先问这里，好回填邮箱、说清进的是哪个工作区。

    🎯 为什么单独做而非让前端直接提交：注册前用户什么都不知道，邮箱填错就 404，
        还看不出是链接过期还是邮箱不对。这里只回答「还能不能用」，不泄露成员名单。
    🛡️ 只回工作区名与邮箱，不回 workspace_id、不回角色之外的任何东西。
    """
    if not settings.invite_api_enabled:
        raise HTTPException(status_code=404, detail="Invite API is disabled")
    invite = db.query(WorkspaceInvite).filter(WorkspaceInvite.token == token).first()
    if not invite or invite.accepted_at is not None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite_is_expired(invite):
        raise HTTPException(status_code=410, detail="Invite expired")
    workspace = db.get(Workspace, invite.workspace_id)
    expires_at = invite_expires_at(invite)
    return {
        "email": invite.email,
        "role": normalize_role(invite.role),
        "workspace_name": workspace.name if workspace else "",
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.post("/invites")
def create_invite_endpoint(
    request: InviteCreateRequest,
    membership: WorkspaceMember = Depends(require_manager),
    db: Session = Depends(get_db),
):
    if not settings.invite_api_enabled:
        raise HTTPException(status_code=404, detail="Invite API is disabled")
    invite = create_invite(db, workspace_id=membership.workspace_id, email=request.email, role=request.role)
    # 🎯 创建时这一次必须把 token 交给调用方：`POST /invites/accept` 只认 token，
    #    而列表接口永远不回显（避免一次列表把全工作区的钥匙都抖出来）。
    #    创建时不给，整个邀请流程就没有任何一处能拿到它——开关打开也永远走不通。
    return {"invite": invite_payload(invite, include_token=True)}


@router.post("/invites/accept")
def accept_invite_endpoint(
    request: InviteAcceptRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not settings.invite_api_enabled:
        raise HTTPException(status_code=404, detail="Invite API is disabled")
    invite = ensure_usable_invite(db.query(WorkspaceInvite).filter(WorkspaceInvite.token == request.token).first())
    membership = accept_invite(db, invite=invite, user=current_user)
    total = db.query(WorkspaceMember).filter(WorkspaceMember.workspace_id == membership.workspace_id).count()
    expires_at = invite_expires_at(invite)
    return {
        "accepted": True,
        # 接受邀请后成员身份换了工作区，令牌里的 workspace_id 还是旧的。
        # 前端拿到这个字段就重登一次，否则后续请求全部落在原工作区。
        "relogin_required": True,
        "workspace_id": membership.workspace_id,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "member_count": total,
    }


@router.patch("/members/{member_id}")
def update_member(
    member_id: int,
    request: MemberRoleUpdateRequest,
    membership: WorkspaceMember = Depends(require_manager),
    db: Session = Depends(get_db),
):
    target = update_member_role(db, membership=membership, member_id=member_id, role=request.role)
    return {"member": {"id": target.id, "role": target.role, "user": user_payload(target.user)}}


@router.delete("/members/{member_id}")
def delete_member(
    member_id: int,
    membership: WorkspaceMember = Depends(require_manager),
    db: Session = Depends(get_db),
):
    removed_user_id = remove_member(db, membership=membership, member_id=member_id)
    return {"removed": True, "user_id": removed_user_id}

