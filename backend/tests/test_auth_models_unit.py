"""auth/models.py 纯函数单元测试（不需要 DB）

覆盖：
- 密码哈希：bcrypt、legacy SHA256、is_legacy_hash、空输入边界
- Token 哈希：_hash_token 一致性
- 角色权限：ROLES 定义、has_role 层级
"""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.auth.models import (
    _BCRYPT_PREFIX,
    _SHA256_LEGACY_PREFIX,
    ROLES,
    _hash_password,
    _hash_password_bcrypt,
    _hash_token,
    _is_legacy_hash,
    _verify_password,
    has_role,
)

# ═══════════════ 密码哈希 ═══════════════


class TestPasswordHashing:
    def test_bcrypt_hash_and_verify(self):
        """Hash a password with _hash_password_bcrypt and verify with _verify_password"""
        h = _hash_password_bcrypt("secret")
        assert h.startswith(_BCRYPT_PREFIX)
        assert _verify_password("secret", h) is True
        assert _verify_password("wrong", h) is False

    def test_legacy_sha256_verify(self):
        """Test _verify_password with a legacy SHA256 hash (salt$hash format)"""
        salt = "legacysalt"
        password = "oldpassword"
        h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        legacy = f"{salt}${h}"
        assert _verify_password(password, legacy) is True
        assert _verify_password("wrong", legacy) is False

    def test_legacy_sha256_verify_with_prefix(self):
        """Test _verify_password with sha256$ prefix format"""
        salt = "prefixed"
        password = "mypass"
        h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        legacy = f"{_SHA256_LEGACY_PREFIX}{salt}${h}"
        assert _verify_password(password, legacy) is True
        assert _verify_password("bad", legacy) is False

    def test_is_legacy_hash_bcrypt(self):
        """bcrypt hash should NOT be detected as legacy"""
        h = _hash_password_bcrypt("test")
        assert _is_legacy_hash(h) is False

    def test_is_legacy_hash_sha256(self):
        """SHA256 hash should be detected as legacy"""
        assert _is_legacy_hash("salt$abc123") is True
        assert _is_legacy_hash(f"{_SHA256_LEGACY_PREFIX}salt$abc") is True

    def test_verify_password_empty_input(self):
        """Empty password or hash should return False (not crash)"""
        assert _verify_password("", "") is False
        assert _verify_password("password", "") is False
        assert _verify_password("", "somehash") is False

    def test_hash_password_defaults_to_bcrypt(self):
        """_hash_password should use bcrypt by default"""
        h = _hash_password("defaulttest")
        assert h.startswith(_BCRYPT_PREFIX)
        assert len(h) == 60

    def test_verify_password_corrupt_split(self):
        """Hash with no $ separator should return False"""
        assert _verify_password("x", "no_dollar_sign") is False

    def test_verify_password_corrupt_sha256(self):
        """Malformed SHA256 legacy hash should return False"""
        assert _verify_password("x", "salt$not_a_valid_hex_hash") is False


# ═══════════════ Token 哈希 ═══════════════


class TestTokenHashing:
    def test_hash_token(self):
        """_hash_token produces consistent SHA256 output"""
        h1 = _hash_token("my-secret-token")
        h2 = _hash_token("my-secret-token")
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex is 64 chars
        assert all(c in "0123456789abcdef" for c in h1)

    def test_hash_token_different_inputs(self):
        """Different inputs produce different hashes"""
        h1 = _hash_token("token-a")
        h2 = _hash_token("token-b")
        assert h1 != h2


# ═══════════════ 角色权限 ═══════════════


class TestRolePermissions:
    def test_roles_defined(self):
        """ROLES tuple should contain admin, operator, viewer"""
        assert "admin" in ROLES
        assert "operator" in ROLES
        assert "viewer" in ROLES
        assert len(ROLES) == 3

    def test_admin_can_do_everything(self):
        """admin can do any role-level operations"""
        assert has_role("admin", "admin") is True
        assert has_role("admin", "operator") is True
        assert has_role("admin", "viewer") is True

    def test_operator_can_do_operator_and_viewer(self):
        """operator can do operator and viewer operations"""
        assert has_role("operator", "operator") is True
        assert has_role("operator", "viewer") is True
        assert has_role("operator", "admin") is False

    def test_viewer_is_read_only(self):
        """viewer can only do viewer operations"""
        assert has_role("viewer", "viewer") is True
        assert has_role("viewer", "operator") is False
        assert has_role("viewer", "admin") is False

    def test_unknown_role_fails(self):
        """Unknown user role fails checks; unknown required role defaults to 0 (no barrier)"""
        # unknown user_role → ROLE_HIERARCHY.get("unknown", 0) = 0, always fails against known roles
        assert has_role("unknown", "viewer") is False
        # unknown required_role → ROLE_HIERARCHY.get("unknown", 0) = 0, any user passes
        assert has_role("viewer", "unknown") is True
        # both unknown → both default to 0, 0 >= 0 is True
        assert has_role("unknown", "unknown") is True
