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

from collections.abc import Callable

from fastapi import HTTPException, Request, status
from slowapi.util import get_remote_address


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

        client_id = get_remote_address(request) or "anonymous"
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
