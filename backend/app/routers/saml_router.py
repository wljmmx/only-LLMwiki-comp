"""SAML 2.0 SSO 路由（S13-1 企业级 SSO 补齐）

端点：
- GET  /auth/saml/status                   检查 SAML 是否启用
- GET  /auth/saml/providers                列出已配置的 SAML 提供者
- GET  /auth/saml/{provider}/metadata      返回 SP metadata XML
- GET  /auth/saml/{provider}/login         触发 SP 发起 SSO，重定向到 IdP
- POST /auth/saml/{provider}/acs           ACS 端点（HTTP-POST binding）
- GET  /auth/saml/{provider}/acs           ACS 端点（HTTP-Redirect binding 变体）

SAML 与 OIDC 路由独立，互不干扰。同时启用时前端可同时展示两组登录入口。
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from app.auth.saml import (
    SAMLProvider,
    extract_user_info_from_attributes,
    find_or_create_saml_user,
    parse_saml_providers,
    pop_relay_state,
    save_relay_state,
)
from app.config import get_settings

router = APIRouter()


def _get_providers() -> list[SAMLProvider]:
    settings = get_settings()
    return parse_saml_providers(settings.saml_providers)


def _get_provider(name: str) -> SAMLProvider | None:
    for p in _get_providers():
        if p.name == name:
            return p
    return None


def _build_request_dict(request: Request) -> dict[str, Any]:
    """将 FastAPI Request 转换为 python3-saml 所需的 dict

    python3-saml 的 OneLogin_Saml2_Auth 接受 'dict-like' 对象，需含：
    - 'https': 是否 HTTPS（'on' / 'off'）
    - 'http_host': Host header
    - 'server_port': 端口
    - 'script_name': URI 路径
    - 'get_data': query 参数 dict
    - 'post_data': 表单参数 dict
    """
    headers = request.headers
    scheme = "https" if request.url.scheme == "https" or headers.get("x-forwarded-proto") == "https" else "http"
    return {
        "https": "on" if scheme == "https" else "off",
        "http_host": headers.get("host", "localhost"),
        "server_port": str(request.url.port or (443 if scheme == "https" else 80)),
        "script_name": request.url.path,
        "get_data": dict(request.query_params),
        "post_data": {},
    }


def _redirect_to_frontend_error(
    frontend_base_url: str,
    error_code: str,
    error_desc: str,
) -> RedirectResponse:
    """重定向到前端并附带错误信息"""
    params = {"error": error_code, "error_description": error_desc}
    url = f"{frontend_base_url.rstrip('/')}/login/callback?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url, status_code=302)


@router.get("/auth/saml/status")
async def saml_status() -> dict[str, Any]:
    """检查 SAML SSO 是否启用"""
    providers = _get_providers()
    return {
        "enabled": len(providers) > 0,
        "providers": [p.to_dict() for p in providers],
    }


@router.get("/auth/saml/providers")
async def list_saml_providers() -> dict[str, Any]:
    """列出已配置的 SAML 提供者（不含证书/私钥）"""
    providers = _get_providers()
    return {
        "providers": [p.to_dict() for p in providers],
        "count": len(providers),
    }


@router.get("/auth/saml/{provider}/metadata")
async def saml_metadata(provider: str) -> Response:
    """返回 SP metadata XML（IdP 配置时填入此 URL）"""
    p = _get_provider(provider)
    if not p:
        raise HTTPException(404, f"未配置的 SAML 提供者: {provider}")

    # 延迟导入：仅在调用时需要 python3-saml
    from onelogin.saml2.settings import OneLogin_Saml2_Settings

    settings_dict = p.to_settings_dict(strict=get_settings().saml_strict)
    saml_settings = OneLogin_Saml2_Settings(settings=settings_dict, sp_validation_only=True)
    meta_xml = saml_settings.get_sp_metadata()
    return PlainTextResponse(
        content=meta_xml,
        media_type="application/xml",
    )


@router.get("/auth/saml/{provider}/login")
async def saml_login(
    provider: str,
    request: Request,
    redirect: str = "",
) -> RedirectResponse:
    """触发 SP 发起 SSO，重定向到 IdP SSO URL

    Query 参数：
    - redirect: 登录后前端跳转目标（如 /dashboard）
    """
    p = _get_provider(provider)
    if not p:
        raise HTTPException(404, f"未配置的 SAML 提供者: {provider}")

    # 延迟导入
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    settings = get_settings()
    settings_dict = p.to_settings_dict(strict=settings.saml_strict)
    req_dict = _build_request_dict(request)
    auth = OneLogin_Saml2_Auth(req_dict, settings_dict)

    # 生成 relay_state 保持登录后跳转目标
    relay_state = save_relay_state(provider, redirect)

    # login() 返回 IdP SSO URL（含 SAMLRequest 参数）
    sso_url = auth.login(return_to=relay_state)
    return RedirectResponse(url=sso_url, status_code=302)


@router.post("/auth/saml/{provider}/acs")
async def saml_acs_post(
    provider: str,
    request: Request,
    SAMLResponse: str = Form(...),
    RelayState: str = Form(""),
) -> RedirectResponse:
    """ACS 端点（HTTP-POST binding）— IdP 通过 POST 提交 SAML Response"""
    return await _process_acs(provider, request, SAMLResponse, RelayState)


@router.get("/auth/saml/{provider}/acs")
async def saml_acs_get(
    provider: str,
    request: Request,
    SAMLResponse: str = "",
    RelayState: str = "",
) -> RedirectResponse:
    """ACS 端点（HTTP-Redirect binding 变体）— 部分 IdP 用 GET 提交"""
    if not SAMLResponse:
        settings = get_settings()
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "missing_saml_response",
            "缺少 SAMLResponse 参数",
        )
    return await _process_acs(provider, request, SAMLResponse, RelayState)


async def _process_acs(
    provider: str,
    request: Request,
    saml_response: str,
    relay_state: str,
) -> RedirectResponse:
    """ACS 公共处理逻辑"""
    settings = get_settings()

    p = _get_provider(provider)
    if not p:
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "unknown_provider",
            f"未配置的 SAML 提供者: {provider}",
        )

    # 延迟导入
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    settings_dict = p.to_settings_dict(strict=settings.saml_strict)
    req_dict = _build_request_dict(request)
    # 注入 POST 数据（python3-saml 通过 req['post_data'] 读取 SAMLResponse）
    req_dict["post_data"] = {"SAMLResponse": saml_response}
    auth = OneLogin_Saml2_Auth(req_dict, settings_dict)

    try:
        auth.process_response()
    except Exception as e:  # noqa: BLE001
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "process_failed",
            f"SAML Response 处理失败: {e}",
        )

    errors = auth.get_errors()
    if errors:
        error_reason = auth.get_last_error_reason() or "; ".join(errors)
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "saml_error",
            f"SAML 错误: {error_reason}",
        )

    if not auth.is_authenticated():
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "not_authenticated",
            "SAML 认证失败",
        )

    # 提取用户信息
    nameid = auth.get_nameid()
    if not nameid:
        return _redirect_to_frontend_error(
            settings.frontend_base_url,
            "no_nameid",
            "SAML Response 未包含 NameID",
        )

    attributes: dict[str, Any] = auth.get_attributes() or {}
    email, name = extract_user_info_from_attributes(nameid, attributes)

    # 查找/创建本地用户
    try:
        from app.auth.models import get_auth_store

        user = find_or_create_saml_user(
            provider=provider,
            nameid=nameid,
            email=email,
            name=name,
            default_role=settings.saml_default_role,
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

    # 消费 relay_state（取出 redirect 目标）
    redirect_target = ""
    if relay_state:
        state_data = pop_relay_state(relay_state)
        if state_data and state_data.get("provider") == provider:
            redirect_target = state_data.get("redirect", "")

    # 重定向到前端 callback 页
    params = {
        "token": token,
        "redirect": redirect_target,
    }
    frontend_url = f"{settings.frontend_base_url.rstrip('/')}/login/callback?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=frontend_url, status_code=302)
