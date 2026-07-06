"""LDAP / Active Directory 认证（S13-2 企业级 SSO 补齐）

基于 ldap3，支持 OpenLDAP / Microsoft Active Directory / FreeIPA 等 LDAP v3 服务器。
首次 LDAP 登录自动创建本地用户（角色由 ldap_default_role 配置）。

设计要点：
1. **bind 验证**：先以 service account bind 搜索用户 DN，再以用户 DN + 密码二次 bind 验证
   —— 这是 LDAP 认证的标准模式，避免直接用用户 DN 拼接（易受注入攻击）
2. **属性映射**：从 LDAP entry 提取 email / display_name，兼容 AD（userPrincipalName）
   与 OpenLDAP（mail/cn）的不同命名约定
3. **用户映射**：与 OIDC/SAML 对称设计，独立表 ldap_mappings（provider + dn → user_id）

配置格式（LDAP_PROVIDERS 环境变量，JSON 数组）：
[
  {
    "name": "corp-ad",
    "display_name": "Corporate AD",
    "server_url": "ldap://dc01.corp.example.com:389",
    "bind_dn": "CN=svc-opskg,OU=ServiceAccounts,DC=corp,DC=example,DC=com",
    "bind_password": "svc-password",
    "user_search_base": "OU=Users,DC=corp,DC=example,DC=com",
    "user_search_filter": "(sAMAccountName={username})",
    "use_tls": false,
    "use_ssl": false
  }
]

留空则关闭 LDAP 认证。
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# ────────── LDAP 提供者配置 ──────────


class LDAPProvider:
    """单个 LDAP 服务器配置

    支持两种 bind 模式：
    - service-bind + user-search + user-bind（推荐，AD/OpenLDAP 通用）
    - 直接 user-bind（仅当 user_dn_template 已配置时启用）
    """

    def __init__(
        self,
        name: str,
        display_name: str,
        server_url: str,
        user_search_base: str,
        user_search_filter: str = "(sAMAccountName={username})",
        bind_dn: str = "",
        bind_password: str = "",
        user_dn_template: str = "",
        use_tls: bool = False,
        use_ssl: bool = False,
        **_extra: Any,
    ):
        self.name = name
        self.display_name = display_name
        self.server_url = server_url
        self.user_search_base = user_search_base
        self.user_search_filter = user_search_filter
        self.bind_dn = bind_dn
        self.bind_password = bind_password
        self.user_dn_template = user_dn_template
        self.use_tls = use_tls
        self.use_ssl = use_ssl

    @property
    def uses_service_bind(self) -> bool:
        """是否使用 service-bind + user-search 模式"""
        return bool(self.bind_dn and self.bind_password)

    @property
    def uses_direct_bind(self) -> bool:
        """是否使用直接 user-bind 模式（user_dn_template）"""
        return bool(self.user_dn_template) and "{username}" in self.user_dn_template

    def to_dict(self) -> dict[str, Any]:
        """用于 /auth/ldap/providers 返回（不含密码）"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "server_url": self.server_url,
            "user_search_base": self.user_search_base,
            "user_search_filter": self.user_search_filter,
            "uses_service_bind": self.uses_service_bind,
            "uses_direct_bind": self.uses_direct_bind,
            "use_tls": self.use_tls,
            "use_ssl": self.use_ssl,
        }


def parse_ldap_providers(json_str: str) -> list[LDAPProvider]:
    """解析环境变量中的 LDAP 提供者 JSON 配置"""
    if not json_str or not json_str.strip():
        return []
    try:
        items = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning("ldap.parse_failed", error=str(e))
        return []
    if not isinstance(items, list):
        logger.warning("ldap.config_not_list")
        return []
    providers = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            providers.append(LDAPProvider(**item))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "ldap.provider_skip",
                name=item.get("name"),
                error=str(e),
            )
    return providers


# ────────── LDAP 认证核心 ──────────


class LDAPAuthResult:
    """LDAP 认证结果"""

    def __init__(
        self,
        success: bool,
        user_dn: str | None = None,
        attributes: dict[str, list[str]] | None = None,
        error: str | None = None,
    ):
        self.success = success
        self.user_dn = user_dn
        self.attributes = attributes or {}
        self.error = error


def authenticate(
    provider: LDAPProvider,
    username: str,
    password: str,
) -> LDAPAuthResult:
    """LDAP 用户认证

    流程：
    1. 若配置了 service-bind：
       a. 用 bind_dn/bind_password bind 到 LDAP
       b. 在 user_search_base 下用 user_search_filter 搜索用户 DN
       c. 用找到的用户 DN + 提供的 password 二次 bind 验证
    2. 若配置了 user_dn_template：
       a. 用 template 拼出用户 DN
       b. 用该 DN + password 直接 bind

    Args:
        provider: LDAP 提供者配置
        username: 用户名（不含域名部分）
        password: 明文密码

    Returns:
        LDAPAuthResult
    """
    if not username or not password:
        return LDAPAuthResult(success=False, error="用户名和密码不能为空")

    # 延迟导入：仅在调用时需要 ldap3
    from ldap3 import Connection, Server
    from ldap3.core.exceptions import LDAPException

    server = Server(
        provider.server_url,
        use_ssl=provider.use_ssl,
        tls=None,  # use_tls 单独处理（STARTTLS）
    )

    user_dn: str | None = None
    user_attributes: dict[str, list[str]] = {}

    try:
        if provider.uses_service_bind:
            # 模式 A：service-bind + user-search + user-bind
            with Connection(
                server,
                user=provider.bind_dn,
                password=provider.bind_password,
                auto_bind=True,
            ) as conn:
                if not conn.bound:
                    return LDAPAuthResult(
                        success=False, error="service account bind 失败"
                    )

                # 搜索用户
                search_filter = provider.user_search_filter.format(username=_escape_ldap_filter(username))
                # 请求常见属性
                search_attrs = [
                    "mail",
                    "displayName",
                    "cn",
                    "givenName",
                    "sn",
                    "userPrincipalName",
                    "sAMAccountName",
                    "uid",
                ]
                if not conn.search(
                    search_base=provider.user_search_base,
                    search_filter=search_filter,
                    attributes=search_attrs,
                    size_limit=1,
                ):
                    return LDAPAuthResult(
                        success=False, error=f"未找到用户: {username}"
                    )

                if len(conn.entries) == 0:
                    return LDAPAuthResult(
                        success=False, error=f"未找到用户: {username}"
                    )

                entry = conn.entries[0]
                user_dn = entry.entry_dn

                # 提取属性（ldap3 返回的是 Attribute 对象，需转换）
                for attr_name in search_attrs:
                    if attr_name in entry:
                        values = entry[attr_name].values
                        if values:
                            user_attributes[attr_name] = values

            # 二次 bind：用 user_dn + password 验证
            with Connection(
                server,
                user=user_dn,
                password=password,
                auto_bind=True,
            ) as user_conn:
                if not user_conn.bound:
                    return LDAPAuthResult(
                        success=False, error="用户名或密码错误"
                    )

            return LDAPAuthResult(
                success=True, user_dn=user_dn, attributes=user_attributes
            )

        elif provider.uses_direct_bind:
            # 模式 B：直接 user-bind
            user_dn = provider.user_dn_template.format(
                username=_escape_ldap_dn(username)
            )
            with Connection(
                server,
                user=user_dn,
                password=password,
                auto_bind=True,
            ) as user_conn:
                if not user_conn.bound:
                    return LDAPAuthResult(
                        success=False, error="用户名或密码错误"
                    )

            return LDAPAuthResult(success=True, user_dn=user_dn, attributes={})

        else:
            return LDAPAuthResult(
                success=False,
                error="LDAP 提供者未配置 bind 方式（既无 bind_dn 也无 user_dn_template）",
            )

    except LDAPException as e:
        logger.warning("ldap.auth_exception", provider=provider.name, error=str(e))
        return LDAPAuthResult(success=False, error=f"LDAP 认证异常: {e}")
    except Exception as e:  # noqa: BLE001
        logger.warning("ldap.auth_unknown_exception", provider=provider.name, error=str(e))
        return LDAPAuthResult(success=False, error=f"认证异常: {e}")


def _escape_ldap_filter(value: str) -> str:
    r"""转义 LDAP 搜索过滤器中的特殊字符（防注入）

    RFC 4515 规定需转义的字符：* ( ) \ NUL
    """
    if not value:
        return ""
    replacements = {
        "\\": "\\5c",
        "*": "\\2a",
        "(": "\\28",
        ")": "\\29",
        "\x00": "\\00",
    }
    result = []
    for char in value:
        result.append(replacements.get(char, char))
    return "".join(result)


def _escape_ldap_dn(value: str) -> str:
    r"""转义 LDAP DN 中的特殊字符（防注入）

    RFC 4514 规定需转义的字符：, + " \ < > ;
    以及 # 在 DN 开头、空格在 DN 开头/结尾
    """
    if not value:
        return ""
    replacements = {
        "\\": "\\5c",
        ",": "\\2c",
        "+": "\\2b",
        '"': "\\22",
        "<": "\\3c",
        ">": "\\3e",
        ";": "\\3b",
        "#": "\\23",
    }
    result = []
    for i, char in enumerate(value):
        # 空格在开头/结尾需转义
        if char == " " and (i == 0 or i == len(value) - 1):
            result.append("\\20")
        else:
            result.append(replacements.get(char, char))
    return "".join(result)


# ────────── LDAP 用户映射 ──────────

# LDAP 用户映射存储（provider + user_dn → local user_id）
# 复用 OIDC/SAML 的 auth.db，独立表 ldap_mappings
MAPPING_DB_PATH = Path(__file__).parent.parent.parent / "data" / "auth.db"


def _get_mapping_db() -> sqlite3.Connection:
    MAPPING_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MAPPING_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ldap_mappings (
            provider TEXT NOT NULL,
            user_dn TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            ldap_username TEXT,
            ldap_email TEXT,
            ldap_name TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (provider, user_dn),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    conn.commit()
    return conn


def find_or_create_ldap_user(
    provider: str,
    user_dn: str,
    username: str,
    email: str | None,
    name: str | None,
    default_role: str = "viewer",
) -> dict[str, Any]:
    """根据 LDAP user_dn 查找本地用户，不存在则自动创建

    与 OIDC/SAML 的 find_or_create_user 对称设计。
    """
    from app.auth.models import get_auth_store

    conn = _get_mapping_db()
    try:
        row = conn.execute(
            "SELECT * FROM ldap_mappings WHERE provider = ? AND user_dn = ?",
            (provider, user_dn),
        ).fetchone()

        if row:
            # 更新映射信息
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE ldap_mappings
                   SET ldap_username = ?, ldap_email = ?, ldap_name = ?, updated_at = ?
                   WHERE provider = ? AND user_dn = ?""",
                (username, email, name, now, provider, user_dn),
            )
            conn.commit()
            store = get_auth_store()
            user = store.get_user_by_id(row["user_id"])
            if user and user["active"]:
                return user
            logger.warning("ldap.user_disabled", provider=provider, user_dn=user_dn)
            raise ValueError("用户已被禁用")

        # 首次登录，自动创建用户
        store = get_auth_store()
        # 优先用 email 作为 username，否则用 LDAP 提供的 username
        local_username = email or username or f"ldap_{provider}_{secrets.token_hex(4)}"
        display_name = name or local_username
        # 确保 username 唯一
        existing = store.get_user(local_username)
        if existing:
            local_username = f"ldap_{provider}_{username}"
            existing = store.get_user(local_username)
            if existing:
                local_username = f"ldap_{provider}_{secrets.token_hex(8)}"

        user = store.create_user(
            username=local_username,
            password=secrets.token_urlsafe(32),  # 随机密码（LDAP 用户不走本地密码登录）
            role=default_role,
            display_name=display_name,
            email=email,
        )

        # 创建映射
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO ldap_mappings
               (provider, user_dn, user_id, ldap_username, ldap_email, ldap_name, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (provider, user_dn, user["id"], username, email, name, now),
        )
        conn.commit()

        logger.info(
            "ldap.user_created",
            provider=provider,
            user_dn=user_dn,
            username=local_username,
            role=default_role,
        )
        return user
    finally:
        conn.close()


def extract_user_info_from_attributes(
    username: str,
    attributes: dict[str, list[str]],
) -> tuple[str | None, str | None]:
    """从 LDAP attributes 提取 email + display_name

    兼容 AD（userPrincipalName / displayName / givenName / sn）
    与 OpenLDAP（mail / cn / givenName / sn）的不同命名约定。

    返回 (email, display_name)。
    """
    email_keys = ["mail", "userPrincipalName", "email"]
    name_keys = ["displayName", "cn", "name"]
    givenname_keys = ["givenName", "firstName"]
    surname_keys = ["sn", "lastName", "surname"]

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
        elif username:
            name = username

    # 若 email 缺失但 userPrincipalName 看起来像 email
    if not email and name and "@" in name:
        email = name

    return email, name
