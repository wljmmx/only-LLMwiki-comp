"""用户与角色模型（P3-1 SSO 基础）

3 角色模型：
- admin：全部权限（用户管理 + 全部业务操作）
- operator：业务操作权限（上传/编译/审批/生成等写操作）
- viewer：只读权限（浏览/搜索/查询）

存储：SQLite + WAL，DB_PATH = data/auth.db
向后兼容：与现有 token_auth.py 共存
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "auth.db"

# 角色枚举（按权限递减）
ROLES = ("admin", "operator", "viewer")
ROLE_HIERARCHY: dict[str, int] = {"admin": 3, "operator": 2, "viewer": 1}


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            display_name TEXT,
            email TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
        """
    )


def _hash_password(password: str, salt: str | None = None) -> str:
    """SHA256(salt + password)，salt 内嵌在返回值中：salt$hash"""
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}${h}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$", 1)
        return secrets.compare_digest(
            hashlib.sha256(f"{salt}{password}".encode()).hexdigest(), h
        )
    except Exception:  # noqa: BLE001
        return False


def _hash_token(token: str) -> str:
    """会话 token 哈希存储（不明文存 token）"""
    return hashlib.sha256(token.encode()).hexdigest()


def _row_to_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "display_name": row["display_name"],
        "email": row["email"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class AuthStore:
    """用户与会话存储"""

    def create_user(
        self,
        username: str,
        password: str,
        role: str = "viewer",
        display_name: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"无效角色: {role}，可选: {ROLES}")
        now = datetime.now(timezone.utc).isoformat()
        conn = _get_db()
        try:
            conn.execute(
                """INSERT INTO users
                   (username, password_hash, role, display_name, email, active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    username,
                    _hash_password(password),
                    role,
                    display_name or username,
                    email,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            logger.info("auth.user_created", username=username, role=role)
            return _row_to_user(row)
        except sqlite3.IntegrityError as e:
            raise ValueError(f"用户已存在: {username}") from e
        finally:
            conn.close()

    def get_user(self, username: str) -> dict[str, Any] | None:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            return _row_to_user(row) if row else None
        finally:
            conn.close()

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return _row_to_user(row) if row else None
        finally:
            conn.close()

    def list_users(self) -> list[dict[str, Any]]:
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY id"
            ).fetchall()
            return [_row_to_user(r) for r in rows]
        finally:
            conn.close()

    def update_user(
        self,
        user_id: int,
        *,
        role: str | None = None,
        display_name: str | None = None,
        email: str | None = None,
        active: bool | None = None,
        password: str | None = None,
    ) -> dict[str, Any] | None:
        if role is not None and role not in ROLES:
            raise ValueError(f"无效角色: {role}")
        now = datetime.now(timezone.utc).isoformat()
        conn = _get_db()
        try:
            sets: list[str] = []
            params: list[Any] = []
            if role is not None:
                sets.append("role = ?")
                params.append(role)
            if display_name is not None:
                sets.append("display_name = ?")
                params.append(display_name)
            if email is not None:
                sets.append("email = ?")
                params.append(email)
            if active is not None:
                sets.append("active = ?")
                params.append(1 if active else 0)
            if password is not None:
                sets.append("password_hash = ?")
                params.append(_hash_password(password))
            if not sets:
                return self.get_user_by_id(user_id)
            sets.append("updated_at = ?")
            params.append(now)
            params.append(user_id)
            conn.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params
            )
            conn.commit()
            return self.get_user_by_id(user_id)
        finally:
            conn.close()

    def delete_user(self, user_id: int) -> bool:
        conn = _get_db()
        try:
            cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def verify_password(self, username: str, password: str) -> dict[str, Any] | None:
        """验证用户名密码，返回用户（不含密码）或 None"""
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? AND active = 1",
                (username,),
            ).fetchone()
            if not row:
                return None
            if not _verify_password(password, row["password_hash"]):
                return None
            return _row_to_user(row)
        finally:
            conn.close()

    def create_session(self, user_id: int, ttl_seconds: int = 86400) -> str:
        """创建会话，返回明文 token（仅此一次）"""
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = datetime.fromtimestamp(now.timestamp() + ttl_seconds, tz=timezone.utc)
        conn = _get_db()
        try:
            conn.execute(
                """INSERT INTO sessions (token_hash, user_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (_hash_token(token), user_id, now.isoformat(), expires.isoformat()),
            )
            conn.commit()
            logger.info("auth.session_created", user_id=user_id)
            return token
        finally:
            conn.close()

    def verify_session(self, token: str) -> dict[str, Any] | None:
        """验证会话 token，返回用户或 None（过期/不存在）"""
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE token_hash = ?",
                (_hash_token(token),),
            ).fetchone()
            if not row:
                return None
            # 检查过期
            expires = datetime.fromisoformat(row["expires_at"])
            if datetime.now(timezone.utc) > expires:
                conn.execute(
                    "DELETE FROM sessions WHERE token_hash = ?",
                    (_hash_token(token),),
                )
                conn.commit()
                return None
            user = self.get_user_by_id(row["user_id"])
            if not user or not user["active"]:
                return None
            return user
        finally:
            conn.close()

    def revoke_session(self, token: str) -> bool:
        conn = _get_db()
        try:
            cur = conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (_hash_token(token),),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def cleanup_expired_sessions(self) -> int:
        conn = _get_db()
        try:
            now = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                "DELETE FROM sessions WHERE expires_at < ?", (now,)
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def ensure_bootstrap_admin(self, username: str = "admin", password: str = "admin") -> dict[str, Any]:
        """确保存在至少一个 admin 用户（首次启动引导）"""
        existing = self.get_user(username)
        if existing:
            return existing
        return self.create_user(
            username=username,
            password=password,
            role="admin",
            display_name="Administrator",
        )


_store: AuthStore | None = None


def get_auth_store() -> AuthStore:
    global _store
    if _store is None:
        _store = AuthStore()
    return _store


def has_role(user_role: str, required_role: str) -> bool:
    """检查 user_role 是否 >= required_role（层级继承）"""
    return ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get(required_role, 0)
