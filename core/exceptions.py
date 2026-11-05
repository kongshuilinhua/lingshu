"""
灵枢 Agent 平台 —— 统一异常层次体系。

🎯 架构角色：
    本模块是平台所有异常的单一来源（Single Source of Truth）。
    两层异常体系设计：
    - ErrorCode 枚举：可机器读取的错误码 + 人类可读的默认消息
    - AppException 基类：携带 ErrorCode、可选自定义消息、HTTP 状态码

    曾按 HTTP 语义铺过一套子类（BadRequest/Forbidden/NotFound/...），实际只有
    Unauthorized 与 Forbidden 被抛过。其余子类连同 41 个零引用错误码一并删除：
    没人 throw 的异常类型只是让人误以为「这里已经处理过了」。

    在整个请求链路中，异常体系处于最外层的防御圈：
    业务代码抛 AppException → FastAPI exception_handler 拦截 → 统一 JSON 响应格式
"""

from enum import Enum


class ErrorCode(Enum):
    """
    平台统一错误码枚举。

    🎯 设计意图：
        将错误码定义为枚举而非自由字符串，确保：
        1. 编译时即可发现错误码拼写错误
        2. 新增错误类型必须显式声明
        3. 前端可以按 error_code 做精确的交互降级（而非对 message 做正则匹配）
    """

    # ⚡ 只保留真正被抛出的错误码。早先这里按业务域铺了 47 个，
    #    其中 41 个从未被任何代码引用——路由层一律用 HTTPException(status_code, detail)，
    #    前端也只看 message，枚举名和编号都是死的。
    #    加新错误码前先确认确实有一条 raise AppException(ErrorCode.X)，否则别加。

    # ── 通用系统异常 ──────────────────────────────────────────
    INTERNAL_ERROR = ("SYS_001", "内部服务异常")

    # ── 认证与授权 ──────────────────────────────────────────
    INVALID_TOKEN = ("AUTH_001", "令牌无效或已过期")
    INACTIVE_USER = ("AUTH_002", "用户已被禁用")
    PERMISSION_DENIED = ("AUTH_003", "无操作权限")

    # ── 安全 ──────────────────────────────────────────────
    ENCRYPTION_NOT_CONFIGURED = ("SEC_001", "API 密钥加密未配置，无法存储敏感凭据")

    # ── 参数校验 ──────────────────────────────────────────
    VALIDATION_ERROR = ("VAL_001", "请求参数不正确")

    @property
    def code(self) -> str:
        """返回机器可读的错误码字符串，如 'AUTH_001'。"""
        return self.value[0]

    @property
    def default_message(self) -> str:
        """返回人类可读的默认错误描述。"""
        return self.value[1]


class AppException(Exception):
    """
    平台统一异常基类。

    🎯 设计意图：
        每个 AppException 实例携带：
        - error_code: ErrorCode 枚举成员，提供机器可读的错误标识
        - message: 人类可读的错误描述（可选，未提供时回退到 ErrorCode 默认消息）
        - status_code: HTTP 状态码，供 FastAPI exception_handler 使用

    ⚡ 现状：路由层绝大多数地方用的是 HTTPException(status_code, detail)，由
        api/main.py 的 http_exception_handler 补成统一响应体。AppException 只用于
        「业务层没有 Request 上下文、又需要携带错误码」的场景（鉴权、密钥加密）。
        两条路径最终都落到同一个 JSON 形状上，所以不要求统一写法。
    """

    def __init__(
        self,
        error_code: ErrorCode,
        message: str | None = None,
        status_code: int = 500,
    ):
        self.error_code = error_code
        self.message = message or error_code.default_message
        self.status_code = status_code
        super().__init__(self.message)


class UnauthorizedException(AppException):
    """401 Unauthorized —— 认证凭证缺失/无效/过期。"""

    def __init__(self, error_code: ErrorCode = ErrorCode.INVALID_TOKEN, message: str | None = None):
        super().__init__(error_code, message, status_code=401)


class ForbiddenException(AppException):
    """403 Forbidden —— 认证通过但无访问权限。"""

    def __init__(self, error_code: ErrorCode = ErrorCode.PERMISSION_DENIED, message: str | None = None):
        super().__init__(error_code, message, status_code=403)
