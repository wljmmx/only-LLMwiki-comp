"""认证依赖（P0-2 + P3-1 SSO 基础）

双模式认证，向后兼容：
1. 旧模式（P0-2）：单一共享 Token，通过 OPSKG_API_TOKEN 配置
2. 新模式（P3-1）：用户级 Session Token，由 /auth/login 签发

认证优先级：
- 若 OPSKG_API_TOKEN 已配置且匹配 → 放行（legacy 模式，返回 "user"）
- 否则尝试 session token 验证 → 返回用户对象
- 都不匹配 → 401

Header: Authorization: Bearer <token>
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)


async def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> str:
    """验证 Bearer Token（向后兼容 P0-2 行为）

    - 未配置 OPSKG_API_TOKEN 且无用户 → 开发模式放行（返回 "anonymous"）
    - OPSKG_API_TOKEN 匹配 → legacy 模式（返回 "user"）
    - session token 有效 → 新模式（返回 "user:<username>"）

    额外行为：解析 token 后将用户 dict 注入 request.state.user，
    供 AuditLogMiddleware 在 call_next 之后读取（who 字段）。
    不改变现有返回值与 401 行为。
    """
    settings = get_settings()
    expected_token = settings.api_token

    # 开发模式：未配置 legacy token 且无凭证 → 放行
    if not expected_token and not credentials:
        # 注入空用户（审计 middleware 据此判定 anonymous）
        request.state.user = None
        return "anonymous"

    if not credentials or credentials.scheme.lower() != "bearer":
        request.state.user = None
        raise HTTPException(
            401, "未提供认证凭证", headers={"WWW-Authenticate": "Bearer"}
        )

    token = credentials.credentials
    identity = verify_token_string(token)
    if identity is None:
        request.state.user = None
        raise HTTPException(401, "认证失败：Token 无效")
    # session token 模式：尝试注入用户 dict 供审计使用
    # legacy 模式（"user"）无用户对象，注入 None
    if identity.startswith("user:"):
        try:
            from app.auth.models import get_auth_store

            store = get_auth_store()
            user_obj = store.verify_session(token)
            request.state.user = user_obj
        except Exception:  # noqa: BLE001
            request.state.user = None
    else:
        request.state.user = None
    return identity


def verify_token_string(token: str | None) -> str | None:
    """基于 token 字符串校验身份（S15-5 WebSocket 鉴权复用）

    与 verify_token 同语义，但不依赖 HTTP 依赖注入链，便于 WebSocket / SSE
    等非 HTTP Bearer 场景复用。

    返回：
        - 开发模式（未配置 OPSKG_API_TOKEN 且 token 为空）→ "anonymous"
        - legacy 共享 token 匹配 → "user"
        - session token 有效 → "user:<username>"
        - 其他 → None（调用方决定如何拒绝）
    """
    settings = get_settings()
    expected_token = settings.api_token

    # 开发模式
    if not expected_token and not token:
        return "anonymous"

    if not token:
        return None

    # 1. legacy 共享 token
    if expected_token and secrets.compare_digest(token, expected_token):
        return "user"

    # 2. session token
    try:
        from app.auth.models import get_auth_store

        store = get_auth_store()
        user = store.verify_session(token)
        if user:
            return f"user:{user['username']}"
    except Exception:  # noqa: BLE001
        pass

    return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> dict[str, Any] | None:
    """获取当前登录用户（仅新模式有效，legacy/开发模式返回 None）

    用于需要用户身份上下文的端点（审计、个性化等）
    """
    settings = get_settings()
    expected_token = settings.api_token

    # 开发模式
    if not expected_token and not credentials:
        return None

    if not credentials or credentials.scheme.lower() != "bearer":
        return None

    token = credentials.credentials

    # legacy 模式无用户对象
    if expected_token and secrets.compare_digest(token, expected_token):
        return None

    # 新模式
    try:
        from app.auth.models import get_auth_store

        store = get_auth_store()
        return store.verify_session(token)
    except Exception:  # noqa: BLE001
        return None


def require_role(min_role: str):
    """角色守卫依赖工厂

    用法：@router.post("/...", dependencies=[Depends(require_role("operator"))])
    """

    async def _check(
        credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    ) -> str:
        settings = get_settings()
        expected_token = settings.api_token

        # 开发模式放行（无任何认证配置）
        if not expected_token and not credentials:
            return "anonymous"

        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                401,
                f"此操作需要 {min_role} 及以上角色",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = credentials.credentials

        # legacy 共享 token 视为 admin（兼容旧部署）
        if expected_token and secrets.compare_digest(token, expected_token):
            return "user"

        # 新模式：检查角色
        try:
            from app.auth.models import get_auth_store, has_role

            store = get_auth_store()
            user = store.verify_session(token)
            if user and has_role(user["role"], min_role):
                return f"user:{user['username']}"
            if user:
                raise HTTPException(
                    403, f"权限不足：需要 {min_role} 及以上角色"
                )
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            pass

        raise HTTPException(401, "认证失败：Token 无效")

    return _check


def generate_token() -> str:
    """生成随机 Token（用于初始化）"""
    return secrets.token_urlsafe(32)
