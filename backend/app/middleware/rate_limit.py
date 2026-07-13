"""API 限流中间件（基于 slowapi）

提供：
- limiter 单例：基于 IP 的全局限流器（60 req/min/IP，可配置）
- apply_rate_limit(limit) 装饰器工厂：供任意端点应用更严格的限制
- rate_limit_exceeded_handler：429 异常处理器，返回 JSON {error, detail, retry_after}
- get_client_ip：key_func，优先级 X-Forwarded-For > X-Real-IP > request.client.host
- GlobalRateLimitMiddleware：全局限流中间件（对所有非豁免路径生效）

兼容性说明：
- slowapi.middleware.SlowAPIMiddleware 在新版 Starlette（1.x）下无法匹配
  FastAPI _IncludedRouter 内的路由（handler 查找返回 None → 被判为豁免）。
  因此 GlobalRateLimitMiddleware 负责全局限流。
- SlowAPIMiddleware 仍注册（供 @limiter.limit() 装饰的端点使用，如登录端点），
  但不设 default_limits，避免对 /health 等直接定义的路由误限。
- 豁免路径（/health、/setup/status、/metrics 等）通过 _EXEMPT_PATHS 集合判断。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from limits import parse as parse_limit
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

# 默认豁免路径（不限流）
_EXEMPT_PATHS = frozenset({
    "/health",
    "/ready",
    "/metrics",
    "/setup/status",
    "/tracing/status",
})

# 全局限流计数器的 scope（所有非豁免路径共享同一计数器）
_GLOBAL_SCOPE = "global_api"


def get_client_ip(request: Request) -> str:
    """获取客户端真实 IP（key_func）

    优先级：
    1. X-Forwarded-For 第一个 IP（经过反向代理时由代理写入）
    2. X-Real-IP（部分代理如 Nginx 会写入）
    3. request.client.host（直连场景）

    返回：
        客户端 IP 字符串；无法确定时返回 "unknown"
    """
    # X-Forwarded-For 优先（可能含多个 IP，取第一个即客户端真实 IP）
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    # X-Real-IP 次之
    xri = request.headers.get("X-Real-IP")
    if xri:
        return xri.strip()
    # 最后回退到 socket 对端地址
    if request.client is not None:
        return request.client.host
    return "unknown"


def _build_limiter() -> Limiter:
    """根据当前 settings 构建 Limiter 单例

    不设 default_limits —— 全局限流由 GlobalRateLimitMiddleware 手动处理。
    这样 SlowAPIMiddleware 不会对 /health 等直接定义的路由误限。
    SlowAPIMiddleware 仅负责 @limiter.limit() 装饰的端点（如登录端点的严格限流）。
    """
    return Limiter(
        key_func=get_client_ip,
        headers_enabled=True,
    )


# 模块级单例：导入即创建，main.py 注册到 app.state.limiter
limiter: Limiter = _build_limiter()

# 全局限流项（parse 一次复用，所有非豁免路径共享）
_global_limit_item = parse_limit(get_settings().rate_limit_default)


async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """429 限流异常处理器

    返回 JSON 格式：
    {
        "error": "rate_limit_exceeded",
        "detail": "<limit 描述>",
        "retry_after": <秒数>
    }

    retry_after 从 exc 关联的 limit 的重置时间推算；无法解析时回退到 60 秒。
    """
    # 尝试从异常关联的 limit 提取重置时间
    retry_after = 60
    detail = str(exc.detail) if hasattr(exc, "detail") else str(exc)
    try:
        # slowapi 的 RateLimitExceeded 携带 limit 对象，可查重置时间
        if hasattr(exc, "limit") and exc.limit is not None:
            if hasattr(exc.limit, "limit_item"):
                item = exc.limit.limit_item
                # 窗口大小（秒）作为 retry_after 上界
                bucket = item.get_expiry()
                retry_after = min(max(int(bucket), 1), 60)
    except Exception:  # noqa: BLE001
        pass
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "detail": detail,
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


def apply_rate_limit(limit: str | None = None):
    """端点限流装饰器工厂

    用法：
        @router.post("/auth/login")
        @apply_rate_limit("10/minute")
        async def login(...): ...

    Args:
        limit: 限流规则字符串（如 "10/minute"）。None 时使用 settings.rate_limit_default。

    Returns:
        slowapi 的 limit 装饰器，应用到端点上
    """
    if limit is None:
        limit = get_settings().rate_limit_default
    return limiter.limit(limit)


def _build_429_response(ip: str) -> JSONResponse:
    """构造全局限流 429 响应

    从 limiter 的存储查询窗口重置时间，计算 retry_after。
    """
    retry_after = 60
    try:
        # 查询当前窗口的重置时间和剩余请求数
        reset_at, _remaining = limiter.limiter.get_window_stats(
            _global_limit_item, ip, _GLOBAL_SCOPE
        )
        now = time.time()
        retry_after = max(1, int(reset_at - now) + 1)
        # 上界 60 秒（与窗口大小一致）
        retry_after = min(retry_after, 60)
    except Exception:  # noqa: BLE001
        pass
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "detail": f"{_global_limit_item.amount} per {_global_limit_item.get_expiry()}s",
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    """全局限流中间件 — 对所有非豁免路径应用默认限流

    补充 SlowAPIMiddleware 在新版 Starlette 下无法匹配 _IncludedRouter
    路由的问题。直接使用 limiter._limiter（底层 RateLimiter）执行限流检查，
    所有非豁免请求共享 _GLOBAL_SCOPE 作为计数器 scope（全局 60/min/IP）。
    """

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        # 关闭限流时直接放行
        if not limiter.enabled:
            return await call_next(request)

        path = request.url.path

        # 豁免路径不限流
        if path in _EXEMPT_PATHS:
            return await call_next(request)

        # /metrics/internal 等子路径豁免
        if path.startswith("/metrics"):
            return await call_next(request)

        # 用 limiter 的底层 RateLimiter 执行 hit（增量计数 + 判断是否超限）
        ip = get_client_ip(request)
        if not limiter.limiter.hit(_global_limit_item, ip, _GLOBAL_SCOPE):
            # 超限：返回 429
            return _build_429_response(ip)

        response = await call_next(request)
        return response


def configure_rate_limit(app: Any) -> None:
    """在 FastAPI app 上注册限流中间件与异常处理器

    在 main.py 中 app 创建后调用：
    - app.state.limiter = limiter
    - 注册 RateLimitExceeded 异常处理器
    - 注册 SlowAPIMiddleware（供 @limiter.limit() 装饰的端点使用）
    - 注册 GlobalRateLimitMiddleware（全局限流，对所有非豁免路径生效）

    若 settings.rate_limit_enabled=False，则禁用 limiter（全开放）。
    """
    settings = get_settings()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    # SlowAPIMiddleware：处理 @limiter.limit() 装饰的端点（如登录端点的严格限流）
    # 不设 default_limits，避免对 /health 等直接定义的路由误限
    app.add_middleware(SlowAPIMiddleware)
    # GlobalRateLimitMiddleware：全局限流，对所有非豁免路径生效
    app.add_middleware(GlobalRateLimitMiddleware)

    if not settings.rate_limit_enabled:
        # 关闭限流：所有请求放行
        limiter.enabled = False


__all__ = [
    "GlobalRateLimitMiddleware",
    "apply_rate_limit",
    "configure_rate_limit",
    "get_client_ip",
    "limiter",
    "rate_limit_exceeded_handler",
]
