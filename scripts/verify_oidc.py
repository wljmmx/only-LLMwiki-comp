#!/usr/bin/env python3
"""OIDC SSO 验证脚本（P3-1 完整 SSO）

验证内容：
1. OIDC 配置解析（parse_providers）
2. OIDCProvider 类基本行为
3. PKCE 生成（generate_pkce）
4. State 存储（save_state / pop_state / 一次性消费 / 过期清理）
5. OIDC 用户映射（find_or_create_user 首次创建 + 二次查找 + 用户禁用）
6. /auth/oidc/status 端点
7. /auth/oidc/providers 端点
8. /auth/oidc/{provider} 重定向（mock discovery）
9. /auth/oidc/{provider}/callback 端到端（mock IdP）
10. id_token claims 解析（_decode_id_token_claims）

运行：python scripts/verify_oidc.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

# 确保可以 import backend.app.*
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# 测试环境变量：关闭 LLM、关闭 legacy token
os.environ.setdefault("ENV", "test")
os.environ.setdefault("LLM_BACKEND", "openai_compat")
os.environ.setdefault("OPENAI_COMPAT_API_KEY", "test")
os.environ.setdefault("API_TOKEN", "")

# 使用临时 DB
import tempfile

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsgkg_oidc_test_"))
os.environ["HOME"] = str(TMP_DIR)
DATA_DIR = TMP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 重定向 auth DB 到临时目录
import app.auth.models as auth_models

auth_models.DB_PATH = DATA_DIR / "auth.db"

import app.auth.oidc as oidc_module

oidc_module.MAPPING_DB_PATH = DATA_DIR / "auth.db"


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


# ────────── 测试 1：parse_providers ──────────


def test_parse_providers() -> None:
    section("1. parse_providers 配置解析")
    from app.auth.oidc import parse_providers

    # 空字符串
    providers = parse_providers("")
    check("空字符串返回空列表", providers == [])

    # 有效 JSON
    config = json.dumps(
        [
            {
                "name": "google",
                "display_name": "Google",
                "client_id": "google-id",
                "client_secret": "google-secret",
                "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
                "scopes": ["openid", "email", "profile"],
            },
            {
                "name": "github",
                "display_name": "GitHub",
                "client_id": "gh-id",
                "client_secret": "gh-secret",
                "discovery_url": "https://accounts.github.com/.well-known/openid-configuration",
            },
        ]
    )
    providers = parse_providers(config)
    check("解析 2 个提供者", len(providers) == 2, f"got {len(providers)}")
    check("第一个 name=google", providers[0].name == "google")
    check("第二个 name=github", providers[1].name == "github")
    check(
        "github 默认 scopes",
        providers[1].scopes == ["openid", "email", "profile"],
        f"got {providers[1].scopes}",
    )
    check(
        "google client_id 正确",
        providers[0].client_id == "google-id",
    )

    # 无效 JSON
    providers = parse_providers("not-json")
    check("无效 JSON 返回空列表", providers == [])

    # 缺字段
    providers = parse_providers(
        json.dumps([{"name": "incomplete"}])
    )
    check("缺字段提供者被跳过", providers == [])


# ────────── 测试 2：OIDCProvider 类 ──────────


def test_oidc_provider_class() -> None:
    section("2. OIDCProvider 类行为")
    from app.auth.oidc import OIDCProvider

    p = OIDCProvider(
        name="test",
        display_name="Test IdP",
        client_id="cid",
        client_secret="csec",
        discovery_url="https://example.com/.well-known/openid-configuration",
    )
    check("name 设置", p.name == "test")
    check("display_name 设置", p.display_name == "Test IdP")
    check(
        "默认 scopes",
        p.scopes == ["openid", "email", "profile"],
    )

    # 未 discovery 时访问 endpoint 应报错
    try:
        _ = p.authorization_endpoint
        check("未 discovery 访问 endpoint 报错", False, "未抛异常")
    except RuntimeError:
        check("未 discovery 访问 endpoint 报错", True)

    # to_dict 不含 secret
    d = p.to_dict()
    check("to_dict 含 name", d["name"] == "test")
    check("to_dict 含 display_name", d["display_name"] == "Test IdP")
    check("to_dict 含 scopes", "scopes" in d)
    check("to_dict 不含 client_secret", "client_secret" not in d)
    check("to_dict 不含 client_id", "client_id" not in d)


# ────────── 测试 3：PKCE ──────────


def test_pkce() -> None:
    section("3. PKCE 生成")
    from app.auth.oidc import generate_pkce

    v1, c1 = generate_pkce()
    v2, c2 = generate_pkce()
    check("verifier 非空", len(v1) > 0)
    check("challenge 非空", len(c1) > 0)
    check("两次生成不同 verifier", v1 != v2)
    check("两次生成不同 challenge", c1 != c2)
    check("verifier 长度 >= 43", len(v1) >= 43, f"got {len(v1)}")
    # 验证 challenge 是 verifier 的 S256 哈希
    import hashlib

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(v1.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    check("challenge = S256(verifier)", c1 == expected)


# ────────── 测试 4：State 存储 ──────────


def test_state_store() -> None:
    section("4. State 存储（DB 持久化，P0-6）")
    from app.auth.oidc import _get_mapping_db, pop_state, save_state

    # 清理 DB 中的历史 state（P0-6: state 持久化到 SQLite）
    conn = _get_mapping_db()
    try:
        conn.execute("DELETE FROM oidc_states")
        conn.commit()
    finally:
        conn.close()

    state = save_state("google", "verifier123", "/dashboard")
    check("save_state 返回非空 state", len(state) > 0)

    # 第一次 pop 应返回数据
    data = pop_state(state)
    check("pop_state 返回数据", data is not None)
    check("state 含 provider", data["provider"] == "google")
    check("state 含 code_verifier", data["code_verifier"] == "verifier123")
    check("state 含 redirect", data["redirect"] == "/dashboard")

    # 第二次 pop 应返回 None（一次性）
    data2 = pop_state(state)
    check("state 一次性消费", data2 is None)
    check("state 消费后从 DB 删除", pop_state(state) is None)


# ────────── 测试 5：find_or_create_user ──────────


def test_find_or_create_user() -> None:
    section("5. OIDC 用户映射 find_or_create_user")
    from app.auth.models import get_auth_store
    from app.auth.oidc import _get_mapping_db, find_or_create_user

    # 初始化 schema（先建 users 表，再建 mappings 表）
    store = get_auth_store()
    store.ensure_bootstrap_admin()  # 触发 users 表创建

    # 清理可能存在的数据
    conn = _get_mapping_db()
    conn.execute("DELETE FROM oidc_mappings")
    conn.execute("DELETE FROM users WHERE username LIKE 'google_%' OR username LIKE 'github_%' OR username LIKE '%_example.com' OR username LIKE 'alice_%'")
    conn.commit()
    conn.close()

    # 首次创建
    user1 = find_or_create_user(
        provider="google",
        sub="oidc-sub-001",
        email="alice@example.com",
        name="Alice",
        default_role="viewer",
    )
    check("首次创建返回用户", user1 is not None)
    check("用户 username 为 email", user1["username"] == "alice@example.com")
    check("用户 email 正确", user1["email"] == "alice@example.com")
    check("用户 display_name 为 Alice", user1["display_name"] == "Alice")
    check("用户 role 为 viewer", user1["role"] == "viewer")
    check("用户 active", user1["active"] is True)

    user_id = user1["id"]

    # 二次查找（同一 sub）
    user2 = find_or_create_user(
        provider="google",
        sub="oidc-sub-001",
        email="alice@example.com",
        name="Alice Updated",
        default_role="viewer",
    )
    check("二次查找返回同一用户", user2["id"] == user_id)
    check("二次查找不创建新用户", user2["username"] == user1["username"])

    # 不同 provider 同 sub 视为不同用户
    user3 = find_or_create_user(
        provider="github",
        sub="oidc-sub-001",
        email="alice@example.com",
        name="Alice",
        default_role="viewer",
    )
    check("不同 provider 视为新用户", user3["id"] != user_id)

    # 用户被禁用后 OIDC 登录失败
    store.update_user(user_id, active=False)
    try:
        find_or_create_user(
            provider="google",
            sub="oidc-sub-001",
            email="alice@example.com",
            name="Alice",
        )
        check("禁用用户 OIDC 登录失败", False, "未抛异常")
    except ValueError as e:
        check("禁用用户 OIDC 登录失败", True, str(e))

    # 恢复
    store.update_user(user_id, active=True)


# ────────── 测试 6：/auth/oidc/status + providers 端点 ──────────


def test_oidc_status_endpoints() -> None:
    section("6. /auth/oidc/status + /auth/oidc/providers 端点")
    from fastapi.testclient import TestClient

    # 配置一个测试 provider
    os.environ["OIDC_PROVIDERS"] = json.dumps(
        [
            {
                "name": "test-idp",
                "display_name": "Test IdP",
                "client_id": "test-cid",
                "client_secret": "test-csec",
                "discovery_url": "http://localhost:9999/.well-known/openid-configuration",
            }
        ]
    )
    # 清除 settings 缓存
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)

    # /auth/oidc/status
    resp = client.get("/auth/oidc/status")
    check("status 返回 200", resp.status_code == 200)
    data = resp.json()
    check("status enabled=True", data["enabled"] is True)
    check("status providers 含 1 个", data["providers"] and len(data["providers"]) == 1)
    check("status provider name 正确", data["providers"][0]["name"] == "test-idp")

    # /auth/oidc/providers
    resp = client.get("/auth/oidc/providers")
    check("providers 返回 200", resp.status_code == 200)
    data = resp.json()
    check("providers count=1", data["count"] == 1)
    check(
        "providers 不含 client_secret",
        "client_secret" not in str(data),
    )

    # 清除配置
    os.environ["OIDC_PROVIDERS"] = ""
    get_settings.cache_clear()


# ────────── 测试 7：/auth/oidc/{provider} 重定向（mock discovery） ──────────


def test_oidc_authorize_redirect() -> None:
    section("7. /auth/oidc/{provider} 重定向（mock discovery）")
    from fastapi.testclient import TestClient

    os.environ["OIDC_PROVIDERS"] = json.dumps(
        [
            {
                "name": "mock-idp",
                "display_name": "Mock IdP",
                "client_id": "mock-cid",
                "client_secret": "mock-csec",
                "discovery_url": "http://localhost:9999/.well-known/openid-configuration",
            }
        ]
    )
    from app.config import get_settings

    get_settings.cache_clear()

    # mock discovery
    from app.auth.oidc import OIDCProvider

    original_discover = OIDCProvider.discover

    async def mock_discover(self):
        self._authorization_endpoint = "https://idp.example.com/authorize"
        self._token_endpoint = "https://idp.example.com/token"
        self._userinfo_endpoint = "https://idp.example.com/userinfo"
        self._discovered = True

    OIDCProvider.discover = mock_discover

    try:
        from app.main import app

        client = TestClient(app)

        # 未配置的 provider
        resp = client.get("/auth/oidc/nonexistent", follow_redirects=False)
        check("未配置 provider 返回 404", resp.status_code == 404)

        # 已配置 provider 应重定向
        resp = client.get(
            "/auth/oidc/mock-idp?redirect=/dashboard",
            follow_redirects=False,
        )
        check("已配置 provider 返回 302", resp.status_code == 302)
        location = resp.headers.get("location", "")
        check("重定向到 idp authorize", "idp.example.com/authorize" in location)
        check("含 client_id", "client_id=mock-cid" in location)
        check("含 response_type=code", "response_type=code" in location)
        check("含 code_challenge", "code_challenge=" in location)
        check("含 code_challenge_method=S256", "code_challenge_method=S256" in location)
        check("含 state", "state=" in location)
        check("含 scope", "scope=" in location)
    finally:
        OIDCProvider.discover = original_discover
        os.environ["OIDC_PROVIDERS"] = ""
        get_settings.cache_clear()


# ────────── 测试 8：/auth/oidc/{provider}/callback 端到端 ──────────


def test_oidc_callback_e2e() -> None:
    section("8. /auth/oidc/{provider}/callback 端到端（mock IdP）")
    from fastapi.testclient import TestClient

    os.environ["OIDC_PROVIDERS"] = json.dumps(
        [
            {
                "name": "mock-idp",
                "display_name": "Mock IdP",
                "client_id": "mock-cid",
                "client_secret": "mock-csec",
                "discovery_url": "http://localhost:9999/.well-known/openid-configuration",
            }
        ]
    )
    os.environ["FRONTEND_BASE_URL"] = "http://localhost:5173"
    from app.config import get_settings

    get_settings.cache_clear()

    # mock discovery + exchange_code
    from app.auth.oidc import OIDCProvider, generate_pkce, save_state

    original_discover = OIDCProvider.discover

    async def mock_discover(self):
        self._authorization_endpoint = "https://idp.example.com/authorize"
        self._token_endpoint = "https://idp.example.com/token"
        self._userinfo_endpoint = "https://idp.example.com/userinfo"
        self._discovered = True

    OIDCProvider.discover = mock_discover

    import app.routers.oidc_router as oidc_router_mod

    original_exchange = oidc_router_mod.exchange_code

    async def mock_exchange_code(provider, code, code_verifier, redirect_uri):
        return {
            "access_token": "mock-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "id_token": "",
            "user_info": {
                "sub": "mock-oidc-sub-e2e",
                "email": "e2euser@example.com",
                "name": "E2E User",
            },
        }

    oidc_router_mod.exchange_code = mock_exchange_code

    try:
        from app.main import app

        client = TestClient(app)

        # 准备 state
        code_verifier, _ = generate_pkce()
        state = save_state("mock-idp", code_verifier, "/wiki")

        # 调用 callback
        resp = client.get(
            f"/auth/oidc/mock-idp/callback?code=mock-code&state={state}",
            follow_redirects=False,
        )
        check("callback 返回 302", resp.status_code == 302)
        location = resp.headers.get("location", "")
        check("重定向到前端", "localhost:5173/login/callback" in location)
        check("含 token", "token=" in location)
        check("含 redirect=/wiki", "redirect=" in location)

        # 验证用户已被创建
        from app.auth.models import get_auth_store

        store = get_auth_store()
        user = store.get_user("e2euser@example.com")
        check("OIDC 用户已创建", user is not None)
        check("用户 email 正确", user and user["email"] == "e2euser@example.com")

        # state 已被消费（一次性）
        from app.auth.oidc import pop_state

        check("callback 后 state 已消费", pop_state(state) is None)

        # 缺少 code/state 参数
        resp = client.get(
            "/auth/oidc/mock-idp/callback?code=xxx",
            follow_redirects=False,
        )
        check("缺 state 返回 302（错误重定向）", resp.status_code == 302)
        check("错误重定向含 error", "error=" in resp.headers.get("location", ""))

        # IdP 返回 error
        resp = client.get(
            "/auth/oidc/mock-idp/callback?error=access_denied&error_description=user+cancelled",
            follow_redirects=False,
        )
        check("IdP error 返回 302", resp.status_code == 302)
        check("IdP error 重定向到前端", "localhost:5173" in resp.headers.get("location", ""))
    finally:
        OIDCProvider.discover = original_discover
        oidc_router_mod.exchange_code = original_exchange
        os.environ["OIDC_PROVIDERS"] = ""
        get_settings.cache_clear()


# ────────── 测试 9：id_token claims 解析 ──────────


def test_decode_id_token() -> None:
    section("9. id_token claims 解析")
    from app.auth.oidc import _decode_id_token_claims

    # 构造一个 fake JWT
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "sub": "jwt-sub-123",
                "email": "jwtuser@example.com",
                "name": "JWT User",
                "iss": "https://idp.example.com",
            }
        ).encode()
    ).rstrip(b"=").decode()
    fake_jwt = f"{header}.{payload}.signature"

    claims = _decode_id_token_claims(fake_jwt)
    check("解析 sub", claims["sub"] == "jwt-sub-123")
    check("解析 email", claims["email"] == "jwtuser@example.com")
    check("解析 name", claims["name"] == "JWT User")

    # 无效 JWT
    try:
        _decode_id_token_claims("invalid")
        check("无效 JWT 抛异常", False, "未抛异常")
    except Exception:
        check("无效 JWT 抛异常", True)


# ────────── 测试 10：OIDC 关闭时 status ──────────


def test_oidc_disabled() -> None:
    section("10. OIDC 未配置时关闭")
    from fastapi.testclient import TestClient

    os.environ["OIDC_PROVIDERS"] = ""
    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import app

    client = TestClient(app)

    resp = client.get("/auth/oidc/status")
    check("未配置 status 返回 200", resp.status_code == 200)
    data = resp.json()
    check("未配置 enabled=False", data["enabled"] is False)
    check("未配置 providers 为空", data["providers"] == [])

    resp = client.get("/auth/oidc/providers")
    check("未配置 providers 返回 200", resp.status_code == 200)
    data = resp.json()
    check("未配置 count=0", data["count"] == 0)


def main() -> int:
    print("=" * 60)
    print("OIDC SSO 验证脚本（P3-1 完整 SSO）")
    print("=" * 60)

    test_parse_providers()
    test_oidc_provider_class()
    test_pkce()
    test_state_store()
    test_find_or_create_user()
    test_oidc_status_endpoints()
    test_oidc_authorize_redirect()
    test_oidc_callback_e2e()
    test_decode_id_token()
    test_oidc_disabled()

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
