"""OIDC / OAuth2 客户端（P3-1 完整 SSO）

支持多 OIDC 提供者（Google / GitHub / Keycloak 等），基于 Authorization Code Flow + PKCE。
首次 OIDC 登录自动创建本地用户（角色由 oidc_default_role 配置）。

流程：
1. GET  /auth/oidc/providers       → 列出可用 OIDC 提供者
2. GET  /auth/oidc/{provider}       → 重定向到 IdP 授权页
3. GET  /auth/oidc/{provider}/callback → IdP 回调，换取 token + 用户信息 → 签发本地 session
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

# ────────── OIDC 提供者配置 ──────────


class OIDCProvider:
    """单个 OIDC 提供者配置"""

    def __init__(
        self,
        name: str,
        display_name: str,
        client_id: str,
        client_secret: str,
        discovery_url: str,
        scopes: list[str] | None = None,
        **_extra: Any,
    ):
        self.name = name
        self.display_name = display_name
        self.client_id = client_id
        self.client_secret = client_secret
        self.discovery_url = discovery_url
        self.scopes = scopes or ["openid", "email", "profile"]
        # 运行时缓存（discovery 后填充）
        self._authorization_endpoint: str | None = None
        self._token_endpoint: str | None = None
        self._userinfo_endpoint: str | None = None
        self._jwks_uri: str | None = None
        self._discovered = False

    async def discover(self) -> None:
        """从 .well-known/openid-configuration 获取端点信息"""
        if self._discovered:
            return
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(self.discovery_url)
            resp.raise_for_status()
            doc = resp.json()
        self._authorization_endpoint = doc.get("authorization_endpoint")
        self._token_endpoint = doc.get("token_endpoint")
        self._userinfo_endpoint = doc.get("userinfo_endpoint")
        self._jwks_uri = doc.get("jwks_uri")
        self._discovered = True
        logger.info(
            "oidc.discovered",
            provider=self.name,
            auth_endpoint=self._authorization_endpoint,
            token_endpoint=self._token_endpoint,
        )

    @property
    def authorization_endpoint(self) -> str:
        if not self._authorization_endpoint:
            raise RuntimeError(f"OIDC provider {self.name} not discovered yet")
        return self._authorization_endpoint

    @property
    def token_endpoint(self) -> str:
        if not self._token_endpoint:
            raise RuntimeError(f"OIDC provider {self.name} not discovered yet")
        return self._token_endpoint

    @property
    def userinfo_endpoint(self) -> str | None:
        return self._userinfo_endpoint

    def to_dict(self) -> dict[str, Any]:
        """用于 /auth/oidc/providers 返回（不含 client_secret）"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "scopes": self.scopes,
        }


def parse_providers(json_str: str) -> list[OIDCProvider]:
    """解析环境变量中的 OIDC 提供者 JSON 配置"""
    if not json_str.strip():
        return []
    try:
        items = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning("oidc.parse_failed", error=str(e))
        return []
    providers = []
    for item in items:
        try:
            providers.append(OIDCProvider(**item))
        except Exception as e:  # noqa: BLE001
            logger.warning("oidc.provider_skip", item=item.get("name"), error=str(e))
    return providers


# ────────── PKCE 辅助 ──────────


def generate_pkce() -> tuple[str, str]:
    """生成 PKCE code_verifier + code_challenge (S256)"""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = (
        base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    )
    return verifier, challenge


# ────────── OAuth2 state 存储 ──────────

# 简单内存 state 存储（单实例足够；多实例需用 Redis/DB）
# state → {provider, code_verifier, redirect, created_at}
_state_store: dict[str, dict[str, Any]] = {}
STATE_TTL = 600  # 10 分钟


def save_state(
    provider: str, code_verifier: str, redirect: str = ""
) -> str:
    """生成 state 并保存"""
    state = secrets.token_urlsafe(32)
    _state_store[state] = {
        "provider": provider,
        "code_verifier": code_verifier,
        "redirect": redirect,
        "created_at": time.time(),
    }
    # 清理过期
    _cleanup_states()
    return state


def pop_state(state: str) -> dict[str, Any] | None:
    """取出并删除 state（一次性）"""
    return _state_store.pop(state, None)


def _cleanup_states() -> None:
    now = time.time()
    expired = [k for k, v in _state_store.items() if now - v["created_at"] > STATE_TTL]
    for k in expired:
        del _state_store[k]


# ────────── OIDC 用户映射 ──────────

# OIDC 用户映射存储（oidc_id → local user_id）
MAPPING_DB_PATH = Path(__file__).parent.parent.parent / "data" / "auth.db"


def _get_mapping_db() -> sqlite3.Connection:
    MAPPING_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MAPPING_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS oidc_mappings (
            provider TEXT NOT NULL,
            oidc_sub TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            oidc_email TEXT,
            oidc_name TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (provider, oidc_sub),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    conn.commit()
    return conn


def find_or_create_user(
    provider: str,
    sub: str,
    email: str | None,
    name: str | None,
    default_role: str = "viewer",
) -> dict[str, Any]:
    """根据 OIDC sub 查找本地用户，不存在则自动创建"""
    from app.auth.models import get_auth_store

    conn = _get_mapping_db()
    try:
        row = conn.execute(
            "SELECT * FROM oidc_mappings WHERE provider = ? AND oidc_sub = ?",
            (provider, sub),
        ).fetchone()

        if row:
            # 更新映射信息
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE oidc_mappings SET oidc_email = ?, oidc_name = ?, updated_at = ?
                   WHERE provider = ? AND oidc_sub = ?""",
                (email, name, now, provider, sub),
            )
            conn.commit()
            store = get_auth_store()
            user = store.get_user_by_id(row["user_id"])
            if user and user["active"]:
                return user
            # 用户被禁用或删除
            logger.warning("oidc.user_disabled", provider=provider, sub=sub)
            raise ValueError("用户已被禁用")

        # 首次登录，自动创建用户
        store = get_auth_store()
        # 用 email 或 sub 作为 username
        username = email or f"{provider}_{sub[:32]}"
        # 去除邮箱域名部分作为 display_name
        display_name = name or (email.split("@")[0] if email else username)
        # 确保 username 唯一
        existing = store.get_user(username)
        if existing:
            # 如果 username 冲突，追加 provider 前缀
            username = f"{provider}_{username}"
            existing = store.get_user(username)
            if existing:
                username = f"{provider}_{sub[:32]}"

        user = store.create_user(
            username=username,
            password=secrets.token_urlsafe(32),  # 随机密码（OIDC 用户不走密码登录）
            role=default_role,
            display_name=display_name,
            email=email,
        )

        # 创建映射
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO oidc_mappings (provider, oidc_sub, user_id, oidc_email, oidc_name, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (provider, sub, user["id"], email, name, now),
        )
        conn.commit()

        logger.info(
            "oidc.user_created",
            provider=provider,
            sub=sub,
            username=username,
            role=default_role,
        )
        return user
    finally:
        conn.close()


# ────────── Token 交换 ──────────


async def exchange_code(
    provider: OIDCProvider,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """用 authorization code 换取 token + 用户信息"""
    async with httpx.AsyncClient(timeout=15) as client:
        # 交换 token
        token_resp = await client.post(
            provider.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": provider.client_id,
                "client_secret": provider.client_secret,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        access_token = token_data.get("access_token", "")
        id_token = token_data.get("id_token", "")

        # 获取用户信息（优先 userinfo 端点，其次解析 id_token）
        user_info: dict[str, Any] = {}
        if provider.userinfo_endpoint and access_token:
            try:
                ui_resp = await client.get(
                    provider.userinfo_endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                ui_resp.raise_for_status()
                user_info = ui_resp.json()
            except Exception as e:  # noqa: BLE001
                logger.warning("oidc.userinfo_failed", provider=provider.name, error=str(e))

        # 如果 userinfo 没有返回 sub，从 id_token 中解析（不验证签名，仅提取 claims）
        if not user_info.get("sub") and id_token:
            try:
                user_info = _decode_id_token_claims(id_token)
            except Exception as e:  # noqa: BLE001
                logger.warning("oidc.id_token_parse_failed", provider=provider.name, error=str(e))

        return {
            "access_token": access_token,
            "token_type": token_data.get("token_type", "Bearer"),
            "expires_in": token_data.get("expires_in"),
            "id_token": id_token,
            "user_info": user_info,
        }


def _decode_id_token_claims(id_token: str) -> dict[str, Any]:
    """从 JWT id_token 中提取 claims（不验证签名，仅 base64 解码 payload）

    注意：生产环境应验证 JWT 签名。此处为简化实现，
    在 userinfo 端点可用时优先用 userinfo，id_token 仅作 fallback。
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise ValueError("无效 JWT 格式")
    payload = parts[1]
    # 补齐 base64 padding
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding
    decoded = base64.urlsafe_b64decode(payload)
    return json.loads(decoded)
