"""LDAP / Active Directory 认证路由（S13-2 企业级 SSO 补齐）

端点：
- GET  /auth/ldap/status               检查 LDAP 是否启用
- GET  /auth/ldap/providers            列出已配置的 LDAP 提供者
- POST /auth/ldap/{provider}/login     用户名/密码登录，验证 LDAP

LDAP 与 OIDC/SAML 路由独立，互不干扰。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.auth.ldap import (
    LDAPProvider,
    authenticate,
    extract_user_info_from_attributes,
    find_or_create_ldap_user,
    parse_ldap_providers,
)
from app.config import get_settings

router = APIRouter()


def _get_providers() -> list[LDAPProvider]:
    settings = get_settings()
    return parse_ldap_providers(settings.ldap_providers)


def _get_provider(name: str) -> LDAPProvider | None:
    for p in _get_providers():
        if p.name == name:
            return p
    return None


class LDAPLoginRequest(BaseModel):
    """LDAP 登录请求体"""

    username: str = Field(..., min_length=1, max_length=256)
    password: str = Field(..., min_length=1, max_length=1024)
    redirect: str = Field(default="", max_length=256)


@router.get("/auth/ldap/status")
async def ldap_status() -> dict[str, Any]:
    """检查 LDAP 认证是否启用"""
    providers = _get_providers()
    return {
        "enabled": len(providers) > 0,
        "providers": [p.to_dict() for p in providers],
    }


@router.get("/auth/ldap/providers")
async def list_ldap_providers() -> dict[str, Any]:
    """列出已配置的 LDAP 提供者（不含 bind_password）"""
    providers = _get_providers()
    return {
        "providers": [p.to_dict() for p in providers],
        "count": len(providers),
    }


@router.post("/auth/ldap/{provider}/login")
async def ldap_login(provider: str, body: LDAPLoginRequest) -> dict[str, Any]:
    """LDAP 用户名/密码登录

    流程：
    1. 用 LDAP 服务账号 bind + 搜索用户 DN
    2. 用找到的用户 DN + 提供的密码二次 bind 验证
    3. 提取用户属性（email/displayName 等）
    4. 查找或创建本地用户
    5. 签发本地 session token

    返回 JSON（非重定向，与 SAML/OIDC 不同）：
    {
        "success": true,
        "token": "...",
        "redirect": "...",
        "user": {...}
    }
    """
    settings = get_settings()
    p = _get_provider(provider)
    if not p:
        raise HTTPException(404, f"未配置的 LDAP 提供者: {provider}")

    # LDAP 认证（同步阻塞调用，ldap3 不支持 async）
    result = authenticate(p, body.username, body.password)
    if not result.success:
        return {
            "success": False,
            "error": result.error or "认证失败",
            "provider": provider,
        }

    if not result.user_dn:
        return {
            "success": False,
            "error": "未找到用户 DN",
            "provider": provider,
        }

    # 提取用户信息
    email, name = extract_user_info_from_attributes(
        username=body.username,
        attributes=result.attributes,
    )

    # 查找/创建本地用户
    try:
        from app.auth.models import get_auth_store

        user = find_or_create_ldap_user(
            provider=provider,
            user_dn=result.user_dn,
            username=body.username,
            email=email,
            name=name,
            default_role=settings.ldap_default_role,
        )
        # 签发本地 session
        store = get_auth_store()
        token = store.create_session(user["id"])
    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
            "provider": provider,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "success": False,
            "error": f"内部错误: {e}",
            "provider": provider,
        }

    return {
        "success": True,
        "token": token,
        "redirect": body.redirect,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "email": user["email"],
            "role": user["role"],
        },
        "provider": provider,
    }
