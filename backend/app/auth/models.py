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

# P0-1: 密码哈希算法标识前缀
# bcrypt: "$2b$" 开头（bcrypt 自带 salt）
# 旧 SHA256: "sha256$" 前缀（salt$hash 格式，向后兼容）
_BCRYPT_PREFIX = "$2b$"
_SHA256_LEGACY_PREFIX = "sha256$"


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    _migrate_schema(conn)
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


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """增量 schema 迁移（P0-1/P0-5/P0-9 安全加固）"""
    # P0-9: password_changed_at + must_change_password 字段
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "password_changed_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_changed_at TEXT")
    if "must_change_password" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
        )
    # P0-5: failed_login_count + locked_until 字段（账户锁定）
    if "failed_login_count" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0"
        )
    if "locked_until" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN locked_until TEXT")
    conn.commit()


def _hash_password_bcrypt(password: str) -> str:
    """使用 bcrypt KDF 哈希密码（P0-1 安全加固）"""
    import bcrypt

    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode(
        "utf-8"
    )


def _hash_password(password: str, salt: str | None = None) -> str:
    """哈希密码（P0-1: 默认 bcrypt，保留旧 SHA256 签名仅供向后兼容测试调用）"""
    return _hash_password_bcrypt(password)


def _verify_password(password: str, stored: str) -> bool:
    """验证密码（P0-1: 支持 bcrypt + 向后兼容 SHA256）

    - bcrypt hash（$2b$ 开头）→ bcrypt 验证
    - 旧 SHA256 hash（sha256$ 前缀或 salt$hash 格式）→ SHA256 验证（向后兼容）
    """
    try:
        if stored.startswith(_BCRYPT_PREFIX):
            import bcrypt

            return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
        # 向后兼容：旧 SHA256 格式
        if stored.startswith(_SHA256_LEGACY_PREFIX):
            stored = stored[len(_SHA256_LEGACY_PREFIX):]
        salt, h = stored.split("$", 1)
        return secrets.compare_digest(
            hashlib.sha256(f"{salt}{password}".encode()).hexdigest(), h
        )
    except Exception:  # noqa: BLE001
        return False


def _is_legacy_hash(stored: str) -> bool:
    """判断是否为旧 SHA256 哈希（需要迁移）"""
    return not stored.startswith(_BCRYPT_PREFIX)


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
        "password_changed_at": row["password_changed_at"] if "password_changed_at" in row.keys() else None,
        "must_change_password": bool(row["must_change_password"]) if "must_change_password" in row.keys() else False,
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
        must_change_password: bool = False,
    ) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"无效角色: {role}，可选: {ROLES}")
        now = datetime.now(timezone.utc).isoformat()
        conn = _get_db()
        try:
            conn.execute(
                """INSERT INTO users
                   (username, password_hash, role, display_name, email, active,
                    created_at, updated_at, password_changed_at, must_change_password,
                    failed_login_count, locked_until)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 0, NULL)""",
                (
                    username,
                    _hash_password(password),
                    role,
                    display_name or username,
                    email,
                    now,
                    now,
                    now,
                    1 if must_change_password else 0,
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
                # P0-9: 密码变更时记录时间并清除强制改密标志
                sets.append("password_changed_at = ?")
                params.append(now)
                sets.append("must_change_password = 0")
                # P0-5: 密码变更时重置锁定状态
                sets.append("failed_login_count = 0")
                sets.append("locked_until = NULL")
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
        """验证用户名密码，返回用户（不含密码）或 None

        P0-1: 登录时自动将旧 SHA256 哈希迁移到 bcrypt
        P0-5: 失败计数 + 账户锁定（5 次失败锁定 15 分钟）
        """
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? AND active = 1",
                (username,),
            ).fetchone()
            if not row:
                return None

            # P0-5: 检查账户锁定
            locked_until = row["locked_until"] if "locked_until" in row.keys() else None
            if locked_until:
                lock_time = datetime.fromisoformat(locked_until)
                if datetime.now(timezone.utc) < lock_time:
                    logger.warning(
                        "auth.account_locked",
                        username=username,
                        locked_until=locked_until,
                    )
                    return None
                # 锁定过期，重置计数
                conn.execute(
                    "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = ?",
                    (row["id"],),
                )
                conn.commit()

            if not _verify_password(password, row["password_hash"]):
                # P0-5: 记录失败次数
                self._record_failed_login(conn, row["id"])
                return None

            # P0-1: 自动迁移旧 SHA256 哈希到 bcrypt
            if _is_legacy_hash(row["password_hash"]):
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE users SET password_hash = ?, password_changed_at = ?, failed_login_count = 0, locked_until = NULL WHERE id = ?",
                    (_hash_password_bcrypt(password), now, row["id"]),
                )
                conn.commit()
                logger.info("auth.password_hash_migrated", username=username)
            else:
                # 登录成功，重置失败计数
                conn.execute(
                    "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = ?",
                    (row["id"],),
                )
                conn.commit()

            return _row_to_user(row)
        finally:
            conn.close()

    def _record_failed_login(self, conn: sqlite3.Connection, user_id: int) -> None:
        """P0-5: 记录登录失败，达到阈值则锁定账户"""
        MAX_FAILURES = 5
        LOCK_DURATION_MINUTES = 15

        row = conn.execute(
            "SELECT failed_login_count FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        count = (row["failed_login_count"] if row else 0) + 1
        now = datetime.now(timezone.utc)

        if count >= MAX_FAILURES:
            locked_until = datetime.fromtimestamp(
                now.timestamp() + LOCK_DURATION_MINUTES * 60, tz=timezone.utc
            ).isoformat()
            conn.execute(
                "UPDATE users SET failed_login_count = ?, locked_until = ? WHERE id = ?",
                (count, locked_until, user_id),
            )
            logger.warning(
                "auth.account_locked_on_failures",
                user_id=user_id,
                failures=count,
                locked_until=locked_until,
            )
        else:
            conn.execute(
                "UPDATE users SET failed_login_count = ? WHERE id = ?",
                (count, user_id),
            )
            logger.info(
                "auth.login_failed",
                user_id=user_id,
                failures=count,
                threshold=MAX_FAILURES,
            )
        conn.commit()

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
        """确保存在至少一个 admin 用户（首次启动引导）

        P0-9: bootstrap admin 标记为 must_change_password=True，首次登录后强制改密
        """
        existing = self.get_user(username)
        if existing:
            return existing
        return self.create_user(
            username=username,
            password=password,
            role="admin",
            display_name="Administrator",
            must_change_password=True,
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
