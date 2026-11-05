from fastapi import Depends, Header
from sqlalchemy.orm import Session

from core.db.models import User, WorkspaceMember
from core.db.session import get_db
from core.exceptions import (
    ErrorCode,
    ForbiddenException,
    UnauthorizedException,
)
from core.security.auth import decode_access_token
from core.security.permissions import can_manage


def get_token_payload(authorization: str | None = Header(default=None)) -> dict:
    """
    解析并校验 Bearer 令牌，返回 payload。

    🎯 单独抽出来是为了「只解一次」：`get_current_user` 要 `sub`，
        `get_current_membership` 要 `workspace_id`。两边各自解码同一枚令牌纯属浪费，
        更需要紧的是两边解出来的内容必须来自同一次校验，不能一个成功一个失败。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedException(ErrorCode.INVALID_TOKEN, message="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return decode_access_token(token)
    except (ValueError, KeyError):
        raise UnauthorizedException(ErrorCode.INVALID_TOKEN)


def get_current_user(
    payload: dict = Depends(get_token_payload),
    db: Session = Depends(get_db),
) -> User:
    """
    FastAPI 统一会话令牌校验与用户主体注入器（全平台 API 的“第一道安全护城河”）。

    🎯 意图与工程大局观：
        负责拦截进入系统的每一个受保护 HTTP 请求，解析 Authorization 请求头，
        对 Bearer 令牌实施高强度物理鉴权与完整性指纹解密，并最终返回数据库对应的 `User` 实体，
        为下游的租户路由提供了绝对一致的用户上下文环境。

    🛡️ 防御性编程与大模型兜底：
        - 从源头上验证 Bearer Token 语法格式。
        - 用 try-catch 全面包围 JWT 签名解密（`decode_access_token`），
          防止由于签名过期、被恶意撞库篡改、非法的密钥碰撞等情况导致底层 Crash。
          凡有任何解密异常，一致平滑转换为标准的 `HTTP_401_UNAUTHORIZED` 异常抛出。
        - 在数据库取回 `User` 实体后，强力验证其 `is_active` 状态，
          确保已被管理员注销或拉黑的恶意账户在首帧就被强行切断，无法接触平台资产。
    """
    try:
        user_id = int(payload["sub"])
    except (ValueError, KeyError, TypeError):
        raise UnauthorizedException(ErrorCode.INVALID_TOKEN)
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise UnauthorizedException(ErrorCode.INACTIVE_USER)
    return user


def get_current_membership(
    current_user: User = Depends(get_current_user),
    payload: dict = Depends(get_token_payload),
    db: Session = Depends(get_db),
) -> WorkspaceMember:
    """
    多租户隔离层：当前工作区成员身份的动态解析与安全认证器。

    🎯 意图与工程大局观：
        本系统采用多租户隔离架构。用户的任何智能体、知识库资产全部归属于特定的 `Workspace`（工作区）。
        即使一个用户的 Bearer 令牌有效（401 校验通过），但如果他在当前工作区没有合法的 WorkspaceMember 记录，
        依旧绝对无法访问任何平台数据。
        本注入器用于动态拦截多租户水平越权攻击，守护租户数据绝对隐私。

    🎯 为什么读令牌里的 workspace_id：登录时签发的 claim 就是这个用户「本次登录想进哪个工作区」。
        早先这里只按 user_id 取 id 最小的一条成员身份，等于把 claim 当摆设——
        被邀请进第二个工作区的用户永远落在最早那个工作区，邀请接受成功却看不到新工作区的任何东西。
        令牌里的工作区无效时（成员被移除）回落到最早一条，保证老令牌不至于把人锁死在外面。
    """
    claim_workspace_id = payload.get("workspace_id")
    query = db.query(WorkspaceMember).filter(WorkspaceMember.user_id == current_user.id)
    if claim_workspace_id is not None:
        try:
            membership = query.filter(WorkspaceMember.workspace_id == int(claim_workspace_id)).first()
        except (TypeError, ValueError):
            membership = None
        if membership:
            return membership
    membership = query.order_by(WorkspaceMember.id.asc()).first()
    if not membership:
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED, message="No workspace membership")
    return membership


def require_manager(membership: WorkspaceMember = Depends(get_current_membership)) -> WorkspaceMember:
    """
    细粒度 RBAC 权限守门人：强制校验超级管理员 / 经理等高级资产管理角色。

    🎯 意图与工程大局观：
        用于工作区成员管理等管理 API；全平台配置须使用 require_platform_manager。
        通过级联依赖注入（Depends）链条：
        `get_current_user` (身份校验) -> `get_current_membership` (租户物理隔离) -> `require_manager` (管理权能校验)，
        用极简、声明式的工程化 API 代码逻辑，封堵了越权提权的任何通道。
    """
    if not can_manage(membership.role):
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED, message="Admin role required")
    return membership


def require_platform_manager(membership: WorkspaceMember = Depends(get_current_membership)) -> WorkspaceMember:
    from core.security.permissions import can_manage_platform

    if not can_manage_platform(membership):
        raise ForbiddenException(ErrorCode.PERMISSION_DENIED, message="Admin role required")
    return membership
