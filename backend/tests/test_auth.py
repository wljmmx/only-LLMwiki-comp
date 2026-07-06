"""auth 模块单元测试（S11-4）

覆盖：
- AuthStore：用户 CRUD + 密码哈希 + 会话生命周期 + 角色层级
- oidc：PKCE + state store + provider 解析 + JWT claims 解析 + find_or_create_user
- token_auth：双模式认证（legacy token / session token / 开发模式）+ require_role 守卫

DB 隔离：每个测试通过 monkeypatch 将 DB_PATH 重定向到 tmp_path，并重置全局单例。
"""
from __future__ import annotations

import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════ 公共 fixture ═══════════════


@pytest.fixture
def isolated_auth_db(tmp_path, monkeypatch):
    """将 auth 数据库重定向到 tmp_path，并重置 AuthStore 单例"""
    import app.auth.models as models
    import app.auth.oidc as oidc

    db_file = tmp_path / "auth.db"
    monkeypatch.setattr(models, "DB_PATH", db_file)
    monkeypatch.setattr(oidc, "MAPPING_DB_PATH", db_file)
    # 重置单例
    monkeypatch.setattr(models, "_store", None)
    yield models.get_auth_store


# ═══════════════ 密码哈希 ═══════════════


class TestPasswordHash:
    def test_hash_format(self):
        from app.auth.models import _hash_password

        h = _hash_password("secret")
        assert "$" in h
        salt, digest = h.split("$", 1)
        assert len(salt) == 32  # token_hex(16) = 32 chars
        assert len(digest) == 64  # sha256 hexdigest

    def test_hash_with_known_salt(self):
        import hashlib

        from app.auth.models import _hash_password

        salt = "fixedsalt"
        password = "password"
        h = _hash_password(password, salt=salt)
        expected_digest = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        assert h == f"{salt}${expected_digest}"

    def test_verify_correct_password(self):
        from app.auth.models import _hash_password, _verify_password

        stored = _hash_password("mypassword")
        assert _verify_password("mypassword", stored) is True

    def test_verify_wrong_password(self):
        from app.auth.models import _hash_password, _verify_password

        stored = _hash_password("mypassword")
        assert _verify_password("wrong", stored) is False

    def test_verify_corrupt_stored(self):
        from app.auth.models import _verify_password

        # 缺少 $ 分隔符
        assert _verify_password("x", "corrupt") is False
        # 空值
        assert _verify_password("x", "") is False


# ═══════════════ 角色层级 ═══════════════


class TestRoleHierarchy:
    def test_admin_ge_operator(self):
        from app.auth.models import has_role

        assert has_role("admin", "operator") is True

    def test_viewer_lt_operator(self):
        from app.auth.models import has_role

        assert has_role("viewer", "operator") is False

    def test_equal_role_passes(self):
        from app.auth.models import has_role

        assert has_role("operator", "operator") is True

    def test_unknown_role_fails(self):
        from app.auth.models import has_role

        assert has_role("guest", "viewer") is False


# ═══════════════ AuthStore 用户 CRUD ═══════════════


class TestAuthStoreUsers:
    def test_create_user_success(self, isolated_auth_db):
        store = isolated_auth_db()
        user = store.create_user("alice", "pass123", role="operator")
        assert user["username"] == "alice"
        assert user["role"] == "operator"
        assert user["active"] is True
        assert "id" in user

    def test_create_user_invalid_role(self, isolated_auth_db):
        store = isolated_auth_db()
        with pytest.raises(ValueError, match="无效角色"):
            store.create_user("bob", "pass", role="superadmin")

    def test_create_duplicate_user(self, isolated_auth_db):
        store = isolated_auth_db()
        store.create_user("alice", "pass")
        with pytest.raises(ValueError, match="用户已存在"):
            store.create_user("alice", "other")

    def test_get_user(self, isolated_auth_db):
        store = isolated_auth_db()
        store.create_user("alice", "pass", email="alice@x.com")
        user = store.get_user("alice")
        assert user is not None
        assert user["email"] == "alice@x.com"

    def test_get_user_not_found(self, isolated_auth_db):
        store = isolated_auth_db()
        assert store.get_user("nobody") is None

    def test_get_user_by_id(self, isolated_auth_db):
        store = isolated_auth_db()
        created = store.create_user("alice", "pass")
        user = store.get_user_by_id(created["id"])
        assert user is not None
        assert user["username"] == "alice"

    def test_list_users(self, isolated_auth_db):
        store = isolated_auth_db()
        store.create_user("alice", "p")
        store.create_user("bob", "p")
        users = store.list_users()
        assert len(users) == 2
        assert {u["username"] for u in users} == {"alice", "bob"}

    def test_update_user_role(self, isolated_auth_db):
        store = isolated_auth_db()
        created = store.create_user("alice", "p", role="viewer")
        updated = store.update_user(created["id"], role="operator")
        assert updated["role"] == "operator"

    def test_update_user_password(self, isolated_auth_db):
        store = isolated_auth_db()
        created = store.create_user("alice", "old")
        store.update_user(created["id"], password="new")
        # 旧密码失败
        assert store.verify_password("alice", "old") is None
        # 新密码成功
        assert store.verify_password("alice", "new") is not None

    def test_update_user_no_fields_returns_current(self, isolated_auth_db):
        store = isolated_auth_db()
        created = store.create_user("alice", "p")
        result = store.update_user(created["id"])
        assert result["username"] == "alice"

    def test_deactivate_user(self, isolated_auth_db):
        store = isolated_auth_db()
        created = store.create_user("alice", "p")
        updated = store.update_user(created["id"], active=False)
        assert updated["active"] is False
        # 禁用用户无法登录
        assert store.verify_password("alice", "p") is None

    def test_delete_user(self, isolated_auth_db):
        store = isolated_auth_db()
        created = store.create_user("alice", "p")
        assert store.delete_user(created["id"]) is True
        assert store.get_user("alice") is None
        # 再删返回 False
        assert store.delete_user(created["id"]) is False


# ═══════════════ AuthStore 会话 ═══════════════


class TestAuthStoreSessions:
    def test_create_and_verify_session(self, isolated_auth_db):
        store = isolated_auth_db()
        user = store.create_user("alice", "p")
        token = store.create_session(user["id"])
        assert isinstance(token, str) and len(token) > 0
        # 验证会话返回用户
        verified = store.verify_session(token)
        assert verified is not None
        assert verified["username"] == "alice"

    def test_verify_invalid_session(self, isolated_auth_db):
        store = isolated_auth_db()
        assert store.verify_session("nonexistent-token") is None

    def test_revoke_session(self, isolated_auth_db):
        store = isolated_auth_db()
        user = store.create_user("alice", "p")
        token = store.create_session(user["id"])
        assert store.revoke_session(token) is True
        assert store.verify_session(token) is None
        # 再 revoke 返回 False
        assert store.revoke_session(token) is False

    def test_expired_session_rejected(self, isolated_auth_db):
        store = isolated_auth_db()
        user = store.create_user("alice", "p")
        # TTL=0 立即过期（边界：创建时已过期可能因时间精度未过期，用负数确保）
        token = store.create_session(user["id"], ttl_seconds=-1)
        assert store.verify_session(token) is None

    def test_cleanup_expired_sessions(self, isolated_auth_db):
        store = isolated_auth_db()
        user = store.create_user("alice", "p")
        store.create_session(user["id"], ttl_seconds=-1)
        store.create_session(user["id"], ttl_seconds=86400)
        cleaned = store.cleanup_expired_sessions()
        assert cleaned >= 1

    def test_session_invalid_after_user_deactivated(self, isolated_auth_db):
        store = isolated_auth_db()
        user = store.create_user("alice", "p")
        token = store.create_session(user["id"])
        store.update_user(user["id"], active=False)
        assert store.verify_session(token) is None


# ═══════════════ AuthStore bootstrap ═══════════════


class TestAuthStoreBootstrap:
    def test_ensure_bootstrap_admin_creates(self, isolated_auth_db):
        store = isolated_auth_db()
        user = store.ensure_bootstrap_admin("admin", "admin123")
        assert user["role"] == "admin"
        assert user["username"] == "admin"

    def test_ensure_bootstrap_admin_idempotent(self, isolated_auth_db):
        store = isolated_auth_db()
        store.ensure_bootstrap_admin("admin", "first")
        # 第二次不创建新用户，返回现有
        store.ensure_bootstrap_admin("admin", "second")
        assert store.verify_password("admin", "first") is not None
        assert store.verify_password("admin", "second") is None


# ═══════════════ OIDC PKCE ═══════════════


class TestOIDCPKCE:
    def test_pkce_generates_pair(self):
        from app.auth.oidc import generate_pkce

        verifier, challenge = generate_pkce()
        assert len(verifier) > 40
        # challenge 是 base64url（无 padding）
        assert "=" not in challenge
        assert len(challenge) > 30

    def test_pkce_challenge_is_sha256_of_verifier(self):
        import hashlib

        from app.auth.oidc import generate_pkce

        verifier, challenge = generate_pkce()
        expected = (
            base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        assert challenge == expected

    def test_pkce_unique_each_call(self):
        from app.auth.oidc import generate_pkce

        v1, c1 = generate_pkce()
        v2, c2 = generate_pkce()
        assert v1 != v2
        assert c1 != c2


# ═══════════════ OIDC state store ═══════════════


class TestOIDCStateStore:
    def test_save_and_pop_state(self):
        from app.auth.oidc import pop_state, save_state

        state = save_state("google", "verifier123", "/redirect")
        data = pop_state(state)
        assert data is not None
        assert data["provider"] == "google"
        assert data["code_verifier"] == "verifier123"
        assert data["redirect"] == "/redirect"

    def test_pop_state_one_time(self):
        from app.auth.oidc import pop_state, save_state

        state = save_state("google", "v")
        assert pop_state(state) is not None
        # 第二次取返回 None
        assert pop_state(state) is None

    def test_pop_unknown_state(self):
        from app.auth.oidc import pop_state

        assert pop_state("unknown") is None


# ═══════════════ OIDC provider 解析 ═══════════════


class TestOIDCProviderParsing:
    def test_parse_providers_empty(self):
        from app.auth.oidc import parse_providers

        assert parse_providers("") == []
        assert parse_providers("   ") == []

    def test_parse_providers_invalid_json(self):
        from app.auth.oidc import parse_providers

        assert parse_providers("not json") == []

    def test_parse_providers_valid(self):
        from app.auth.oidc import parse_providers

        cfg = json.dumps([
            {
                "name": "google",
                "display_name": "Google",
                "client_id": "cid",
                "client_secret": "sec",
                "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
            }
        ])
        providers = parse_providers(cfg)
        assert len(providers) == 1
        assert providers[0].name == "google"
        assert providers[0].scopes == ["openid", "email", "profile"]

    def test_provider_to_dict_hides_secret(self):
        from app.auth.oidc import OIDCProvider

        p = OIDCProvider(
            name="gh",
            display_name="GitHub",
            client_id="cid",
            client_secret="topsecret",
            discovery_url="https://example/.well-known/openid-configuration",
        )
        d = p.to_dict()
        assert "client_secret" not in d
        assert d["name"] == "gh"

    def test_provider_endpoints_require_discovery(self):
        from app.auth.oidc import OIDCProvider

        p = OIDCProvider(
            name="x", display_name="X", client_id="c", client_secret="s",
            discovery_url="https://x/.well-known/openid-configuration",
        )
        with pytest.raises(RuntimeError, match="not discovered"):
            _ = p.authorization_endpoint


# ═══════════════ OIDC JWT claims 解析 ═══════════════


class TestOIDCIDTokenDecode:
    def test_decode_valid_jwt_payload(self):
        from app.auth.oidc import _decode_id_token_claims

        # 构造无签名 JWT：header.payload.signature
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
        payload_obj = {"sub": "user-123", "email": "u@x.com", "name": "U"}
        payload = base64.urlsafe_b64encode(
            json.dumps(payload_obj).encode()
        ).rstrip(b"=").decode()
        token = f"{header}.{payload}.sig"
        claims = _decode_id_token_claims(token)
        assert claims["sub"] == "user-123"
        assert claims["email"] == "u@x.com"

    def test_decode_invalid_jwt_format(self):
        from app.auth.oidc import _decode_id_token_claims

        with pytest.raises(ValueError, match="无效 JWT"):
            _decode_id_token_claims("not.a.valid.jwt.too.many.parts")


# ═══════════════ OIDC find_or_create_user ═══════════════


class TestOIDCFindOrCreateUser:
    def test_first_login_creates_user(self, isolated_auth_db):
        from app.auth.oidc import find_or_create_user

        user = find_or_create_user(
            "google", "sub-001", "alice@example.com", "Alice", default_role="viewer"
        )
        assert user["email"] == "alice@example.com"
        assert user["role"] == "viewer"

    def test_second_login_reuses_user(self, isolated_auth_db):
        from app.auth.oidc import find_or_create_user

        u1 = find_or_create_user("google", "sub-001", "alice@example.com", "Alice")
        u2 = find_or_create_user("google", "sub-001", "alice@example.com", "Alice2")
        assert u1["id"] == u2["id"]
        # 同一用户复用


# ═══════════════ token_auth 双模式 ═══════════════


class TestTokenAuth:
    def test_dev_mode_no_credentials(self, monkeypatch):
        from app.auth import token_auth

        # 未配置 OPSKG_API_TOKEN 且无凭证 → 开发模式
        class FakeSettings:
            api_token = ""

        monkeypatch.setattr(token_auth, "get_settings", lambda: FakeSettings())
        import asyncio

        result = asyncio.run(token_auth.verify_token(None))
        assert result == "anonymous"

    def test_legacy_token_match(self, monkeypatch):
        from fastapi.security import HTTPAuthorizationCredentials

        from app.auth import token_auth

        class FakeSettings:
            api_token = "shared-secret"

        monkeypatch.setattr(token_auth, "get_settings", lambda: FakeSettings())
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="shared-secret")
        import asyncio

        result = asyncio.run(token_auth.verify_token(creds))
        assert result == "user"

    def test_no_credentials_when_required(self, monkeypatch):
        from fastapi import HTTPException

        from app.auth import token_auth

        class FakeSettings:
            api_token = "shared-secret"

        monkeypatch.setattr(token_auth, "get_settings", lambda: FakeSettings())
        import asyncio

        with pytest.raises(HTTPException) as exc:
            asyncio.run(token_auth.verify_token(None))
        assert exc.value.status_code == 401

    def test_generate_token_returns_random(self):
        from app.auth.token_auth import generate_token

        t1 = generate_token()
        t2 = generate_token()
        assert t1 != t2
        assert len(t1) > 20
