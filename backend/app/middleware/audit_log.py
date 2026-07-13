"""审计日志中间件 — 捕获写操作并记录到 audit_log 表

设计：
- 仅记录写操作（POST/PUT/PATCH/DELETE），跳过 GET / OPTIONS / HEAD
- 跳过高频读与基础设施路径：/health、/metrics、/api/auth/me、静态资源
- 记录字段：who / what / when / status / duration_ms / request_id
- 请求体摘要截断 200 字符，敏感字段脱敏
- 用 structlog logger（名称 "audit"）记录 audit.write 事件
- 同时写入 audit_log 表（SQLite，复用 AuditStore）

敏感字段最小集：password / token / secret / api_key / authorization
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import get_settings
from app.storage.audit_store import AuditStore, get_audit_store

# 独立 audit logger（与业务日志分离，便于单独收集/审计）
audit_logger = structlog.get_logger("audit")

# 敏感字段最小集（小写匹配）
_SENSITIVE_KEYS = frozenset({
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
    "authorization",
})

# 默认跳过的路径（高频读 / 基础设施 / 静态资源）
_DEFAULT_EXEMPT_PATHS = frozenset({
    "/health",
    "/ready",
    "/metrics",
    "/auth/me",
    "/tracing/status",
})

# 写方法集合
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# 请求体摘要最大长度
_PAYLOAD_SUMMARY_MAX = 200

# 敏感字段的正则（用于非 JSON 文本的脱敏，匹配 "key":"value" 或 key=value）
_SENSITIVE_JSON_PATTERN = re.compile(
    r'("(?:' + "|".join(re.escape(k) for k in _SENSITIVE_KEYS) + r')"\s*:\s*)"[^"]*"',
    re.IGNORECASE,
)


def _mask_sensitive_value(key: str) -> bool:
    """判断字段名是否敏感（大小写不敏感）"""
    return key.lower() in _SENSITIVE_KEYS


def _mask_payload(body_bytes: bytes) -> str:
    """对请求体进行脱敏 + 截断，返回摘要字符串

    1. 尝试 JSON 解析 → 对敏感 key 的值替换为 "***"
    2. 解析失败 → 用正则匹配 "key":"value" 模式脱敏
    3. 截断到 200 字符
    """
    if not body_bytes:
        return ""

    text = body_bytes.decode("utf-8", errors="replace")

    # 尝试 JSON 解析（结构化脱敏，最可靠）
    try:
        parsed = json.loads(text)
        masked = _mask_recursive(parsed)
        text = json.dumps(masked, ensure_ascii=False)
    except (json.JSONDecodeError, ValueError):
        # 非 JSON：用正则脱敏 "password":"xxx" 这类模式
        text = _SENSITIVE_JSON_PATTERN.sub(r'\1"***"', text)

    # 截断到 200 字符
    if len(text) > _PAYLOAD_SUMMARY_MAX:
        return text[:_PAYLOAD_SUMMARY_MAX]
    return text


def _mask_recursive(obj: Any) -> Any:
    """递归脱敏 dict/list 中的敏感字段"""
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for k, v in obj.items():
            if _mask_sensitive_value(str(k)):
                result[k] = "***"
            else:
                result[k] = _mask_recursive(v)
        return result
    if isinstance(obj, list):
        return [_mask_recursive(item) for item in obj]
    return obj


def _get_user_from_request(request: Request) -> str:
    """从 request.state.user 提取用户名

    verify_token 依赖解析 token 后注入 request.state.user（dict 或 None）。
    middleware 在 call_next 之后读取（此时依赖已执行）。
    """
    user = getattr(request.state, "user", None)
    if isinstance(user, dict):
        username = user.get("username") or user.get("id")
        if username:
            return str(username)
    # anonymous 兜底（含 dev 模式、legacy token、未登录）
    return "anonymous"


def _get_client_ip(request: Request) -> str:
    """获取客户端 IP（与 rate_limit.get_client_ip 同语义）"""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    xri = request.headers.get("X-Real-IP")
    if xri:
        return xri.strip()
    if request.client is not None:
        return request.client.host
    return ""


def _is_exempt(path: str, extra_paths: set[str]) -> bool:
    """判断路径是否应跳过审计"""
    if path in _DEFAULT_EXEMPT_PATHS or path in extra_paths:
        return True
    # 静态资源：以 /static/ 或 /assets/ 开头，或含文件扩展名
    if path.startswith("/static/") or path.startswith("/assets/"):
        return True
    # /metrics/internal 等子路径
    if path.startswith("/metrics"):
        return True
    return False


class AuditLogMiddleware(BaseHTTPMiddleware):
    """审计日志中间件 — 仅记录写操作

    注册位置：CORS 之后、路由之前（在 main.py 中通过 app.add_middleware 注册）。
    """

    def __init__(self, app: Any, store: AuditStore | None = None) -> None:
        super().__init__(app)
        # 允许测试注入 store（DB 隔离）
        self._store = store

    def _get_store(self) -> AuditStore:
        """获取 AuditStore（延迟到运行时，确保 monkeypatch 生效）"""
        if self._store is not None:
            return self._store
        return get_audit_store()

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """拦截写操作并记录审计日志"""
        settings = get_settings()

        # 关闭审计时直接放行
        if not settings.audit_log_enabled:
            return await call_next(request)

        method = request.method
        path = request.url.path

        # 仅记录写操作
        if method not in _WRITE_METHODS:
            return await call_next(request)

        # 解析额外豁免路径
        extra_paths: set[str] = set()
        if settings.audit_log_paths:
            extra_paths = {
                p.strip() for p in settings.audit_log_paths.split(",") if p.strip()
            }

        # 跳过豁免路径
        if _is_exempt(path, extra_paths):
            return await call_next(request)

        # 读取请求体（Starlette _CachedRequest 会缓存并回放给下游）
        body_bytes = await request.body()
        payload_summary = _mask_payload(body_bytes)

        # request_id：优先用 X-Request-ID header，否则自动生成
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        # 写入 request.state 供下游（如异常处理器）复用
        request.state.request_id = request_id

        user_agent = request.headers.get("User-Agent", "")
        client_ip = _get_client_ip(request)

        # 计时 + 转发请求
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            # 在 call_next 之后读取 user（此时 verify_token 依赖已注入 request.state.user）
            user = _get_user_from_request(request)
            try:
                # 用线程池执行同步 SQLite 写入，避免阻塞事件循环
                # （BaseHTTPMiddleware 的 call_next coro 需要事件循环空闲才能完成）
                await asyncio.to_thread(
                    self._get_store().log_write,
                    user=user,
                    method=method,
                    path=path,
                    status=status_code,
                    duration_ms=duration_ms,
                    request_id=request_id,
                    payload_summary=payload_summary,
                    user_agent=user_agent,
                    ip=client_ip,
                )
            except Exception as e:  # noqa: BLE001
                # 审计日志失败不应阻断业务请求
                audit_logger.error("audit.log_write_failed", error=str(e), path=path)


# ────────── 单例 / 工厂 ──────────

_audit_middleware: AuditLogMiddleware | None = None


def get_audit_middleware() -> AuditLogMiddleware:
    """获取 AuditLogMiddleware 单例（供测试与外部访问）"""
    global _audit_middleware
    if _audit_middleware is None:
        _audit_middleware = AuditLogMiddleware(app=None)  # type: ignore[arg-type]
    return _audit_middleware


__all__ = [
    "AuditLogMiddleware",
    "audit_logger",
    "get_audit_middleware",
]
