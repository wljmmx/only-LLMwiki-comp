"""API 版本废弃中间件

为已废弃的 API 端点添加 Deprecation 和 Sunset 响应头，
帮助客户端及时迁移到新版本。

用法：
    from app.middleware.deprecation import DeprecationMiddleware
    app.add_middleware(
        DeprecationMiddleware,
        deprecated_paths={
            "/api/v1/old-endpoint": "2026-12-31",
        },
    )
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class DeprecationMiddleware(BaseHTTPMiddleware):
    """废弃 API 端点中间件

    为已废弃路径添加响应头：
    - Deprecation: true
    - Sunset: <YYYY-MM-DD>（移除日期）
    - Link: <新端点 URL>; rel="deprecation"（可选）
    """

    def __init__(
        self,
        app: Any,
        deprecated_paths: dict[str, str] | None = None,
        migration_map: dict[str, str] | None = None,
    ) -> None:
        """
        Args:
            deprecated_paths: {路径前缀: 移除日期}，如 {"/api/old": "2026-12-31"}
            migration_map: {旧路径前缀: 新路径前缀}，如 {"/api/old": "/api/v1/new"}
        """
        super().__init__(app)
        self._deprecated: dict[str, date] = {}
        for prefix, sunset_str in (deprecated_paths or {}).items():
            self._deprecated[prefix] = datetime.strptime(sunset_str, "%Y-%m-%d").date()
        self._migration = migration_map or {}

    def _is_deprecated(self, path: str) -> tuple[bool, str | None]:
        """检查路径是否已废弃，返回 (是否废弃, 移除日期)"""
        for prefix, sunset_date in self._deprecated.items():
            if path.startswith(prefix):
                return True, sunset_date.isoformat()
        return False, None

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)

        is_deprecated, sunset = self._is_deprecated(request.url.path)
        if is_deprecated:
            response.headers["Deprecation"] = "true"
            if sunset:
                response.headers["Sunset"] = sunset

            # 添加迁移链接
            for old_prefix, new_prefix in self._migration.items():
                if request.url.path.startswith(old_prefix):
                    new_path = request.url.path.replace(old_prefix, new_prefix, 1)
                    link = f'<{new_path}>; rel="deprecation"'
                    response.headers["Link"] = link
                    break

        return response