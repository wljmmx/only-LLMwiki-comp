#!/usr/bin/env python3
"""LDAP 认证验证脚本（S13-2 企业级 SSO 补齐）

验证内容：
1. LDAPProvider 配置 + to_dict
2. parse_ldap_providers（含异常容错）
3. _escape_ldap_filter（防注入）
4. _escape_ldap_dn（防注入）
5. authenticate（mock ldap3，覆盖 service-bind + direct-bind + 异常路径）
6. find_or_create_ldap_user（首次创建 / 二次查找 / 禁用）
7. extract_user_info_from_attributes（AD / OpenLDAP 兼容）
8. /auth/ldap/status + /auth/ldap/providers 端点
9. /auth/ldap/{provider}/login 端到端（mock ldap3）
10. LDAP 关闭时 status
11. LDAP/SAML/OIDC 三者共存

运行：python scripts/verify_s13_2_ldap.py
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
TMP_DIR = Path(tempfile.mkdtemp(prefix="opskg_ldap_test_"))
os.environ["HOME"] = str(TMP_DIR)
DATA_DIR = TMP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 重定向 auth DB 到临时目录
import app.auth.models as auth_models

auth_models.DB_PATH = DATA_DIR / "auth.db"

import app.auth.ldap as ldap_module

ldap_module.MAPPING_DB_PATH = DATA_DIR / "auth.db"


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


# ────────── 测试 1：LDAPProvider 配置 ──────────


def test_ldap_provider_config() -> None:
    section("1. LDAPProvider 配置 + to_dict")
    from app.auth.ldap import LDAPProvider

    # service-bind 模式
    p = LDAPProvider(
        name="corp-ad",
        display_name="Corporate AD",
        server_url="ldap://dc01.corp.example.com:389",
        bind_dn="CN=svc-opskg,OU=ServiceAccounts,DC=corp,DC=example,DC=com",
        bind_password="svc-password",
        user_search_base="OU=Users,DC=corp,DC=example,DC=com",
        user_search_filter="(sAMAccountName={username})",
    )
    check("name 设置", p.name == "corp-ad")
    check("server_url 设置", p.server_url == "ldap://dc01.corp.example.com:389")
    check("uses_service_bind=True", p.uses_service_bind is True)
    check("uses_direct_bind=False", p.uses_direct_bind is False)

    d = p.to_dict()
    check("to_dict 含 name", d["name"] == "corp-ad")
    check("to_dict 含 server_url", d["server_url"] == p.server_url)
    check("to_dict uses_service_bind=True", d["uses_service_bind"] is True)
    check("to_dict 不含 bind_password", "bind_password" not in d)
    check("to_dict 不含 bind_dn", "bind_dn" not in d)

    # direct-bind 模式
    p2 = LDAPProvider(
        name="openldap",
        display_name="OpenLDAP",
        server_url="ldap://ldap.example.com:389",
        user_search_base="ou=users,dc=example,dc=com",
        user_dn_template="uid={username},ou=users,dc=example,dc=com",
    )
    check("direct-bind uses_service_bind=False", p2.uses_service_bind is False)
    check("direct-bind uses_direct_bind=True", p2.uses_direct_bind is True)

    d2 = p2.to_dict()
    check("direct-bind to_dict uses_direct_bind=True", d2["uses_direct_bind"] is True)

    # 两种模式都未配置
    p3 = LDAPProvider(
        name="bad",
        display_name="Bad",
        server_url="ldap://bad.example.com:389",
        user_search_base="ou=users,dc=example,dc=com",
    )
    check("未配置 bind 时 uses_service_bind=False", p3.uses_service_bind is False)
    check("未配置 bind 时 uses_direct_bind=False", p3.uses_direct_bind is False)


# ────────── 测试 2：parse_ldap_providers ──────────


def test_parse_providers() -> None:
    section("2. parse_ldap_providers 配置解析")
    from app.auth.ldap import parse_ldap_providers

    # 空字符串
    providers = parse_ldap_providers("")
    check("空字符串返回空列表", providers == [])

    # 有效 JSON
    config = json.dumps(
        [
            {
                "name": "corp-ad",
                "display_name": "Corporate AD",
                "server_url": "ldap://dc01.corp.example.com:389",
                "bind_dn": "CN=svc,DC=corp",
                "bind_password": "pwd",
                "user_search_base": "OU=Users,DC=corp",
            },
            {
                "name": "openldap",
                "display_name": "OpenLDAP",
                "server_url": "ldap://ldap.example.com:389",
                "user_search_base": "ou=users,dc=example,dc=com",
                "user_dn_template": "uid={username},ou=users,dc=example,dc=com",
            },
        ]
    )
    providers = parse_ldap_providers(config)
    check("解析 2 个提供者", len(providers) == 2, f"got {len(providers)}")
    check("第一个 name=corp-ad", providers[0].name == "corp-ad")
    check("第二个 name=openldap", providers[1].name == "openldap")

    # 无效 JSON
    providers = parse_ldap_providers("not-json")
    check("无效 JSON 返回空列表", providers == [])

    # 非数组
    providers = parse_ldap_providers('{"name": "not-array"}')
    check("非数组返回空列表", providers == [])

    # 缺字段
    providers = parse_ldap_providers(json.dumps([{"name": "incomplete"}]))
    check("缺字段提供者被跳过", providers == [])

    # 多余字段被吸收
    providers = parse_ldap_providers(
        json.dumps(
            [
                {
                    "name": "extra",
                    "display_name": "Extra",
                    "server_url": "ldap://x.example.com:389",
                    "user_search_base": "ou=users,dc=x",
                    "extra_field": "ignored",
                }
            ]
        )
    )
    check("多余字段被吸收不报错", len(providers) == 1)


# ────────── 测试 3：_escape_ldap_filter ──────────


def test_escape_ldap_filter() -> None:
    section("3. _escape_ldap_filter 防注入")
    from app.auth.ldap import _escape_ldap_filter

    # 普通字符不转义
    check("普通字符不转义", _escape_ldap_filter("alice") == "alice")

    # * 转义
    result = _escape_ldap_filter("ali*ce")
    check("* 转义为 \\2a", result == "ali\\2ace")

    # ( ) 转义（RFC 4515：两者都必须转义）
    result = _escape_ldap_filter("ali(c)e")
    check("( 转义为 \\28", result == "ali\\28c\\29e")
    result = _escape_ldap_filter("ali)c(e")
    check(") 转义为 \\29", result == "ali\\29c\\28e")

    # \ 转义
    result = _escape_ldap_filter("ali\\ce")
    check("\\ 转义为 \\5c", result == "ali\\5cce")

    # NUL 转义
    result = _escape_ldap_filter("ali\x00ce")
    check("NUL 转义为 \\00", result == "ali\\00ce")

    # 注入攻击：* 试图匹配所有用户
    injection = _escape_ldap_filter("*")
    check("* 单独转义", injection == "\\2a")

    # 注入攻击：(uid=*) 试图绕过 filter
    injection2 = _escape_ldap_filter("(uid=*)")
    check("(uid=*) 转义后无特殊字符", injection2 == "\\28uid=\\2a\\29")

    # 空字符串
    check("空字符串返回空", _escape_ldap_filter("") == "")


# ────────── 测试 4：_escape_ldap_dn ──────────


def test_escape_ldap_dn() -> None:
    section("4. _escape_ldap_dn 防注入")
    from app.auth.ldap import _escape_ldap_dn

    # 普通字符不转义
    check("普通字符不转义", _escape_ldap_dn("alice") == "alice")

    # , 转义（DN 分隔符）
    result = _escape_ldap_dn("alice,smith")
    check(", 转义为 \\2c", result == "alice\\2csmith")

    # + 转义
    result = _escape_ldap_dn("alice+smith")
    check("+ 转义为 \\2b", result == "alice\\2bsmith")

    # " 转义
    result = _escape_ldap_dn('alice"smith')
    check('" 转义为 \\22', result == "alice\\22smith")

    # < > 转义
    result = _escape_ldap_dn("alice<smith>")
    check("< 转义为 \\3c", "< 转义为 \\3c" and "\\3c" in result)
    check("> 转义为 \\3e", "\\3e" in result)

    # ; 转义
    result = _escape_ldap_dn("alice;smith")
    check("; 转义为 \\3b", result == "alice\\3bsmith")

    # # 转义
    result = _escape_ldap_dn("alice#smith")
    check("# 转义为 \\23", result == "alice\\23smith")

    # 空格在开头转义
    result = _escape_ldap_dn(" alice")
    check("开头空格转义为 \\20", result == "\\20alice")

    # 空格在结尾转义
    result = _escape_ldap_dn("alice ")
    check("结尾空格转义为 \\20", result == "alice\\20")

    # 空格在中间不转义
    result = _escape_ldap_dn("alice smith")
    check("中间空格不转义", result == "alice smith")

    # 空字符串
    check("空字符串返回空", _escape_ldap_dn("") == "")


# ────────── 测试 5：authenticate（mock ldap3） ──────────


def test_authenticate_mock() -> None:
    section("5. authenticate（mock ldap3，service-bind + direct-bind + 异常路径）")
    from app.auth.ldap import LDAPProvider, authenticate

    # ── mock ldap3 模块 ──
    class MockEntry:
        def __init__(self, dn, attrs):
            self.entry_dn = dn
            self._attrs = attrs

        def __contains__(self, key):
            return key in self._attrs

        def __getitem__(self, key):
            class _Attr:
                def __init__(self, vals):
                    self.values = vals
            return _Attr(self._attrs[key])

    class MockConnection:
        # 类变量控制行为
        bound_result = True
        search_result = True
        search_entries: list[MockEntry] = []
        search_exception: Exception | None = None
        bind_exception: Exception | None = None

        def __init__(self, server, user=None, password=None, auto_bind=False, **kwargs):
            self.user = user
            self.password = password
            self.bound = False
            self.entries: list[MockEntry] = []
            # 模拟 auto_bind
            if MockConnection.bind_exception:
                raise MockConnection.bind_exception
            self.bound = MockConnection.bound_result

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def search(self, search_base, search_filter, attributes=None, size_limit=0):
            if MockConnection.search_exception:
                raise MockConnection.search_exception
            self.entries = list(MockConnection.search_entries)
            return MockConnection.search_result

    class MockServer:
        def __init__(self, url, use_ssl=False, tls=None):
            self.url = url

    # 准备 mock 模块
    class MockLDAP3:
        ALL = "ALL"
        Server = MockServer
        Connection = MockConnection

        class core:
            class exceptions:
                class LDAPException(Exception):
                    pass

    # 替换 ldap_mod 内的 ldap3 引用（通过 sys.modules）
    original_ldap3 = sys.modules.get("ldap3")
    sys.modules["ldap3"] = MockLDAP3
    # 注意：authenticate 内部使用 from ldap3 import ... 延迟导入
    # 因此我们还需要 mock ldap3.core.exceptions

    original_core = sys.modules.get("ldap3.core")
    original_exc = sys.modules.get("ldap3.core.exceptions")

    class MockCore:
        class exceptions:
            class LDAPException(Exception):
                pass

    sys.modules["ldap3.core"] = MockCore
    sys.modules["ldap3.core.exceptions"] = MockCore.exceptions

    try:
        # ── 5.1 service-bind + user-search + user-bind 成功路径 ──
        MockConnection.bound_result = True
        MockConnection.search_result = True
        MockConnection.search_entries = [
            MockEntry(
                dn="CN=alice,OU=Users,DC=corp,DC=example,DC=com",
                attrs={
                    "mail": ["alice@example.com"],
                    "displayName": ["Alice Wang"],
                    "sAMAccountName": ["alice"],
                },
            )
        ]
        MockConnection.search_exception = None
        MockConnection.bind_exception = None

        p = LDAPProvider(
            name="corp-ad",
            display_name="Corporate AD",
            server_url="ldap://dc01.corp.example.com:389",
            bind_dn="CN=svc,DC=corp",
            bind_password="svc-pwd",
            user_search_base="OU=Users,DC=corp",
            user_search_filter="(sAMAccountName={username})",
        )
        result = authenticate(p, "alice", "alice-password")
        check("service-bind 认证成功", result.success is True)
        check("user_dn 正确", result.user_dn == "CN=alice,OU=Users,DC=corp,DC=example,DC=com")
        check("attributes 含 mail", result.attributes.get("mail") == ["alice@example.com"])
        check("attributes 含 displayName", result.attributes.get("displayName") == ["Alice Wang"])

        # ── 5.2 用户不存在 ──
        MockConnection.search_entries = []
        result = authenticate(p, "nonexistent", "pwd")
        check("用户不存在认证失败", result.success is False)
        check("用户不存在错误信息", "未找到用户" in (result.error or ""))

        # ── 5.3 service bind 失败 ──
        MockConnection.bound_result = False
        result = authenticate(p, "alice", "pwd")
        check("service bind 失败", result.success is False)
        MockConnection.bound_result = True

        # ── 5.4 用户 bind 失败（密码错误） ──
        # 模拟：service bind 成功 + search 成功 + 用户 bind 失败
        call_count = [0]
        original_init = MockConnection.__init__

        def counter_init(self, server, user=None, password=None, auto_bind=False, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # 第一次：service bind，成功
                self.bound = True
                self.user = user
                self.password = password
                self.entries = []
            else:
                # 第二次：user bind，失败
                self.bound = False
                self.user = user

        MockConnection.__init__ = counter_init
        MockConnection.search_entries = [
            MockEntry(
                dn="CN=bob,OU=Users,DC=corp",
                attrs={"mail": ["bob@example.com"]},
            )
        ]
        result = authenticate(p, "bob", "wrong-password")
        check("用户密码错误认证失败", result.success is False)
        check("密码错误信息含'用户名或密码错误'", "用户名或密码错误" in (result.error or ""))
        MockConnection.__init__ = original_init
        call_count[0] = 0

        # ── 5.5 direct-bind 成功路径 ──
        p_direct = LDAPProvider(
            name="openldap",
            display_name="OpenLDAP",
            server_url="ldap://ldap.example.com:389",
            user_search_base="ou=users,dc=example,dc=com",
            user_dn_template="uid={username},ou=users,dc=example,dc=com",
        )
        MockConnection.bound_result = True
        result = authenticate(p_direct, "charlie", "charlie-pwd")
        check("direct-bind 认证成功", result.success is True)
        check("direct-bind user_dn 正确", result.user_dn == "uid=charlie,ou=users,dc=example,dc=com")

        # ── 5.6 direct-bind 失败 ──
        MockConnection.bound_result = False
        result = authenticate(p_direct, "charlie", "wrong")
        check("direct-bind 密码错误认证失败", result.success is False)
        MockConnection.bound_result = True

        # ── 5.7 LDAP 异常 ──
        MockConnection.bind_exception = MockCore.exceptions.LDAPException("connection refused")
        result = authenticate(p, "alice", "pwd")
        check("LDAP 异常认证失败", result.success is False)
        check("LDAP 异常错误信息含 'LDAP 认证异常'", "LDAP 认证异常" in (result.error or ""))
        MockConnection.bind_exception = None

        # ── 5.8 其他异常 ──
        MockConnection.bind_exception = RuntimeError("unexpected")
        result = authenticate(p, "alice", "pwd")
        check("未知异常认证失败", result.success is False)
        check("未知异常错误信息含 '认证异常'", "认证异常" in (result.error or ""))
        MockConnection.bind_exception = None

        # ── 5.9 用户名/密码为空 ──
        result = authenticate(p, "", "pwd")
        check("空用户名认证失败", result.success is False)
        result = authenticate(p, "alice", "")
        check("空密码认证失败", result.success is False)

        # ── 5.10 未配置 bind 方式 ──
        p_no_bind = LDAPProvider(
            name="bad",
            display_name="Bad",
            server_url="ldap://bad:389",
            user_search_base="ou=users,dc=bad",
        )
        result = authenticate(p_no_bind, "alice", "pwd")
        check("未配置 bind 方式认证失败", result.success is False)
        check("未配置 bind 错误信息", "未配置" in (result.error or ""))

        # ── 5.11 LDAP 注入防御 ──
        # 检查搜索 filter 中的特殊字符被转义
        captured_filters: list[str] = []
        original_search = MockConnection.search

        def capturing_search(self, search_base, search_filter, attributes=None, size_limit=0):
            captured_filters.append(search_filter)
            self.entries = []
            return False

        MockConnection.search = capturing_search
        MockConnection.bound_result = True
        # 输入含有 * 试图匹配所有用户
        authenticate(p, "*", "pwd")
        if captured_filters:
            check("LDAP filter 注入防御：* 被转义", "\\2a" in captured_filters[-1])
        else:
            check("LDAP filter 注入防御：filter 被调用", False, "search 未被调用")
        MockConnection.search = original_search
    finally:
        # 恢复 sys.modules
        if original_ldap3 is not None:
            sys.modules["ldap3"] = original_ldap3
        else:
            sys.modules.pop("ldap3", None)
        if original_core is not None:
            sys.modules["ldap3.core"] = original_core
        else:
            sys.modules.pop("ldap3.core", None)
        if original_exc is not None:
            sys.modules["ldap3.core.exceptions"] = original_exc
        else:
            sys.modules.pop("ldap3.core.exceptions", None)


# ────────── 测试 6：find_or_create_ldap_user ──────────


def test_find_or_create_ldap_user() -> None:
    section("6. LDAP 用户映射 find_or_create_ldap_user")
    from app.auth.ldap import _get_mapping_db, find_or_create_ldap_user
    from app.auth.models import get_auth_store

    # 初始化 schema
    store = get_auth_store()
    store.ensure_bootstrap_admin()

    # 清理可能存在的数据
    conn = _get_mapping_db()
    conn.execute("DELETE FROM ldap_mappings")
    conn.execute(
        "DELETE FROM users WHERE username LIKE 'ldap_%' OR username LIKE '%_example.com'"
    )
    conn.commit()
    conn.close()

    # 首次创建
    user1 = find_or_create_ldap_user(
        provider="corp-ad",
        user_dn="CN=alice,OU=Users,DC=corp,DC=example,DC=com",
        username="alice",
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

    # 二次查找（同一 user_dn）
    user2 = find_or_create_ldap_user(
        provider="corp-ad",
        user_dn="CN=alice,OU=Users,DC=corp,DC=example,DC=com",
        username="alice",
        email="alice@example.com",
        name="Alice Updated",
    )
    check("二次查找返回同一用户", user2["id"] == user_id)

    # 不同 provider 同 user_dn 视为不同用户
    user3 = find_or_create_ldap_user(
        provider="other-ad",
        user_dn="CN=alice,OU=Users,DC=corp,DC=example,DC=com",
        username="alice",
        email="alice@example.com",
        name="Alice",
    )
    check("不同 provider 视为新用户", user3["id"] != user_id)

    # 用户被禁用后 LDAP 登录失败
    store.update_user(user_id, active=False)
    try:
        find_or_create_ldap_user(
            provider="corp-ad",
            user_dn="CN=alice,OU=Users,DC=corp,DC=example,DC=com",
            username="alice",
            email="alice@example.com",
            name="Alice",
        )
        check("禁用用户 LDAP 登录失败", False, "未抛异常")
    except ValueError as e:
        check("禁用用户 LDAP 登录失败", True, str(e))

    # 恢复
    store.update_user(user_id, active=True)


# ────────── 测试 7：extract_user_info_from_attributes ──────────


def test_extract_user_info() -> None:
    section("7. extract_user_info_from_attributes（AD/OpenLDAP 兼容）")
    from app.auth.ldap import extract_user_info_from_attributes

    # AD 风格（mail + displayName）
    email, name = extract_user_info_from_attributes(
        username="alice",
        attributes={
            "mail": ["alice@example.com"],
            "displayName": ["Alice Wang"],
            "sAMAccountName": ["alice"],
        },
    )
    check("AD email 解析正确", email == "alice@example.com")
    check("AD name 解析正确", name == "Alice Wang")

    # AD 风格（userPrincipalName + givenName + sn）
    email, name = extract_user_info_from_attributes(
        username="bob",
        attributes={
            "userPrincipalName": ["bob@corp.example.com"],
            "givenName": ["Bob"],
            "sn": ["Smith"],
        },
    )
    check("AD userPrincipalName 解析正确", email == "bob@corp.example.com")
    check("AD name = givenName + sn", name == "Bob Smith")

    # OpenLDAP 风格（mail + cn）
    email, name = extract_user_info_from_attributes(
        username="charlie",
        attributes={
            "mail": ["charlie@example.com"],
            "cn": ["Charlie Brown"],
            "uid": ["charlie"],
        },
    )
    check("OpenLDAP email 解析正确", email == "charlie@example.com")
    check("OpenLDAP cn 解析正确", name == "Charlie Brown")

    # 仅有 username，无属性
    email, name = extract_user_info_from_attributes(
        username="dave",
        attributes={},
    )
    check("无属性时 email=None", email is None)
    check("无属性时 name=用户名", name == "dave")

    # userPrincipalName 看起来像 email 时也作为 email
    email, name = extract_user_info_from_attributes(
        username="eve",
        attributes={
            "userPrincipalName": ["eve@corp.example.com"],
            "displayName": ["Eve"],
        },
    )
    check("userPrincipalName 作为 email", email == "eve@corp.example.com")

    # 单值字符串
    email, name = extract_user_info_from_attributes(
        username="frank",
        attributes={"mail": "frank@example.com", "cn": "Frank"},
    )
    check("单值字符串 email 解析正确", email == "frank@example.com")
    check("单值字符串 name 解析正确", name == "Frank")


# ────────── 测试 8：/auth/ldap/status + providers 端点 ──────────


def test_ldap_status_endpoints() -> None:
    section("8. /auth/ldap/status + /auth/ldap/providers 端点")
    from fastapi.testclient import TestClient

    os.environ["LDAP_PROVIDERS"] = json.dumps(
        [
            {
                "name": "test-ad",
                "display_name": "Test AD",
                "server_url": "ldap://dc01.test.example.com:389",
                "bind_dn": "CN=svc,DC=test",
                "bind_password": "svc-pwd",
                "user_search_base": "OU=Users,DC=test",
            }
        ]
    )
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)

    resp = client.get("/auth/ldap/status")
    check("status 返回 200", resp.status_code == 200)
    data = resp.json()
    check("status enabled=True", data["enabled"] is True)
    check("status providers 含 1 个", len(data["providers"]) == 1)
    check("status provider name 正确", data["providers"][0]["name"] == "test-ad")

    resp = client.get("/auth/ldap/providers")
    check("providers 返回 200", resp.status_code == 200)
    data = resp.json()
    check("providers count=1", data["count"] == 1)
    check(
        "providers 不含 bind_password",
        "bind_password" not in str(data),
    )

    os.environ["LDAP_PROVIDERS"] = ""
    get_settings.cache_clear()


# ────────── 测试 9：/auth/ldap/{provider}/login 端到端 ──────────


def test_ldap_login_e2e() -> None:
    section("9. /auth/ldap/{provider}/login 端到端（mock ldap3）")
    from fastapi.testclient import TestClient

    os.environ["LDAP_PROVIDERS"] = json.dumps(
        [
            {
                "name": "corp-ad",
                "display_name": "Corporate AD",
                "server_url": "ldap://dc01.corp.example.com:389",
                "bind_dn": "CN=svc,DC=corp",
                "bind_password": "svc-pwd",
                "user_search_base": "OU=Users,DC=corp",
                "user_search_filter": "(sAMAccountName={username})",
            }
        ]
    )
    from app.config import get_settings

    get_settings.cache_clear()

    # mock ldap3
    class MockEntry:
        def __init__(self, dn, attrs):
            self.entry_dn = dn
            self._attrs = attrs

        def __contains__(self, key):
            return key in self._attrs

        def __getitem__(self, key):
            class _Attr:
                def __init__(self, vals):
                    self.values = vals
            return _Attr(self._attrs[key])

    class MockConnection:
        bound_result = True
        search_entries: list[MockEntry] = []

        def __init__(self, server, user=None, password=None, auto_bind=False, **kwargs):
            self.bound = MockConnection.bound_result
            self.entries = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def search(self, search_base, search_filter, attributes=None, size_limit=0):
            self.entries = list(MockConnection.search_entries)
            return len(self.entries) > 0

    class MockServer:
        def __init__(self, url, use_ssl=False, tls=None):
            pass

    class MockLDAP3:
        ALL = "ALL"
        Server = MockServer
        Connection = MockConnection

    class MockCore:
        class exceptions:
            class LDAPException(Exception):
                pass

    original_ldap3 = sys.modules.get("ldap3")
    original_core = sys.modules.get("ldap3.core")
    original_exc = sys.modules.get("ldap3.core.exceptions")

    sys.modules["ldap3"] = MockLDAP3
    sys.modules["ldap3.core"] = MockCore
    sys.modules["ldap3.core.exceptions"] = MockCore.exceptions

    try:
        # 成功路径
        MockConnection.bound_result = True
        MockConnection.search_entries = [
            MockEntry(
                dn="CN=ldapuser,OU=Users,DC=corp,DC=example,DC=com",
                attrs={
                    "mail": ["ldapuser@example.com"],
                    "displayName": ["LDAP User"],
                    "sAMAccountName": ["ldapuser"],
                },
            )
        ]

        from app.main import app

        client = TestClient(app)

        resp = client.post(
            "/auth/ldap/corp-ad/login",
            json={"username": "ldapuser", "password": "secret", "redirect": "/wiki"},
        )
        check("login 返回 200", resp.status_code == 200)
        data = resp.json()
        check("login success=True", data["success"] is True)
        check("login 含 token", "token" in data and data["token"])
        check("login redirect 正确", data["redirect"] == "/wiki")
        check("login user 含 username", data["user"]["username"] == "ldapuser@example.com")
        check("login user 含 email", data["user"]["email"] == "ldapuser@example.com")
        check("login user 含 role", data["user"]["role"] == "viewer")

        # 验证用户已创建
        from app.auth.models import get_auth_store

        store = get_auth_store()
        user = store.get_user("ldapuser@example.com")
        check("LDAP 用户已创建", user is not None)

        # 二次登录（同一用户）
        resp = client.post(
            "/auth/ldap/corp-ad/login",
            json={"username": "ldapuser", "password": "secret"},
        )
        check("二次 login success=True", resp.json()["success"] is True)

        # 用户不存在
        MockConnection.search_entries = []
        resp = client.post(
            "/auth/ldap/corp-ad/login",
            json={"username": "nonexistent", "password": "pwd"},
        )
        data = resp.json()
        check("用户不存在 success=False", data["success"] is False)
        check("用户不存在错误信息含'未找到'", "未找到" in data.get("error", ""))

        # 密码错误（user bind 失败）
        call_count = [0]

        original_init = MockConnection.__init__

        def counter_init(self, server, user=None, password=None, auto_bind=False, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                self.bound = True
                self.entries = []
            else:
                self.bound = False

        MockConnection.__init__ = counter_init
        MockConnection.search_entries = [
            MockEntry(
                dn="CN=baduser,OU=Users,DC=corp",
                attrs={"mail": ["baduser@example.com"]},
            )
        ]
        resp = client.post(
            "/auth/ldap/corp-ad/login",
            json={"username": "baduser", "password": "wrong"},
        )
        data = resp.json()
        check("密码错误 success=False", data["success"] is False)
        check("密码错误信息含'用户名或密码错误'", "用户名或密码错误" in data.get("error", ""))
        MockConnection.__init__ = original_init
        call_count[0] = 0

        # 未配置的 provider
        resp = client.post(
            "/auth/ldap/nonexistent/login",
            json={"username": "x", "password": "y"},
        )
        check("未配置 provider 返回 404", resp.status_code == 404)

        # 缺字段
        resp = client.post(
            "/auth/ldap/corp-ad/login",
            json={"username": "x"},  # 缺 password
        )
        check("缺 password 返回 422", resp.status_code == 422)
    finally:
        if original_ldap3 is not None:
            sys.modules["ldap3"] = original_ldap3
        else:
            sys.modules.pop("ldap3", None)
        if original_core is not None:
            sys.modules["ldap3.core"] = original_core
        else:
            sys.modules.pop("ldap3.core", None)
        if original_exc is not None:
            sys.modules["ldap3.core.exceptions"] = original_exc
        else:
            sys.modules.pop("ldap3.core.exceptions", None)
        os.environ["LDAP_PROVIDERS"] = ""
        get_settings.cache_clear()


# ────────── 测试 10：LDAP 关闭时 status ──────────


def test_ldap_disabled() -> None:
    section("10. LDAP 未配置时关闭")
    from fastapi.testclient import TestClient

    os.environ["LDAP_PROVIDERS"] = ""
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)

    resp = client.get("/auth/ldap/status")
    check("未配置 status 返回 200", resp.status_code == 200)
    data = resp.json()
    check("未配置 enabled=False", data["enabled"] is False)
    check("未配置 providers 为空", data["providers"] == [])

    resp = client.get("/auth/ldap/providers")
    check("未配置 providers 返回 200", resp.status_code == 200)
    data = resp.json()
    check("未配置 count=0", data["count"] == 0)


# ────────── 测试 11：LDAP/SAML/OIDC 三者共存 ──────────


def test_ldap_saml_oidc_coexist() -> None:
    section("11. LDAP / SAML / OIDC 三者路由独立共存")
    from fastapi.testclient import TestClient

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
    os.environ["LDAP_PROVIDERS"] = json.dumps(
        [
            {
                "name": "corp-ad",
                "display_name": "Corporate AD",
                "server_url": "ldap://dc01.corp.example.com:389",
                "bind_dn": "CN=svc,DC=corp",
                "bind_password": "svc-pwd",
                "user_search_base": "OU=Users,DC=corp",
            }
        ]
    )
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)

    # 三个 status 端点都应启用
    for kind in ("oidc", "saml", "ldap"):
        resp = client.get(f"/auth/{kind}/status")
        check(f"{kind} status 返回 200", resp.status_code == 200)
        data = resp.json()
        check(f"{kind} enabled=True", data["enabled"] is True)
        check(f"{kind} providers 含 1 个", len(data["providers"]) == 1)

    # 互不干扰
    oidc_names = [p["name"] for p in client.get("/auth/oidc/status").json()["providers"]]
    saml_names = [p["name"] for p in client.get("/auth/saml/status").json()["providers"]]
    ldap_names = [p["name"] for p in client.get("/auth/ldap/status").json()["providers"]]
    check("OIDC 含 google", "google" in oidc_names)
    check("SAML 含 okta", "okta" in saml_names)
    check("LDAP 含 corp-ad", "corp-ad" in ldap_names)
    check("OIDC 不含 okta/corp-ad", "okta" not in oidc_names and "corp-ad" not in oidc_names)
    check("SAML 不含 google/corp-ad", "google" not in saml_names and "corp-ad" not in saml_names)
    check("LDAP 不含 google/okta", "google" not in ldap_names and "okta" not in ldap_names)

    # 清除配置
    os.environ["OIDC_PROVIDERS"] = ""
    os.environ["SAML_PROVIDERS"] = ""
    os.environ["LDAP_PROVIDERS"] = ""
    get_settings.cache_clear()


def main() -> int:
    print("=" * 60)
    print("LDAP 认证验证脚本（S13-2 企业级 SSO 补齐）")
    print("=" * 60)

    test_ldap_provider_config()
    test_parse_providers()
    test_escape_ldap_filter()
    test_escape_ldap_dn()
    test_authenticate_mock()
    test_find_or_create_ldap_user()
    test_extract_user_info()
    test_ldap_status_endpoints()
    test_ldap_login_e2e()
    test_ldap_disabled()
    test_ldap_saml_oidc_coexist()

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
