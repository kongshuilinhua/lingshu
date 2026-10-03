"""
灵枢 Agent 平台 —— Auth 路由。

提供注册、登录、获取/更新当前用户信息。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from api.access import invite_workspace
from api.deps import get_current_membership, get_current_user
from api.rate_limit import redis_rate_limit
from api.schemas import (
    LoginRequest,
    RegisterRequest,
    UserProfileUpdateRequest,
)
from api.serializers import (
    membership_payload,
    user_payload,
    workspace_payload,
)
from core.db.models import User, WorkspaceMember, WorkspaceInvite
from core.db.session import get_db
from core.security.auth import (
    create_access_token,
    hash_password,
    revoke_access_token,
    verify_password,
)
from core.security.permissions import normalize_role
from core.services.bootstrap import (
    create_default_workspace_user,
    create_first_user_workspace,
    has_any_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", dependencies=[Depends(redis_rate_limit(10, 60, scope="register"))])
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == request.email.lower()).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    invite = None
    if request.invite_token:
        invite = (
            db.query(WorkspaceInvite)
            .filter(
                WorkspaceInvite.token == request.invite_token,
                WorkspaceInvite.accepted_at.is_(None),
                WorkspaceInvite.email == request.email.lower(),
            )
            .first()
        )
        if not invite:
            raise HTTPException(status_code=404, detail="Invite not found")
    if invite:
        user = User(email=request.email.lower(), name=request.name, password_hash=hash_password(request.password))
        db.add(user)
        db.flush()
        db.add(WorkspaceMember(workspace_id=invite.workspace_id, user_id=user.id, role=normalize_role(invite.role)))
        invite.accepted_at = datetime.now(timezone.utc)
        db.commit()
        workspace = invite_workspace(db, invite.workspace_id)
        role = normalize_role(invite.role)
    elif has_any_user(db):
        user, workspace = create_default_workspace_user(db, email=request.email, name=request.name, password=request.password)
        role = "user"
    else:
        user, workspace = create_first_user_workspace(db, email=request.email, name=request.name, password=request.password)
        role = "admin"
    token = create_access_token({"sub": str(user.id), "workspace_id": workspace.id})
    return {"access_token": token, "token_type": "bearer", "user": user_payload(user), "workspace": workspace_payload(workspace, role)}


@router.post("/login", dependencies=[Depends(redis_rate_limit(20, 60, scope="login"))])
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == request.email.lower()).first()
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    membership = db.query(WorkspaceMember).filter(WorkspaceMember.user_id == user.id).first()
    token = create_access_token({"sub": str(user.id), "workspace_id": membership.workspace_id if membership else None})
    return {"access_token": token, "token_type": "bearer", "user": user_payload(user)}


@router.post("/logout")
def logout(
    authorization: str | None = Header(default=None),
    current_user: User = Depends(get_current_user),
):
    """
    主动登出：把当前令牌的 jti 写入 Redis 黑名单，使其立即失效。

    令牌有效期为 24 小时，若没有撤销出口，泄露的令牌在这段时间内无法作废。
    `revoke_access_token` 在 Redis 不可用时返回 False（降级为不撤销），
    此时仍返回 200——客户端该清本地令牌的动作不应被服务端缓存状态阻塞，
    但用 `revoked` 字段如实告知调用方撤销是否真的生效。
    """
    token = (authorization or "").split(" ", 1)[1].strip()
    return {"revoked": revoke_access_token(token)}


@router.get("/me")
def me(current_user: User = Depends(get_current_user), membership: WorkspaceMember = Depends(get_current_membership)):
    return {"user": user_payload(current_user), "membership": membership_payload(membership)}


@router.patch("/me")
def update_me(request: UserProfileUpdateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    patch = request.model_dump(exclude_unset=True)
    user = db.get(User, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if "name" in patch and patch["name"] is not None:
        user.name = patch["name"].strip()
    if "avatar_url" in patch:
        avatar_url = patch["avatar_url"] or ""
        if avatar_url and not avatar_url.startswith("data:image/"):
            raise HTTPException(status_code=400, detail="avatar_url must be an image data URL")
        user.avatar_url = avatar_url
    db.commit()
    db.refresh(user)
    return {"user": user_payload(user)}
