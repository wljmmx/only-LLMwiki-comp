#!/usr/bin/env python3
"""SAML 2.0 SSO 验证脚本（S13-1 企业级 SSO 补齐）

验证内容：
1. SAMLProvider 配置 + to_settings_dict
2. parse_saml_providers（含异常容错）
3. relay_state 存储（一次性消费 / 过期清理）
4. find_or_create_saml_user（首次创建 / 二次查找 / 禁用）
5. extract_user_info_from_attributes（Okta / Azure AD / Keycloak 三种属性命名）
6. /auth/saml/status + /auth/saml/providers 端点
7. /auth/saml/{provider}/metadata SP metadata XML 生成
8. /auth/saml/{provider}/login 重定向到 IdP（HTTP-Redirect binding）
9. /auth/saml/{provider}/acs POST 端到端（mock IdP SAMLResponse）
10. SAML 关闭时 status
11. SAML 与 OIDC 共存（路由独立）

运行：python scripts/verify_s13_1_saml.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# 确保可以 import backend.app.*
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# 测试环境变量
os.environ.setdefault("ENV", "test")
os.environ.setdefault("LLM_BACKEND", "openai_compat")
os.environ.setdefault("OPENAI_COMPAT_API_KEY", "test")
os.environ.setdefault("API_TOKEN", "")

# 使用临时 DB
TMP_DIR = Path(tempfile.mkdtemp(prefix="opskg_saml_test_"))
os.environ["HOME"] = str(TMP_DIR)
DATA_DIR = TMP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 重定向 auth DB 到临时目录
import app.auth.models as auth_models

auth_models.DB_PATH = DATA_DIR / "auth.db"

import app.auth.saml as saml_module

saml_module.MAPPING_DB_PATH = DATA_DIR / "auth.db"


PASS = 0
FAIL = 0
TESTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        TESTS.append((name, True, detail))
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        TESTS.append((name, False, detail))
        print(f"  ❌ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n── {title} ──")


# ────────── 测试 1：SAMLProvider 配置 + to_settings_dict ──────────


def test_saml_provider_settings() -> None:
    section("1. SAMLProvider 配置 + to_settings_dict")
    from app.auth.saml import SAMLProvider

    p = SAMLProvider(
        name="okta",
        display_name="Okta",
        sp_entity_id="https://opskg.example.com/saml/sp",
        acs_url="https://opskg.example.com/auth/saml/okta/acs",
        slo_url="https://opskg.example.com/auth/saml/okta/sls",
        idp_entity_id="http://www.okta.com/exk123",
        idp_sso_url="https://org.okta.com/app/opskg/abc/sso",
        idp_slo_url="https://org.okta.com/app/opskg/abc/slo",
        idp_x509cert="MIID_fake_cert_for_testing",
        sp_x509cert="",
        sp_private_key="",
    )
    check("name 设置", p.name == "okta")
    check("display_name 设置", p.display_name == "Okta")
    check("acs_url 设置", p.acs_url.endswith("/auth/saml/okta/acs"))
    check("idp_x509cert 设置", p.idp_x509cert.startswith("MIID"))

    settings = p.to_settings_dict(strict=True)
    check("settings strict=True", settings["strict"] is True)
    check("settings sp.entityId 正确", settings["sp"]["entityId"] == "https://opskg.example.com/saml/sp")
    check(
        "settings sp.assertionConsumerService.url 正确",
        settings["sp"]["assertionConsumerService"]["url"] == p.acs_url,
    )
    check(
        "settings sp.assertionConsumerService.binding HTTP-POST",
        settings["sp"]["assertionConsumerService"]["binding"].endswith("HTTP-POST"),
    )
    check("settings idp.entityId 正确", settings["idp"]["entityId"] == "http://www.okta.com/exk123")
    check(
        "settings idp.singleSignOnService.url 正确",
        settings["idp"]["singleSignOnService"]["url"] == p.idp_sso_url,
    )
    check(
        "settings idp.singleSignOnService.binding HTTP-Redirect",
        settings["idp"]["singleSignOnService"]["binding"].endswith("HTTP-Redirect"),
    )
    check("settings idp.x509cert 正确", settings["idp"]["x509cert"] == "MIID_fake_cert_for_testing")
    check(
        "settings security.wantAssertionsSigned True",
        settings["security"]["wantAssertionsSigned"] is True,
    )
    check(
        "settings security.authnRequestsSigned False（无 SP 证书）",
        settings["security"]["authnRequestsSigned"] is False,
    )
    # SLO 已配置
    check(
        "settings sp.singleLogoutService 存在",
        "singleLogoutService" in settings["sp"],
    )
    check(
        "settings idp.singleLogoutService 存在",
        "singleLogoutService" in settings["idp"],
    )

    # 无 SLO 的情况
    p_no_slo = SAMLProvider(
        name="noslo",
        display_name="No SLO",
        sp_entity_id="https://opskg.example.com/saml/sp2",
        acs_url="https://opskg.example.com/auth/saml/noslo/acs",
        idp_entity_id="https://idp.example.com",
        idp_sso_url="https://idp.example.com/sso",
        idp_x509cert="cert",
    )
    settings2 = p_no_slo.to_settings_dict()
    check(
        "无 SLO 时 sp.singleLogoutService 不存在",
        "singleLogoutService" not in settings2["sp"],
    )

    # 带 SP 证书的情况（authnRequestsSigned=True）
    p_signed = SAMLProvider(
        name="signed",
        display_name="Signed SP",
        sp_entity_id="https://opskg.example.com/saml/sp3",
        acs_url="https://opskg.example.com/auth/saml/signed/acs",
        idp_entity_id="https://idp.example.com",
        idp_sso_url="https://idp.example.com/sso",
        idp_x509cert="idp-cert",
        sp_x509cert="sp-cert",
        sp_private_key="sp-key",
    )
    settings3 = p_signed.to_settings_dict()
    check(
        "settings 带 SP 证书时 authnRequestsSigned=True",
        settings3["security"]["authnRequestsSigned"] is True,
    )
    check(
        "settings 带 SP 证书时 logoutRequestSigned=True",
        settings3["security"]["logoutRequestSigned"] is True,
    )

    # to_dict 不含敏感字段
    d = p.to_dict()
    check("to_dict 含 name", d["name"] == "okta")
    check("to_dict 含 display_name", d["display_name"] == "Okta")
    check("to_dict 含 sp_entity_id", "sp_entity_id" in d)
    check("to_dict 不含 idp_x509cert", "idp_x509cert" not in d)
    check("to_dict 不含 sp_private_key", "sp_private_key" not in d)
    check("to_dict has_slo=True", d["has_slo"] is True)


# ────────── 测试 2：parse_saml_providers ──────────


def test_parse_providers() -> None:
    section("2. parse_saml_providers 配置解析")
    from app.auth.saml import parse_saml_providers

    # 空字符串
    providers = parse_saml_providers("")
    check("空字符串返回空列表", providers == [])

    # 有效 JSON
    config = json.dumps(
        [
            {
                "name": "okta",
                "display_name": "Okta",
                "sp_entity_id": "https://opskg.example.com/saml/sp",
                "acs_url": "https://opskg.example.com/auth/saml/okta/acs",
                "idp_entity_id": "http://www.okta.com/exk123",
                "idp_sso_url": "https://org.okta.com/app/abc/sso",
                "idp_x509cert": "MIID_cert1",
            },
            {
                "name": "azuread",
                "display_name": "Azure AD",
                "sp_entity_id": "https://opskg.example.com/saml/sp",
                "acs_url": "https://opskg.example.com/auth/saml/azuread/acs",
                "idp_entity_id": "https://login.microsoftonline.com/tenant/v2.0",
                "idp_sso_url": "https://login.microsoftonline.com/tenant/saml2",
                "idp_x509cert": "MIID_cert2",
            },
        ]
    )
    providers = parse_saml_providers(config)
    check("解析 2 个提供者", len(providers) == 2, f"got {len(providers)}")
    check("第一个 name=okta", providers[0].name == "okta")
    check("第二个 name=azuread", providers[1].name == "azuread")

    # 无效 JSON
    providers = parse_saml_providers("not-json")
    check("无效 JSON 返回空列表", providers == [])

    # 非数组
    providers = parse_saml_providers('{"name": "not-array"}')
    check("非数组返回空列表", providers == [])

    # 缺字段
    providers = parse_saml_providers(json.dumps([{"name": "incomplete"}]))
    check("缺字段提供者被跳过", providers == [])

    # 多余字段被 **_extra 吸收
    providers = parse_saml_providers(
        json.dumps(
            [
                {
                    "name": "extra",
                    "display_name": "Extra",
                    "sp_entity_id": "https://example.com/sp",
                    "acs_url": "https://example.com/acs",
                    "idp_entity_id": "https://idp.example.com",
                    "idp_sso_url": "https://idp.example.com/sso",
                    "idp_x509cert": "cert",
                    "extra_field": "ignored",
                    "another_extra": 123,
                }
            ]
        )
    )
    check("多余字段被吸收不报错", len(providers) == 1)


# ────────── 测试 3：relay_state 存储 ──────────


def test_relay_state_store() -> None:
    section("3. relay_state 存储")
    from app.auth.saml import _relay_store, pop_relay_state, save_relay_state

    _relay_store.clear()

    state = save_relay_state("okta", "/dashboard")
    check("save_relay_state 返回非空 state", len(state) > 0)
    check("state 已存入内存", state in _relay_store)

    # 第一次 pop 应返回数据
    data = pop_relay_state(state)
    check("pop_relay_state 返回数据", data is not None)
    check("state 含 provider", data["provider"] == "okta")
    check("state 含 redirect", data["redirect"] == "/dashboard")

    # 第二次 pop 应返回 None（一次性）
    data2 = pop_relay_state(state)
    check("relay_state 一次性消费", data2 is None)
    check("relay_state 消费后从内存删除", state not in _relay_store)


# ────────── 测试 4：find_or_create_saml_user ──────────


def test_find_or_create_saml_user() -> None:
    section("4. SAML 用户映射 find_or_create_saml_user")
    from app.auth.models import get_auth_store
    from app.auth.saml import _get_mapping_db, find_or_create_saml_user

    # 初始化 schema
    store = get_auth_store()
    store.ensure_bootstrap_admin()

    # 清理可能存在的数据
    conn = _get_mapping_db()
    conn.execute("DELETE FROM saml_mappings")
    conn.execute(
        "DELETE FROM users WHERE username LIKE 'saml_%' OR username LIKE '%_example.com'"
    )
    conn.commit()
    conn.close()

    # 首次创建
    user1 = find_or_create_saml_user(
        provider="okta",
        nameid="alice@example.com",
        email="alice@example.com",
        name="Alice Wang",
        default_role="viewer",
    )
    check("首次创建返回用户", user1 is not None)
    check("用户 username 为 email", user1["username"] == "alice@example.com")
    check("用户 email 正确", user1["email"] == "alice@example.com")
    check("用户 display_name 为 Alice Wang", user1["display_name"] == "Alice Wang")
    check("用户 role 为 viewer", user1["role"] == "viewer")
    check("用户 active", user1["active"] is True)

    user_id = user1["id"]

    # 二次查找（同一 nameid）
    user2 = find_or_create_saml_user(
        provider="okta",
        nameid="alice@example.com",
        email="alice@example.com",
        name="Alice Updated",
        default_role="viewer",
    )
    check("二次查找返回同一用户", user2["id"] == user_id)

    # 不同 provider 同 nameid 视为不同用户
    user3 = find_or_create_saml_user(
        provider="azuread",
        nameid="alice@example.com",
        email="alice@example.com",
        name="Alice",
    )
    check("不同 provider 视为新用户", user3["id"] != user_id)

    # 用户被禁用后 SAML 登录失败
    store.update_user(user_id, active=False)
    try:
        find_or_create_saml_user(
            provider="okta",
            nameid="alice@example.com",
            email="alice@example.com",
            name="Alice",
        )
        check("禁用用户 SAML 登录失败", False, "未抛异常")
    except ValueError as e:
        check("禁用用户 SAML 登录失败", True, str(e))

    # 恢复
    store.update_user(user_id, active=True)


# ────────── 测试 5：extract_user_info_from_attributes ──────────


def test_extract_user_info() -> None:
    section("5. extract_user_info_from_attributes（多 IdP 兼容）")
    from app.auth.saml import extract_user_info_from_attributes

    # Okta 风格
    email, name = extract_user_info_from_attributes(
        nameid="alice@example.com",
        attributes={
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": ["alice@example.com"],
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name": ["Alice"],
        },
    )
    check("Okta email 解析正确", email == "alice@example.com")
    check("Okta name 解析正确", name == "Alice")

    # Azure AD 风格（givenname + surname）
    email, name = extract_user_info_from_attributes(
        nameid="bob@example.com",
        attributes={
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": ["bob@example.com"],
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname": ["Bob"],
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname": ["Smith"],
        },
    )
    check("Azure AD email 解析正确", email == "bob@example.com")
    check("Azure AD name = givenname + surname", name == "Bob Smith")

    # Keycloak 风格
    email, name = extract_user_info_from_attributes(
        nameid="charlie@example.com",
        attributes={
            "email": ["charlie@example.com"],
            "User.FirstName": ["Charlie"],
            "User.LastName": ["Brown"],
        },
    )
    check("Keycloak email 解析正确", email == "charlie@example.com")
    check("Keycloak name = FirstName + LastName", name == "Charlie Brown")

    # 仅有 nameid（email 格式），无 attributes
    email, name = extract_user_info_from_attributes(
        nameid="dave@example.com",
        attributes={},
    )
    check("无 attributes 时 nameid 作为 email", email == "dave@example.com")
    check("无 attributes 时 name 为 None", name is None)

    # nameid 不是 email 格式且无 email 属性
    email, name = extract_user_info_from_attributes(
        nameid="user123",
        attributes={"displayname": ["Display Name"]},
    )
    check("nameid 非 email 且无 email 属性时 email=None", email is None)
    check("displayname 被识别为 name", name == "Display Name")

    # 单值字符串（非列表）
    email, name = extract_user_info_from_attributes(
        nameid="x@y.com",
        attributes={"email": "x@y.com", "name": "X User"},
    )
    check("单值字符串 email 解析正确", email == "x@y.com")
    check("单值字符串 name 解析正确", name == "X User")


# ────────── 测试 6：/auth/saml/status + providers 端点 ──────────


def test_saml_status_endpoints() -> None:
    section("6. /auth/saml/status + /auth/saml/providers 端点")
    from fastapi.testclient import TestClient

    # 配置一个测试 provider
    os.environ["SAML_PROVIDERS"] = json.dumps(
        [
            {
                "name": "test-idp",
                "display_name": "Test IdP",
                "sp_entity_id": "https://opskg.example.com/saml/sp",
                "acs_url": "https://opskg.example.com/auth/saml/test-idp/acs",
                "idp_entity_id": "https://idp.example.com",
                "idp_sso_url": "https://idp.example.com/sso",
                "idp_x509cert": "MIID_test_cert",
            }
        ]
    )
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)

    # /auth/saml/status
    resp = client.get("/auth/saml/status")
    check("status 返回 200", resp.status_code == 200)
    data = resp.json()
    check("status enabled=True", data["enabled"] is True)
    check("status providers 含 1 个", data["providers"] and len(data["providers"]) == 1)
    check("status provider name 正确", data["providers"][0]["name"] == "test-idp")

    # /auth/saml/providers
    resp = client.get("/auth/saml/providers")
    check("providers 返回 200", resp.status_code == 200)
    data = resp.json()
    check("providers count=1", data["count"] == 1)
    check(
        "providers 不含证书/私钥",
        "idp_x509cert" not in str(data) and "sp_private_key" not in str(data),
    )

    # 清除配置
    os.environ["SAML_PROVIDERS"] = ""
    get_settings.cache_clear()


# ────────── 测试 7：SP metadata XML ──────────


def test_saml_metadata_endpoint() -> None:
    section("7. /auth/saml/{provider}/metadata SP metadata XML")
    from fastapi.testclient import TestClient

    os.environ["SAML_PROVIDERS"] = json.dumps(
        [
            {
                "name": "okta",
                "display_name": "Okta",
                "sp_entity_id": "https://opskg.example.com/saml/sp",
                "acs_url": "https://opskg.example.com/auth/saml/okta/acs",
                "idp_entity_id": "http://www.okta.com/exk123",
                "idp_sso_url": "https://org.okta.com/app/abc/sso",
                "idp_x509cert": "MIID_test_cert",
            }
        ]
    )
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)

    # 未配置的 provider
    resp = client.get("/auth/saml/nonexistent/metadata")
    check("未配置 provider metadata 返回 404", resp.status_code == 404)

    # 已配置 provider 应返回 XML
    resp = client.get("/auth/saml/okta/metadata")
    check("已配置 provider metadata 返回 200", resp.status_code == 200)
    content = resp.text
    check(
        "Content-Type 为 XML",
        "xml" in resp.headers.get("content-type", "").lower(),
    )
    check("metadata 含 EntityDescriptor", "EntityDescriptor" in content)
    check("metadata 含 SPSSODescriptor", "SPSSODescriptor" in content)
    check(
        "metadata 含 entityID",
        'entityID="https://opskg.example.com/saml/sp"' in content,
    )
    check(
        "metadata 含 AssertionConsumerService",
        "AssertionConsumerService" in content,
    )
    check(
        "metadata 含 acs_url",
        "https://opskg.example.com/auth/saml/okta/acs" in content,
    )

    # 清除配置
    os.environ["SAML_PROVIDERS"] = ""
    get_settings.cache_clear()


# ────────── 测试 8：/auth/saml/{provider}/login 重定向 ──────────


def test_saml_login_redirect() -> None:
    section("8. /auth/saml/{provider}/login 重定向到 IdP")
    from fastapi.testclient import TestClient

    os.environ["SAML_PROVIDERS"] = json.dumps(
        [
            {
                "name": "okta",
                "display_name": "Okta",
                "sp_entity_id": "https://opskg.example.com/saml/sp",
                "acs_url": "https://opskg.example.com/auth/saml/okta/acs",
                "idp_entity_id": "http://www.okta.com/exk123",
                "idp_sso_url": "https://org.okta.com/app/abc/sso",
                "idp_x509cert": "MIID_test_cert",
            }
        ]
    )
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)

    # 未配置的 provider
    resp = client.get("/auth/saml/nonexistent/login", follow_redirects=False)
    check("未配置 provider login 返回 404", resp.status_code == 404)

    # 已配置 provider 应重定向到 IdP SSO URL
    resp = client.get(
        "/auth/saml/okta/login?redirect=/dashboard",
        follow_redirects=False,
    )
    check("已配置 provider login 返回 302", resp.status_code == 302)
    location = resp.headers.get("location", "")
    check("重定向到 idp SSO URL", location.startswith("https://org.okta.com/app/abc/sso"))
    check("含 SAMLRequest 参数", "SAMLRequest=" in location)
    check("含 RelayState 参数", "RelayState=" in location)

    # 清除配置
    os.environ["SAML_PROVIDERS"] = ""
    get_settings.cache_clear()


# ────────── 测试 9：ACS POST 端到端（mock IdP） ──────────


def test_saml_acs_post_e2e() -> None:
    section("9. /auth/saml/{provider}/acs POST 端到端（mock process_response）")
    from fastapi.testclient import TestClient

    os.environ["SAML_PROVIDERS"] = json.dumps(
        [
            {
                "name": "okta",
                "display_name": "Okta",
                "sp_entity_id": "https://opskg.example.com/saml/sp",
                "acs_url": "https://opskg.example.com/auth/saml/okta/acs",
                "idp_entity_id": "http://www.okta.com/exk123",
                "idp_sso_url": "https://org.okta.com/app/abc/sso",
                "idp_x509cert": "MIID_test_cert",
            }
        ]
    )
    os.environ["FRONTEND_BASE_URL"] = "http://localhost:5173"
    # 关闭严格模式以方便测试
    os.environ["SAML_STRICT"] = "false"
    from app.config import get_settings

    get_settings.cache_clear()

    # mock OneLogin_Saml2_Auth 类
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    original_init = OneLogin_Saml2_Auth.__init__

    class MockSamlAuth:
        def __init__(self, req, settings):
            self._authenticated = True
            self._errors: list[str] = []
            self._nameid = "samluser@example.com"
            self._attributes = {
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": [
                    "samluser@example.com"
                ],
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name": [
                    "SAML User"
                ],
            }

        def process_response(self):
            pass

        def get_errors(self):
            return self._errors

        def get_last_error_reason(self):
            return None

        def is_authenticated(self):
            return self._authenticated

        def get_nameid(self):
            return self._nameid

        def get_attributes(self):
            return self._attributes

        def login(self, return_to=None):
            return f"https://idp.example.com/sso?RelayState={return_to}"

    # 替换模块内的 OneLogin_Saml2_Auth 引用
    import onelogin.saml2.auth as saml_auth_mod

    original_auth_class = saml_auth_mod.OneLogin_Saml2_Auth
    saml_auth_mod.OneLogin_Saml2_Auth = MockSamlAuth

    try:
        from app.main import app

        client = TestClient(app)

        # 准备 relay_state
        from app.auth.saml import save_relay_state

        relay_state = save_relay_state("okta", "/wiki")

        # POST SAMLResponse
        resp = client.post(
            "/auth/saml/okta/acs",
            data={"SAMLResponse": "fake-saml-response-base64", "RelayState": relay_state},
            follow_redirects=False,
        )
        check("ACS POST 返回 302", resp.status_code == 302)
        location = resp.headers.get("location", "")
        check("重定向到前端", "localhost:5173/login/callback" in location)
        check("含 token", "token=" in location)
        check("含 redirect=/wiki", "redirect=" in location)

        # 验证用户已被创建
        from app.auth.models import get_auth_store

        store = get_auth_store()
        user = store.get_user("samluser@example.com")
        check("SAML 用户已创建", user is not None)
        check("用户 email 正确", user and user["email"] == "samluser@example.com")

        # relay_state 已被消费（一次性）
        from app.auth.saml import pop_relay_state

        check("ACS 后 relay_state 已消费", pop_relay_state(relay_state) is None)

        # 测试 unknown provider
        resp = client.post(
            "/auth/saml/nonexistent/acs",
            data={"SAMLResponse": "fake", "RelayState": ""},
            follow_redirects=False,
        )
        check("ACS unknown provider 返回 302（错误重定向）", resp.status_code == 302)
        check("错误重定向含 error", "error=" in resp.headers.get("location", ""))

        # 测试 process_response 抛异常
        original_process = MockSamlAuth.process_response

        def exploding_process(self):
            raise RuntimeError("mock process error")

        MockSamlAuth.process_response = exploding_process
        resp = client.post(
            "/auth/saml/okta/acs",
            data={"SAMLResponse": "fake", "RelayState": ""},
            follow_redirects=False,
        )
        check("process_response 异常返回 302", resp.status_code == 302)
        check(
            "process_response 异常含 process_failed",
            "process_failed" in resp.headers.get("location", ""),
        )
        MockSamlAuth.process_response = original_process

        # 测试认证失败
        original_is_auth = MockSamlAuth.is_authenticated

        def not_auth(self):
            return False

        MockSamlAuth.is_authenticated = not_auth
        resp = client.post(
            "/auth/saml/okta/acs",
            data={"SAMLResponse": "fake", "RelayState": ""},
            follow_redirects=False,
        )
        check("认证失败返回 302", resp.status_code == 302)
        check(
            "认证失败含 not_authenticated",
            "not_authenticated" in resp.headers.get("location", ""),
        )
        MockSamlAuth.is_authenticated = original_is_auth

        # 测试 errors
        original_get_errors = MockSamlAuth.get_errors

        def with_errors(self):
            return ["invalid_response"]

        MockSamlAuth.get_errors = with_errors
        resp = client.post(
            "/auth/saml/okta/acs",
            data={"SAMLResponse": "fake", "RelayState": ""},
            follow_redirects=False,
        )
        check("errors 非空返回 302", resp.status_code == 302)
        check(
            "errors 含 saml_error",
            "saml_error" in resp.headers.get("location", ""),
        )
        MockSamlAuth.get_errors = original_get_errors
    finally:
        saml_auth_mod.OneLogin_Saml2_Auth = original_auth_class
        OneLogin_Saml2_Auth.__init__ = original_init
        os.environ["SAML_PROVIDERS"] = ""
        os.environ.pop("SAML_STRICT", None)
        get_settings.cache_clear()


# ────────── 测试 10：ACS GET 变体 ──────────


def test_saml_acs_get_variant() -> None:
    section("10. /auth/saml/{provider}/acs GET 变体")
    from fastapi.testclient import TestClient

    os.environ["SAML_PROVIDERS"] = json.dumps(
        [
            {
                "name": "okta",
                "display_name": "Okta",
                "sp_entity_id": "https://opskg.example.com/saml/sp",
                "acs_url": "https://opskg.example.com/auth/saml/okta/acs",
                "idp_entity_id": "http://www.okta.com/exk123",
                "idp_sso_url": "https://org.okta.com/app/abc/sso",
                "idp_x509cert": "MIID_test_cert",
            }
        ]
    )
    os.environ["FRONTEND_BASE_URL"] = "http://localhost:5173"
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)

    # GET 缺 SAMLResponse
    resp = client.get("/auth/saml/okta/acs", follow_redirects=False)
    check("GET ACS 缺 SAMLResponse 返回 302", resp.status_code == 302)
    check(
        "GET ACS 缺 SAMLResponse 含 missing_saml_response",
        "missing_saml_response" in resp.headers.get("location", ""),
    )

    # 清除配置
    os.environ["SAML_PROVIDERS"] = ""
    get_settings.cache_clear()


# ────────── 测试 11：SAML 关闭时 status ──────────


def test_saml_disabled() -> None:
    section("11. SAML 未配置时关闭")
    from fastapi.testclient import TestClient

    os.environ["SAML_PROVIDERS"] = ""
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)

    resp = client.get("/auth/saml/status")
    check("未配置 status 返回 200", resp.status_code == 200)
    data = resp.json()
    check("未配置 enabled=False", data["enabled"] is False)
    check("未配置 providers 为空", data["providers"] == [])

    resp = client.get("/auth/saml/providers")
    check("未配置 providers 返回 200", resp.status_code == 200)
    data = resp.json()
    check("未配置 count=0", data["count"] == 0)


# ────────── 测试 12：SAML 与 OIDC 共存 ──────────


def test_saml_oidc_coexist() -> None:
    section("12. SAML 与 OIDC 路由独立共存")
    from fastapi.testclient import TestClient

    # 同时配置 OIDC 和 SAML
    os.environ["OIDC_PROVIDERS"] = json.dumps(
        [
            {
                "name": "google",
                "display_name": "Google",
                "client_id": "g-id",
                "client_secret": "g-sec",
                "discovery_url": "http://localhost:9999/.well-known/openid-configuration",
            }
        ]
    )
    os.environ["SAML_PROVIDERS"] = json.dumps(
        [
            {
                "name": "okta",
                "display_name": "Okta",
                "sp_entity_id": "https://opskg.example.com/saml/sp",
                "acs_url": "https://opskg.example.com/auth/saml/okta/acs",
                "idp_entity_id": "http://www.okta.com/exk123",
                "idp_sso_url": "https://org.okta.com/app/abc/sso",
                "idp_x509cert": "MIID_test_cert",
            }
        ]
    )
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)

    # OIDC status
    resp = client.get("/auth/oidc/status")
    check("OIDC status 返回 200", resp.status_code == 200)
    oidc_data = resp.json()
    check("OIDC enabled=True", oidc_data["enabled"] is True)
    check("OIDC providers 含 1 个", len(oidc_data["providers"]) == 1)

    # SAML status
    resp = client.get("/auth/saml/status")
    check("SAML status 返回 200", resp.status_code == 200)
    saml_data = resp.json()
    check("SAML enabled=True", saml_data["enabled"] is True)
    check("SAML providers 含 1 个", len(saml_data["providers"]) == 1)

    # 互不干扰：OIDC 提供者不出现 在 SAML 列表里
    saml_names = [p["name"] for p in saml_data["providers"]]
    oidc_names = [p["name"] for p in oidc_data["providers"]]
    check("SAML providers 含 okta", "okta" in saml_names)
    check("OIDC providers 含 google", "google" in oidc_names)
    check("SAML providers 不含 google", "google" not in saml_names)
    check("OIDC providers 不含 okta", "okta" not in oidc_names)

    # 清除配置
    os.environ["OIDC_PROVIDERS"] = ""
    os.environ["SAML_PROVIDERS"] = ""
    get_settings.cache_clear()


def main() -> int:
    print("=" * 60)
    print("SAML 2.0 SSO 验证脚本（S13-1 企业级 SSO 补齐）")
    print("=" * 60)

    test_saml_provider_settings()
    test_parse_providers()
    test_relay_state_store()
    test_find_or_create_saml_user()
    test_extract_user_info()
    test_saml_status_endpoints()
    test_saml_metadata_endpoint()
    test_saml_login_redirect()
    test_saml_acs_post_e2e()
    test_saml_acs_get_variant()
    test_saml_disabled()
    test_saml_oidc_coexist()

    print("\n" + "=" * 60)
    print(f"总计：{PASS} 通过 / {FAIL} 失败")
    print("=" * 60)

    if FAIL > 0:
        print("\n失败项：")
        for name, ok, detail in TESTS:
            if not ok:
                print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
