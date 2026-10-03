"""
灵枢 Agent 平台 —— 安全认证模块。

🎯 架构角色：
    本模块是平台安全体系的基石，提供两大核心能力：
    1. 密码哈希：使用 PBKDF2-HMAC-SHA256 将明文密码转为不可逆摘要
    2. JWT 令牌：签发/验证无状态的访问令牌，驱动 API 层的身份识别

    在整个请求生命周期中，本模块处于最前端：
    HTTP 请求 → FastAPI deps.py(提取 Bearer Token) → auth.decode_access_token → 用户身份确认
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from base64 import urlsafe_b64decode, urlsafe_b64encode

from core.config import get_settings


# 🛡️ 验签侧的算法白名单。本模块的签名实现是硬编码的 HMAC-SHA256，
# 因此这里只接受 HS256——配置项若被改成其它值，请求会明确失败而不是静默降级。
_ALLOWED_ALGORITHMS = {"HS256"}


# ═══════════════════════════════════════════════════════════════
# 密码哈希（Password Hashing）
# ═══════════════════════════════════════════════════════════════


def hash_password(password: str) -> str:
    """
    将明文密码转化为安全的不可逆哈希值。

    🎯 设计意图：
        使用 PBKDF2-HMAC-SHA256 慢哈希算法，通过大量迭代将计算成本提升到
        暴力破解不可行的程度。每次调用随机生成 16 字节盐值(salt)，确保相同
        密码产生不同摘要，抵御彩虹表攻击。

    🧠 参数解析：
        - iterations=210000: OWASP 2023 推荐的最低迭代次数。
          每次哈希需要约 100-200ms CPU 时间，攻击者暴力枚举 1 亿个密码
          需要约 231 天（单核），有效遏制离线破解。
          值过低(如 1000) → 毫秒级可破解；值过高(如 100万) → 登录延迟不可接受。
        - salt=16字节: 128 位随机盐，碰撞概率 < 2^-64，实践中视为不可能。

    返回格式: "pbkdf2:iterations:hex(salt):hex(hash)"
    """
    salt = os.urandom(16)
    iterations = 210_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2:{iterations}:{salt.hex()}:{dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """
    验证用户输入的明文密码是否与存储的哈希匹配。

    🛡️ 防御性设计：
        1. 使用 hmac.compare_digest 做恒定时间比较，防止计时侧信道攻击
           (timing attack)：攻击者无法通过响应时间差异推断哈希的前缀匹配程度
        2. 外层 try/except 兜底所有解析错误（格式损坏、字段缺失等），
           确保任何异常情况都返回 False 而非抛出异常泄漏内部信息
    """
    try:
        parts = stored.split(":")
        # 格式校验：必须为 "pbkdf2:iterations:salt:hash" 四段
        if len(parts) != 4 or parts[0] != "pbkdf2":
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        stored_hash = bytes.fromhex(parts[3])
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        # 🛡️ 恒定时间比较：即使哈希不匹配，比较耗时也相同，防止计时攻击
        return hmac.compare_digest(dk, stored_hash)
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
# JWT 令牌（JSON Web Token）
# ═══════════════════════════════════════════════════════════════

# 🎯 设计决策：手动实现 JWT 而非使用 PyJWT 库
# 原因：减少第三方依赖、完全掌控安全边界、代码量极小（<30行核心逻辑）
# 局限：仅支持 HS256，若需 RS256 非对称签名或 JWK 密钥轮转则应引入标准库


def create_access_token(data: dict) -> str:
    """
    签发 JWT 访问令牌。

    🎯 工作原理：
        JWT 由三部分组成：Header.Payload.Signature
        - Header: 声明签名算法（HS256）
        - Payload: 携带标准 JWT 声明 + 业务声明
        - Signature: HMAC-SHA256(Header.Payload, secret)，保证完整性

    🧠 标准声明（RFC 7519）：
        - sub (Subject): 令牌主体，通常为用户 ID
        - exp (Expiration Time): 过期时间戳
        - nbf (Not Before): 生效时间，允许 30 秒时钟偏差容错
        - iss (Issuer): 签发者标识，固定为 "lingshu-agent"
        - jti (JWT ID): 令牌唯一标识符，用于支持令牌撤销（黑名单）

    🧠 过期时间设计：
        默认 24 小时有效期，过期后前端需引导重新登录。

    ⚡ 性能：纯 Python 计算，单次签发 < 0.1ms，无 I/O 开销。
    """
    settings = get_settings()
    now_ts = int(time.time())
    payload = dict(data)
    payload["exp"] = now_ts + settings.access_token_minutes * 60
    payload["nbf"] = now_ts - 30              # 允许 30 秒时钟偏差
    payload["iss"] = "lingshu-agent"
    payload["jti"] = uuid.uuid4().hex          # 唯一令牌 ID，用于撤销
    # JWT Header：固定为 HS256 对称签名
    header = {"alg": settings.jwt_algorithm, "typ": "JWT"}
    # 构造 Header.Payload 的 Base64URL 编码
    h = _b64url_encode(json.dumps(header).encode())
    p = _b64url_encode(json.dumps(payload).encode())
    signing_input = f"{h}.{p}"
    # HMAC-SHA256 签名：使用 jwt_secret 作为密钥
    sig = hmac.new(settings.jwt_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(sig)}"


def decode_access_token(token: str) -> dict:
    """
    验证并解码 JWT 访问令牌。

    🛡️ 安全校验链：
        1. 结构完整性：必须为三段式 Header.Payload.Signature 格式
        2. 签名验证：重新计算 HMAC 并恒定时间比较，防止篡改
        3. 过期检查：exp 必须大于当前时间戳
        4. 生效检查：nbf 必须小于等于当前时间戳（允许 30 秒时钟偏差）
        5. 签发者验证：iss 必须为 "lingshu-agent"
        6. 主体检查：sub 字段必须存在，代表用户 ID
        7. 撤销检查：jti 不在 Redis 黑名单中（如果 Redis 可用）

    Raises:
        ValueError: 任何验证失败
    """
    settings = get_settings()
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid token format")
    h, p, s = parts
    # 🛡️ Header 校验：显式断言 alg 在白名单内。
    # 原实现把 header 当不透明串直接参与 HMAC 计算，从不解析 alg。当前签发端固定
    # HS256 所以尚不可被 alg:none 直接打穿，但 jwt_algorithm 是可配置项，一旦改成
    # RS256 或引入第二条签发路径，验签侧不会跟着变，会静默退化成「用公钥当 HMAC
    # 密钥」的 algorithm confusion 漏洞。自研 JWT 必须把 alg 白名单写死在验签侧。
    try:
        header = json.loads(_b64url_decode(h))
    except (ValueError, json.JSONDecodeError):
        raise ValueError("Invalid token header")
    if header.get("alg") not in _ALLOWED_ALGORITHMS:
        raise ValueError("Unsupported token algorithm")
    if header.get("alg") != settings.jwt_algorithm:
        raise ValueError("Unexpected token algorithm")
    if str(header.get("typ", "JWT")).upper() != "JWT":
        raise ValueError("Unsupported token type")
    # 🛡️ 签名验证：恒定时间比较，防止计时攻击
    expected = hmac.new(settings.jwt_secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_decode(s), expected):
        raise ValueError("Invalid signature")
    payload = json.loads(_b64url_decode(p))
    now_ts = int(time.time())
    # 🛡️ 过期校验
    if payload.get("exp", 0) < now_ts:
        raise ValueError("Token expired")
    # 🛡️ 生效时间校验（nbf — Not Before）
    if payload.get("nbf", 0) > now_ts:
        raise ValueError("Token not yet valid")
    # 🛡️ 签发者校验
    if payload.get("iss") != "lingshu-agent":
        raise ValueError("Invalid issuer")
    # 🛡️ 主体校验
    if "sub" not in payload:
        raise ValueError("Missing subject")
    # 🛡️ 撤销校验（如 Redis 可用）
    if not _token_is_active(payload.get("jti", ""), payload.get("exp", 0)):
        raise ValueError("Token revoked")
    return payload


def _b64url_encode(data: bytes) -> str:
    """Base64URL 编码（无填充），符合 RFC 7515 JWS 规范。"""
    return urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    """
    Base64URL 解码（自动补填充）。

    🛡️ 补齐 '=' 填充字符：Base64 编码长度必须是 4 的倍数，
    JWT 规范中传输时会省略填充符，解码前需要还原。
    """
    s += "=" * (-len(s) % 4)
    return urlsafe_b64decode(s)


# ═══════════════════════════════════════════════════════════════
# 令牌撤销 (Token Revocation)
# ═══════════════════════════════════════════════════════════════


def _token_is_active(jti: str, exp: int) -> bool:
    """
    检查令牌是否未被撤销。

    查询 jti 是否在 Redis 黑名单中：
    - 命中黑名单 → 已撤销，返回 False。
    - 未命中 / Redis 不可用 → 视为有效（降级放行），返回 True。

    🛡️ `redis_store.exists()` 内部已对「未配置 / 连接故障」做降级（返回 False），
        因此此处无需再包一层宽 except —— 让真正的代码缺陷能够暴露。
    """
    if not jti:
        return True
    from core.services.rag_cache import redis_store

    return not redis_store.exists(f"revoked:{jti}")


def revoke_access_token(token: str) -> bool:
    """
    撤销访问令牌——将 jti 加入 Redis 黑名单。

    🎯 使用场景：
        - 用户主动登出
        - 管理员强制下线某用户
        - 检测到令牌滥用

    黑名单 TTL 设置为令牌剩余有效期，过期后自动清理。

    返回 True 表示撤销成功，False 表示令牌不合法或 Redis 不可用（降级）。
    """
    parts = token.split(".")
    if len(parts) != 3:
        return False
    # 仅对令牌解析做窄异常兜底；Redis 写入失败由 set_string 内部降级处理。
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, json.JSONDecodeError):
        return False
    jti = payload.get("jti", "")
    exp = payload.get("exp", 0)
    if not jti:
        return False
    ttl = max(1, exp - int(time.time()))
    from core.services.rag_cache import redis_store

    return redis_store.set_string(f"revoked:{jti}", ttl, "1")
