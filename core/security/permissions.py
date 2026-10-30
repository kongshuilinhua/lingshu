"""
灵枢 Agent 平台 —— RBAC 权限控制模块。

🎯 架构角色：
    本模块实现轻量级的基于角色的访问控制（Role-Based Access Control）。
    在整个请求链路中，权限检查发生在身份认证之后：
    JWT 验证 → 用户身份确认 → WorkspaceMember.role 提取 → can_manage() 判定

    当前设计为两级角色模型：
    - admin/owner: 管理员，可管理所属工作区的成员与资产
    - user/viewer: 普通用户，仅可操作自己的 Agent 和会话
    平台模型及后端 stdio 配置另由 can_manage_platform 限制。

    ⚡ 扩展性考虑：
    若未来需要更细粒度的权限（如按 Agent/知识库级别授权），
    建议引入 Permission 表或基于策略的权限引擎（如 Casbin），
    而非在此模块中堆叠条件判断。
"""


# 🧠 管理角色白名单：使用 frozenset 保证不可变性，防止运行时被意外修改
# "admin" 和 "owner" 语义等价，保留两者是为了兼容不同版本的前端传参
_MANAGE_ROLES = frozenset({"admin", "owner"})


def can_manage(role: str | None) -> bool:
    """
    判断给定角色是否具有管理权限。

    🎯 使用场景：
        - api/deps.py 中的 require_manager 依赖注入
        - 保护工作区管理员端点（成员角色调整、邀请成员等）

    🛡️ 防御性设计：
        - role 为 None 或空字符串时返回 False（安全默认拒绝原则）
        - 大小写敏感匹配，避免 "Admin" 等变体绕过检查
        - 使用 frozenset 的 O(1) 查找，即使角色种类扩展也保持恒定性能

    Args:
        role: 用户在工作空间中的角色字符串，来自 WorkspaceMember.role 字段

    Returns:
        True 表示具有管理权限，False 表示仅有普通用户权限
    """
    return (role or "") in _MANAGE_ROLES


def normalize_role(role: str | None) -> str:
    """
    规整角色字段值，剔除首尾空白，转换为小写。
    如果是非法角色或者为空，默认退化为 "user"。
    """
    if not role:
        return "user"
    cleaned = str(role).strip().lower()
    if cleaned in {"owner", "admin"}:
        return cleaned
    return "user"


def can_manage_platform(membership) -> bool:
    """平台配置沿用初始 default 工作区的管理员授权，不授予普通注册者。"""
    return can_manage(membership.role) and membership.workspace.slug == "default"
