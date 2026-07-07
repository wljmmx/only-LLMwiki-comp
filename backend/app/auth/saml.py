"""SAML 2.0 SSO 客户端（S13-1 企业级 SSO 补齐）

基于 python3-saml（OneLogin SAML Toolkit），支持 Okta / Azure AD / Keycloak 等 SAML IdP。
首次 SAML 登录自动创建本地用户（角色由 saml_default_role 配置）。

流程：
1. GET  /auth/saml/status                    → 检查 SAML 是否启用
2. GET  /auth/saml/providers                 → 列出已配置的 SAML 提供者
3. GET  /auth/saml/{provider}/metadata       → 返回 SP metadata XML（IdP 配置时填入）
4. GET  /auth/saml/{provider}/login          → 触发 SP 发起 SSO，重定向到 IdP
5. POST /auth/saml/{provider}/acs            → ACS 端点（Assertion Consumer Service），接收 IdP SAML Response
6. GET  /auth/saml/{provider}/acs            → ACS GET 变体（部分 IdP 用 GET 回调）

配置格式（SAML_PROVIDERS 环境变量，JSON 数组）：
[
  {
    "name": "okta",
    "display_name": "Okta",
    "sp_entity_id": "https://opskg.example.com/saml/sp",
    "acs_url": "https://opskg.example.com/auth/saml/okta/acs",
    "slo_url": "https://opskg.example.com/auth/saml/okta/sls",
    "idp_entity_id": "http://www.okta.com/exk...",
    "idp_sso_url": "https://your-org.okta.com/app/...",
    "idp_slo_url": "https://your-org.okta.com/app/.../slo",
    "idp_x509cert": "MIID...",
    "sp_x509cert": "...",        # 可选，签名 AuthnRequest
    "sp_private_key": "..."      # 可选
  }
]

留空则关闭 SAML SSO。
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# ────────── SAML 提供者配置 ──────────


class SAMLProvider:
    """单个 SAML IdP 提供者配置

    封装 IdP 元数据 + SP 端点信息，可转换为 python3-saml 所需的 settings dict。
    """

    def __init__(
        self,
        name: str,
        display_name: str,
        sp_entity_id: str,
        acs_url: str,
        idp_entity_id: str,
        idp_sso_url: str,
        idp_x509cert: str,
        slo_url: str | None = None,
        idp_slo_url: str | None = None,
        sp_x509cert: str = "",
        sp_private_key: str = "",
        want_assertions_signed: bool = True,
        want_assertions_encrypted: bool = False,
        **_extra: Any,
    ):
        self.name = name
        self.display_name = display_name
        self.sp_entity_id = sp_entity_id
        self.acs_url = acs_url
        self.slo_url = slo_url or ""
        self.idp_entity_id = idp_entity_id
        self.idp_sso_url = idp_sso_url
        self.idp_slo_url = idp_slo_url or ""
        self.idp_x509cert = idp_x509cert
        self.sp_x509cert = sp_x509cert
        self.sp_private_key = sp_private_key
        self.want_assertions_signed = want_assertions_signed
        self.want_assertions_encrypted = want_assertions_encrypted

    def to_settings_dict(self, strict: bool = True) -> dict[str, Any]:
        """转换为 python3-saml OneLogin_Saml2_Settings 所需的 settings dict

        结构遵循 python3-saml 文档：
        https://github.com/onelogin/python3-saml#how-to-use-it
        """
        settings: dict[str, Any] = {
            "strict": strict,
            "debug": False,
            "sp": {
                "entityId": self.sp_entity_id,
                "assertionConsumerService": {
                    "url": self.acs_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
                "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
                "x509cert": self.sp_x509cert,
                "privateKey": self.sp_private_key,
            },
            "idp": {
                "entityId": self.idp_entity_id,
                "singleSignOnService": {
                    "url": self.idp_sso_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "x509cert": self.idp_x509cert,
            },
            "security": {
                "nameIdEncrypted": False,
                "authnRequestsSigned": bool(self.sp_x509cert and self.sp_private_key),
                "logoutRequestSigned": bool(self.sp_x509cert and self.sp_private_key),
                "logoutResponseSigned": bool(self.sp_x509cert and self.sp_private_key),
                "signMetadata": False,
                "wantMessagesSigned": self.want_assertions_signed,
                "wantAssertionsSigned": self.want_assertions_signed,
                "wantAssertionsEncrypted": self.want_assertions_encrypted,
                "wantNameId": True,
                "wantNameIdEncrypted": False,
                "requestedAuthnContext": False,
                "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
                "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
            },
        }

        # SLO 端点（可选）
        if self.slo_url and self.idp_slo_url:
            settings["sp"]["singleLogoutService"] = {
                "url": self.slo_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            }
            settings["idp"]["singleLogoutService"] = {
                "url": self.idp_slo_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            }

        return settings

    def to_dict(self) -> dict[str, Any]:
        """用于 /auth/saml/providers 返回（不含证书/私钥）"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "sp_entity_id": self.sp_entity_id,
            "acs_url": self.acs_url,
            "idp_entity_id": self.idp_entity_id,
            "idp_sso_url": self.idp_sso_url,
            "has_slo": bool(self.slo_url and self.idp_slo_url),
            "want_assertions_signed": self.want_assertions_signed,
        }


def parse_saml_providers(json_str: str) -> list[SAMLProvider]:
    """解析环境变量中的 SAML 提供者 JSON 配置"""
    if not json_str or not json_str.strip():
        return []
    try:
        items = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning("saml.parse_failed", error=str(e))
        return []
    if not isinstance(items, list):
        logger.warning("saml.config_not_list")
        return []
    providers = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            providers.append(SAMLProvider(**item))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "saml.provider_skip",
                name=item.get("name"),
                error=str(e),
            )
    return providers


# ────────── SAML relay state 存储 ──────────

# 与 OIDC 一样使用内存 state 存储（单实例；多实例需用 Redis/DB）
# relay_state → {provider, redirect, created_at}
_relay_store: dict[str, dict[str, Any]] = {}
RELAY_TTL = 600  # 10 分钟


def save_relay_state(provider: str, redirect: str = "") -> str:
    """生成 relay_state 并保存（用于 SP → IdP → ACS 流程中保持上下文）"""
    state = secrets.token_urlsafe(32)
    _relay_store[state] = {
        "provider": provider,
        "redirect": redirect,
        "created_at": time.time(),
    }
    _cleanup_relay_states()
    return state


def pop_relay_state(state: str) -> dict[str, Any] | None:
    """取出并删除 relay_state（一次性）"""
    return _relay_store.pop(state, None)


def _cleanup_relay_states() -> None:
    now = time.time()
    expired = [k for k, v in _relay_store.items() if now - v["created_at"] > RELAY_TTL]
    for k in expired:
        del _relay_store[k]


# ────────── SAML 用户映射 ──────────

# SAML 用户映射存储（provider + nameid → local user_id）
# 复用 OIDC 的 auth.db，独立表 saml_mappings
MAPPING_DB_PATH = Path(__file__).parent.parent.parent / "data" / "auth.db"


def _get_mapping_db() -> sqlite3.Connection:
    MAPPING_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MAPPING_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS saml_mappings (
            provider TEXT NOT NULL,
            nameid TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            saml_email TEXT,
            saml_name TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (provider, nameid),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    conn.commit()
    return conn


def find_or_create_saml_user(
    provider: str,
    nameid: str,
    email: str | None,
    name: str | None,
    default_role: str = "viewer",
) -> dict[str, Any]:
    """根据 SAML nameid 查找本地用户，不存在则自动创建

    与 OIDC 的 find_or_create_user 对称设计，便于维护。
    """
    from app.auth.models import get_auth_store

    conn = _get_mapping_db()
    try:
        row = conn.execute(
            "SELECT * FROM saml_mappings WHERE provider = ? AND nameid = ?",
            (provider, nameid),
        ).fetchone()

        if row:
            # 更新映射信息
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE saml_mappings SET saml_email = ?, saml_name = ?, updated_at = ?
                   WHERE provider = ? AND nameid = ?""",
                (email, name, now, provider, nameid),
            )
            conn.commit()
            store = get_auth_store()
            user = store.get_user_by_id(row["user_id"])
            if user and user["active"]:
                return user
            logger.warning("saml.user_disabled", provider=provider, nameid=nameid)
            raise ValueError("用户已被禁用")

        # 首次登录，自动创建用户
        store = get_auth_store()
        # 优先用 email 作为 username，否则用 provider + nameid 派生
        username = email or f"saml_{provider}_{nameid[:32]}"
        display_name = name or (email.split("@")[0] if email else username)
        # 确保 username 唯一
        existing = store.get_user(username)
        if existing:
            username = f"saml_{provider}_{nameid[:32]}"
            existing = store.get_user(username)
            if existing:
                username = f"saml_{provider}_{secrets.token_hex(8)}"

        user = store.create_user(
            username=username,
            password=secrets.token_urlsafe(32),  # 随机密码（SAML 用户不走密码登录）
            role=default_role,
            display_name=display_name,
            email=email,
        )

        # 创建映射
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO saml_mappings
               (provider, nameid, user_id, saml_email, saml_name, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (provider, nameid, user["id"], email, name, now),
        )
        conn.commit()

        logger.info(
            "saml.user_created",
            provider=provider,
            nameid=nameid,
            username=username,
            role=default_role,
        )
        return user
    finally:
        conn.close()


def extract_user_info_from_attributes(
    nameid: str,
    attributes: dict[str, list[str] | str],
) -> tuple[str | None, str | None]:
    """从 SAML attributes 提取 email + display_name

    不同 IdP 的属性命名约定不同：
    - Okta: email = http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress
    - Azure AD: 同上 + givenname/surname
    - Keycloak: email / User.FirstName / User.LastName

    返回 (email, display_name)。若 attributes 无 email，尝试用 nameid 作为 email。
    """
    email_keys = [
        "email",
        "mail",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        "Email",
        "EmailAddress",
    ]
    name_keys = [
        "displayname",
        "display_name",
        "name",
        "cn",  # commonName
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
        "User.Name",
    ]
    givenname_keys = [
        "givenname",
        "firstname",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
        "User.FirstName",
    ]
    surname_keys = [
        "surname",
        "lastname",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname",
        "User.LastName",
    ]

    def _first(keys: list[str]) -> str | None:
        for k in keys:
            v = attributes.get(k)
            if v:
                if isinstance(v, list):
                    return v[0] if v else None
                return str(v)
        return None

    email = _first(email_keys)
    name = _first(name_keys)
    if not name:
        given = _first(givenname_keys)
        surname = _first(surname_keys)
        if given and surname:
            name = f"{given} {surname}"
        elif given:
            name = given
        elif surname:
            name = surname

    # 若 email 缺失但 nameid 看起来像 email，使用 nameid
    if not email and nameid and "@" in nameid:
        email = nameid

    return email, name
