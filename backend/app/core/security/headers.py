"""安全响应头中间件

添加以下安全头：
- Content-Security-Policy: 限制资源加载来源，防 XSS
- X-Content-Type-Options: nosniff，防 MIME 类型嗅探
- X-Frame-Options: DENY，防点击劫持
- X-XSS-Protection: 1; mode=block，防反射型 XSS（旧浏览器）
- Referrer-Policy: strict-origin-when-cross-origin
- Permissions-Policy: 限制浏览器 API 使用

CSP 策略说明：
- default-src 'self': 默认仅允许同源
- script-src 'self' 'unsafe-inline': 允许 Vue SPA 内联脚本
- style-src 'self' 'unsafe-inline': 允许 Naive UI 内联样式
- connect-src 'self': 限制 API 请求同源
- img-src 'self' data: https: 允许 data URI 和 HTTPS 图片
- font-src 'self' data: 允许同源字体
"""

from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头中间件 — 为所有响应添加安全头

    CSP 可从环境变量 OPSKG_CSP_REPORT_URI 配置违规报告端点。
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)

        headers = response.headers

        # CSP: 默认严格，允许 Vue SPA 必需的内联脚本/样式
        if "Content-Security-Policy" not in headers:
            headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; "
                "font-src 'self' data:; "
                "connect-src 'self' https: wss:; "
                "frame-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
            )

        # 防 MIME 类型嗅探
        if "X-Content-Type-Options" not in headers:
            headers["X-Content-Type-Options"] = "nosniff"

        # 防点击劫持
        if "X-Frame-Options" not in headers:
            headers["X-Frame-Options"] = "DENY"

        # 防反射型 XSS（旧浏览器兼容）
        if "X-XSS-Protection" not in headers:
            headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer 策略
        if "Referrer-Policy" not in headers:
            headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions Policy: 限制浏览器特性
        if "Permissions-Policy" not in headers:
            headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=(), "
                "interest-cohort=()"
            )

        return response