"""
灵枢 Agent 平台 —— 工作区成员与邀请服务。

🎯 架构角色：
    把「谁能进这个工作区、他是什么角色、邀请何时失效」这三件事从路由里拿出来，
    路由只负责 HTTP 形状与依赖注入。守卫逻辑集中在这里，改一处即全链路生效。

⚡ 两级角色模型（core/security/permissions.py）：
    admin/owner 可管理成员与全部资产；user 只能动自己创建的东西。
    owner 与 admin 权限等价，但 owner 记录不可改亦不可删——没有所有权转移流程时，
    允许删掉 owner 会让工作区永久没有可管理的账号。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.config import get_settings
from core.db.models import User, WorkspaceInvite, WorkspaceMember
from core.security.permissions import can_manage, normalize_role

# 邀请里允许指定的角色。owner 不在其列：所有权只能随首个管理员产生。
INVITABLE_ROLES = frozenset({"user", "admin"})


def utcnow_naive() -> datetime:
    """MySQL 的 DateTime 列不带时区，比较前统一去掉 tzinfo，避免 aware/naive 相减报错。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def db_now() -> datetime:
    """
    写入 DATETIME 列用的当前时间，微秒归零。

    🎯 为什么必须截断：MySQL 的 DATETIME 默认秒精度，落库时会把微秒四舍五入——
        07:28:37.552 存成 07:28:38，比真实时间还晚半秒。这个偏移在按 created_at
        推算过期时刻时会变成「刚创建就还差半秒才算过期」，有效期取得很短时判定就飘了。
    """
    return utcnow_naive().replace(microsecond=0)


def invite_expires_at(invite: WorkspaceInvite) -> datetime | None:
    if not invite.created_at:
        return None
    return invite.created_at + timedelta(hours=get_settings().invite_expire_hours)


def invite_is_expired(invite: WorkspaceInvite) -> bool:
    expires_at = invite_expires_at(invite)
    return bool(expires_at and expires_at < utcnow_naive())


def ensure_usable_invite(invite: WorkspaceInvite | None) -> WorkspaceInvite:
    """校验一枚邀请仍然可用：存在、未接受、未过期。三种情况对调用方都是「链接不能用」。"""
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.accepted_at is not None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite_is_expired(invite):
        raise HTTPException(status_code=410, detail="Invite expired")
    return invite


def create_invite(db: Session, *, workspace_id: int, email: str, role: str) -> WorkspaceInvite:
    """
    创建邀请。

    🎯 同邮箱重发即换 token：同一个待接受的邀请再发一次，若新增一行，
        旧链接依然有效，等于链接越点越多、谁也说不清哪条还算数。
        这里直接复用那一行并重置 token 与创建时间，新链接生效、旧链接当场作废。
    """
    normalized_role = normalize_role(role)
    if normalized_role not in INVITABLE_ROLES:
        raise HTTPException(status_code=400, detail="Invite role must be user or admin")
    address = email.strip().lower()
    if db.query(User).filter(User.email == address).first():
        # 已有账号的人该走「接受邀请」，再发一封注册邀请只会让人注册时撞 409。
        raise HTTPException(status_code=400, detail="该邮箱已有账号，请让对方登录后使用邀请链接加入")

    invite = (
        db.query(WorkspaceInvite)
        .filter(
            WorkspaceInvite.workspace_id == workspace_id,
            WorkspaceInvite.email == address,
            WorkspaceInvite.accepted_at.is_(None),
        )
        .order_by(WorkspaceInvite.id.desc())
        .first()
    )
    if invite:
        invite.role = normalized_role
        invite.token = secrets.token_urlsafe(24)
        invite.created_at = db_now()
    else:
        invite = WorkspaceInvite(
            workspace_id=workspace_id,
            email=address,
            role=normalized_role,
            token=secrets.token_urlsafe(24),
        )
        db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


def accept_invite(db: Session, *, invite: WorkspaceInvite, user: User) -> WorkspaceMember:
    """
    接受邀请：补齐成员身份并把用户「上次所在工作区」指过来。

    🎯 为什么要改 last_active_workspace_id：用户本来在别的工作区，接受邀请后如果
        这个字段不动，下一次登录签发的令牌还指向旧工作区——邀请接受成功却什么都看不到。
    """
    membership = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == invite.workspace_id, WorkspaceMember.user_id == user.id)
        .first()
    )
    if not membership:
        membership = WorkspaceMember(
            workspace_id=invite.workspace_id,
            user_id=user.id,
            role=normalize_role(invite.role),
        )
        db.add(membership)
    invite.accepted_at = db_now()
    user.last_active_workspace_id = invite.workspace_id
    db.commit()
    db.refresh(membership)
    return membership


def _require_manageable_target(db: Session, membership: WorkspaceMember, member_id: int) -> WorkspaceMember:
    """取出要操作的目标成员，并拦下不该被改动的对象。"""
    target = db.get(WorkspaceMember, member_id)
    if not target or target.workspace_id != membership.workspace_id:
        raise HTTPException(status_code=404, detail="Member not found")
    if target.user_id == membership.user_id:
        # 改自己被降级、删自己退组，都会把管理员当场锁在门外，只能由另一位管理员来做。
        raise HTTPException(status_code=400, detail="不能修改自己的成员身份")
    if normalize_role(target.role) == "owner":
        raise HTTPException(status_code=400, detail="工作区所有者不可修改")
    return target


def _admin_count(db: Session, workspace_id: int) -> int:
    rows = db.query(WorkspaceMember).filter(WorkspaceMember.workspace_id == workspace_id).all()
    return sum(1 for row in rows if can_manage(row.role))


def update_member_role(db: Session, *, membership: WorkspaceMember, member_id: int, role: str) -> WorkspaceMember:
    target = _require_manageable_target(db, membership, member_id)
    normalized_role = normalize_role(role)
    if normalized_role not in INVITABLE_ROLES:
        raise HTTPException(status_code=400, detail="角色只能是普通用户或管理员")
    if can_manage(target.role) and not can_manage(normalized_role) and _admin_count(db, membership.workspace_id) <= 1:
        raise HTTPException(status_code=400, detail="工作区至少要保留一位管理员")
    target.role = normalized_role
    db.commit()
    db.refresh(target)
    return target


def remove_member(db: Session, *, membership: WorkspaceMember, member_id: int) -> int:
    target = _require_manageable_target(db, membership, member_id)
    if can_manage(target.role) and _admin_count(db, membership.workspace_id) <= 1:
        raise HTTPException(status_code=400, detail="工作区至少要保留一位管理员")
    user_id = target.user_id
    db.delete(target)
    db.commit()
    return user_id
