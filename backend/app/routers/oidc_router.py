"""OIDC / OAuth2 SSO 路由（P3-1 完整 SSO）

端点：
- GET  /auth/oidc/providers              列出已配置的 OIDC 提供者
- GET  /auth/oidc/{provider}             重定向到 IdP 授权页（带 PKCE + state）
- GET  /auth/oidc/{provider}/callback    IdP 回调，换取 token + 用户信息 → 签发 session → 重定向到前端
- GET  /auth/oidc/status                 检查 OIDC 是否启用
"""

from __future__ import annotations

import secrets
import urllib.parse
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth.oidc import (
    OIDCProvider,
    exchange_code,
    find_or_create_user,
    generate_pkce,
    parse_providers,
    pop_state,
    save_state,
)
from app.config import get_settings

router = APIRouter()


def _get_providers() -> list[OIDCProvider]:
    settings = get_settings()
    return parse_providers(settings.oidc_providers)


def _get_provider(name: str) -> OIDCProvider | None:
    for p in _get_providers():
        if p.name == name:
            return p
    return None


def _redirect_uri(request: Request, provider_name: str) -> str:
    """构造 callback 完整 URL"""
    settings = get_settings()
    if settings.oidc_redirect_base_url:
        base = settings.oidc_redirect_base_url.rstrip("/")
    else:
        # 从请求推断
        base = str(request.base_url).rstrip("/")
    return f"{base}/auth/oidc/{provider_name}/callback"


@router.get("/auth/oidc/status")
async def oidc_status() -> dict[str, Any]:
    """检查 OIDC SSO 是否启用"""
    providers = _get_providers()
    return {
        "enabled": len(providers) > 0,
        "providers": [p.to_dict() for p in providers],
    }


@router.get("/auth/oidc/providers")
async def list_oidc_providers() -> dict[str, Any]:
    """列出已配置的 OIDC 提供者（不含 client_secret）"""
    providers = _get_providers()
    return {
        "providers": [p.to_dict() for p in providers],
        "count": len(providers),
    }


@router.get("/auth/oidc/{provider}")
async def oidc_authorize(
    provider: str,
    request: Request,
    redirect: str = "",
) -> RedirectResponse:
    """重定向到 IdP 授权页

    Query 参数：
    - redirect: 登录后前端跳转目标（如 /dashboard）

    P0-2: 生成 nonce 并包含在 authorization request 中，防止重放攻击。
    """
    p = _get_provider(provider)
    if not p:
        raise HTTPException(404, f"未配置的 OIDC 提供者: {provider}")

    # 1. discovery
    try:
        await p.discover()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"OIDC discovery 失败: {e}") from e

    # 2. 生成 PKCE + state + nonce
    code_verifier, code_challenge = generate_pkce()
    nonce = secrets.token_urlsafe(24)  # P0-2: 防重放 nonce
    state = save_state(provider, code_verifier, redirect, nonce=nonce)

    # 3. 构造授权 URL（包含 nonce）
    callback = _redirect_uri(request, provider)
    params = {
        "response_type": "code",
        "client_id": p.client_id,
        "redirect_uri": callback,
        "scope": " ".join(p.scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "nonce": nonce,  # P0-2: nonce 参数
    }
    auth_url = f"{p.authorization_endpoint}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/auth/oidc/{provider}/callback")
async def oidc_callback(
    provider: str,
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
) -> RedirectResponse:
    """IdP 授权回调

    流程：
    1. 验证 state（一次性消费）
    2. 用 code 换取 access_token / id_token
    3. 获取用户信息（userinfo 或 id_token claims）
    4. 查找或创建本地用户
    5. 签发本地 session
    6. 重定向到前端 callback 页（带 token + redirect）
    """
    settings = get_settings()

    # IdP 错误（如用户拒绝授权）
    if error:
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            error,
            error_description,
        )

    # 校验必填参数
    if not code or not state:
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "invalid_callback",
            "缺少 code 或 state 参数",
        )

    # 消费 state（一次性）
    state_data = pop_state(state)
    if not state_data:
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "invalid_state",
            "state 无效或已过期",
        )
    if state_data["provider"] != provider:
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "provider_mismatch",
            "state 与 provider 不匹配",
        )

    p = _get_provider(provider)
    if not p:
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "unknown_provider",
            f"未配置的 OIDC 提供者: {provider}",
        )

    # 确保 discovery 完成
    try:
        await p.discover()
    except Exception as e:  # noqa: BLE001
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "discovery_failed",
            f"OIDC discovery 失败: {e}",
        )

    # 交换 token
    redirect_uri = _redirect_uri(request, provider)
    try:
        result = await exchange_code(
            p, code, state_data["code_verifier"], redirect_uri,
            nonce=state_data.get("nonce") or None,  # P0-2: 传递 nonce
        )
    except Exception as e:  # noqa: BLE001
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "token_exchange_failed",
            f"换取 token 失败: {e}",
        )

    user_info = result.get("user_info", {})
    sub = user_info.get("sub")
    if not sub:
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "no_sub",
            "IdP 未返回用户标识 (sub)",
        )

    email = user_info.get("email") or user_info.get("email_address")
    name = (
        user_info.get("name")
        or user_info.get("display_name")
        or user_info.get("preferred_username")
    )

    # 查找/创建本地用户
    try:
        from app.auth.models import get_auth_store

        user = find_or_create_user(
            provider=provider,
            sub=sub,
            email=email,
            name=name,
            default_role=settings.oidc_default_role,
        )
        # 签发本地 session
        store = get_auth_store()
        token = store.create_session(user["id"])
    except ValueError as e:
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "user_create_failed",
            str(e),
        )
    except Exception as e:  # noqa: BLE001
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "internal_error",
            f"内部错误: {e}",
        )

    # 重定向到前端 callback 页
    redirect_target = state_data.get("redirect") or ""
    params = {
        "token": token,
        "redirect": redirect_target,
    }
    frontend_url = f"{settings.frontend_base_url.rstrip('/')}/login/callback?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=frontend_url, status_code=302)


def _redirect_to_frontend_error(
    frontend_base_url: str,
    error_code: str,
    error_desc: str,
) -> RedirectResponse:
    """重定向到前端并附带错误信息"""
    params = {"error": error_code, "error_description": error_desc}
    url = f"{frontend_base_url.rstrip('/')}/login/callback?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url, status_code=302)
