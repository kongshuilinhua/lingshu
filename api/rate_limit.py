"""
灵枢 Agent 平台 —— API 频率限制模块。

🎯 架构角色：
    只保留一条限流链路：基于 Redis ZSET 滑动窗口的分布式限流依赖，进程间共享计数，
    多 worker / 多实例下额度一致；Redis 不可用时 fail-open 放行（软依赖）。

    历史上这里还配置过 slowapi 的内存计数器（`default_limits=["200/minute"]`），
    但它既没注册 `SlowAPIMiddleware` 也从未被任何端点用 `@limiter.limit` 装饰，
    实际从未生效——已移除，避免「看起来有全局限流」的错觉。
    需要全局限流时应改用本模块的 `redis_rate_limit`，它才是多副本下正确的实现。

    建议配额：
    - 聊天流式端点：30 请求/分钟（高消耗）
    - 注册端点：10 请求/分钟（防滥用）
    - 登录端点：20 请求/分钟
"""

import ipaddress
from collections.abc import Callable

from fastapi import HTTPException, Request, status

from core.config import get_settings


def client_address(request: Request) -> str:
    """
    取真实客户端 IP，用于限流计数。

    🎯 为什么要看 X-Forwarded-For：部署形态是同源 nginx 反代（见 frontend/nginx.conf），
        反代后 `request.client.host` 恒为 nginx 容器 IP。只信它的话，所有用户的
        登录/注册/对话配额会合并成一份全局配额——一个人打满就能把所有人挡在外面。

    🛡️ 只有当直连方本身是受信代理时才采纳转发头：否则任何人加一行
        `X-Forwarded-For: 任意值` 就能给自己换一个配额桶，限流形同虚设。
        受信列表默认是私网/回环（容器网络），可用 LINGSHU_TRUSTED_PROXIES 覆盖。
    """
    peer = request.client.host if request.client else ""
    if not _is_trusted_proxy(peer):
        return peer or "anonymous"
    forwarded = request.headers.get("x-forwarded-for") or ""
    # 取最左一跳：右侧是逐跳追加的，最左边才是原始客户端
    candidate = forwarded.split(",")[0].strip()
    return candidate or peer


def _is_trusted_proxy(address: str) -> bool:
    if not address:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    configured = (get_settings().trusted_proxies or "").strip()
    if configured:
        for item in configured.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                if ip in ipaddress.ip_network(item, strict=False):
                    return True
            except ValueError:
                continue
        return False
    return ip.is_private or ip.is_loopback


def redis_rate_limit(limit: int, window_seconds: int, *, scope: str) -> Callable[[Request], None]:
    """
    生成一个基于 Redis 滑动窗口的 FastAPI 限流依赖。

    🎯 用法：`dependencies=[Depends(redis_rate_limit(10, 60, scope="register"))]`
        或作为参数 `_: None = Depends(redis_rate_limit(...))`。

    🛡️ 按「scope + 客户端 IP」隔离计数；超限抛 429 并带 Retry-After。
        Redis 不可用时 rate_limit_check 返回放行，不阻断请求。
    """

    def dependency(request: Request) -> None:
        from core.services.rag_cache import redis_store

        client_id = client_address(request)
        allowed, _remaining = redis_store.rate_limit_check(f"rl:{scope}:{client_id}", limit, window_seconds)
        if not allowed:
            from core.observability.metrics import record_rate_limit_rejection

            record_rate_limit_rejection(scope)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"message": "请求过于频繁，请稍后再试。", "error_code": "rate_limited"},
                headers={"Retry-After": str(window_seconds)},
            )

    return dependency
