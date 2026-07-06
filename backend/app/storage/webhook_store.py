"""Webhook 订阅与投递记录持久化（Sprint 10+）

两张表：
- webhook_subscriptions: 订阅配置（url / events / secret / active）
- webhook_deliveries:    投递记录（payload / status / attempts / next_retry）

复用 SQLite + WAL 模式，与项目其他存储保持一致。
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

DB_PATH = Path(__file__).parent.parent.parent / "data" / "webhooks.db"


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
        CREATE TABLE IF NOT EXISTS webhook_subscriptions (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            events TEXT NOT NULL DEFAULT '[]',
            secret TEXT NOT NULL,
            description TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_webhook_active ON webhook_subscriptions(active);

        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id TEXT PRIMARY KEY,
            subscription_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            response_code INTEGER,
            response_body TEXT DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            next_retry_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (subscription_id) REFERENCES webhook_subscriptions(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_delivery_sub ON webhook_deliveries(subscription_id);
        CREATE INDEX IF NOT EXISTS idx_delivery_status ON webhook_deliveries(status);
        CREATE INDEX IF NOT EXISTS idx_delivery_retry ON webhook_deliveries(status, next_retry_at);
        """
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str = "wh") -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def _gen_secret() -> str:
    return secrets.token_hex(32)


class WebhookStore:
    """Webhook 订阅与投递记录存储"""

    # ────────── 订阅 CRUD ──────────

    def create_subscription(
        self,
        url: str,
        events: list[str],
        secret: str | None = None,
        description: str = "",
        active: bool = True,
    ) -> dict[str, Any]:
        """创建订阅

        Args:
            url: 回调地址（https:// 或 http://）
            events: 订阅事件类型列表，支持通配符 `*` 或前缀 `incident.*`
            secret: 签名密钥；不传则自动生成
            description: 描述
            active: 是否启用

        Returns:
            订阅记录（含明文 secret，仅在创建/重置时返回）
        """
        if not url.startswith(("http://", "https://")):
            raise ValueError("url 必须以 http:// 或 https:// 开头")
        sub_id = _gen_id("wh")
        secret = secret or _gen_secret()
        now = _now_iso()
        conn = _get_db()
        try:
            conn.execute(
                """INSERT INTO webhook_subscriptions
                   (id, url, events, secret, description, active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sub_id,
                    url,
                    json.dumps(events, ensure_ascii=False),
                    secret,
                    description,
                    1 if active else 0,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info(
            "webhook.subscription.created", sub_id=sub_id, url=url, events=events
        )
        return {
            "id": sub_id,
            "url": url,
            "events": events,
            "secret": secret,
            "description": description,
            "active": active,
            "created_at": now,
            "updated_at": now,
        }

    def list_subscriptions(
        self, active_only: bool = False, event_type: str | None = None
    ) -> list[dict[str, Any]]:
        """列出订阅（不返回 secret）"""
        conn = _get_db()
        try:
            sql = "SELECT * FROM webhook_subscriptions"
            clauses: list[str] = []
            params: list[Any] = []
            if active_only:
                clauses.append("active = 1")
            if event_type:
                clauses.append("(events LIKE ? OR events LIKE ?)")
                params.extend([f'%"{event_type}"%', '"%*"%'])
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at DESC"
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_sub(r, include_secret=False) for r in rows]
        finally:
            conn.close()

    def get_subscription(
        self, sub_id: str, include_secret: bool = False
    ) -> dict[str, Any] | None:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT * FROM webhook_subscriptions WHERE id = ?", (sub_id,)
            ).fetchone()
            return self._row_to_sub(row, include_secret=include_secret) if row else None
        finally:
            conn.close()

    def get_subscription_secret(self, sub_id: str) -> str | None:
        """仅内部 dispatch 使用"""
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT secret FROM webhook_subscriptions WHERE id = ? AND active = 1",
                (sub_id,),
            ).fetchone()
            return row["secret"] if row else None
        finally:
            conn.close()

    def update_subscription(
        self,
        sub_id: str,
        url: str | None = None,
        events: list[str] | None = None,
        description: str | None = None,
        active: bool | None = None,
    ) -> dict[str, Any] | None:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT * FROM webhook_subscriptions WHERE id = ?", (sub_id,)
            ).fetchone()
            if not row:
                return None
            now = _now_iso()
            new_url = url if url is not None else row["url"]
            if new_url and not new_url.startswith(("http://", "https://")):
                raise ValueError("url 必须以 http:// 或 https:// 开头")
            new_events = (
                json.dumps(events, ensure_ascii=False) if events is not None else row["events"]
            )
            new_desc = description if description is not None else row["description"]
            new_active = (1 if active else 0) if active is not None else row["active"]
            conn.execute(
                """UPDATE webhook_subscriptions
                   SET url = ?, events = ?, description = ?, active = ?, updated_at = ?
                   WHERE id = ?""",
                (new_url, new_events, new_desc, new_active, now, sub_id),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_subscription(sub_id)

    def rotate_secret(self, sub_id: str) -> str | None:
        """重置 secret，返回新 secret（仅此一次返回明文）"""
        new_secret = _gen_secret()
        now = _now_iso()
        conn = _get_db()
        try:
            cur = conn.execute(
                "UPDATE webhook_subscriptions SET secret = ?, updated_at = ? WHERE id = ?",
                (new_secret, now, sub_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                return None
        finally:
            conn.close()
        return new_secret

    def delete_subscription(self, sub_id: str) -> bool:
        conn = _get_db()
        try:
            cur = conn.execute(
                "DELETE FROM webhook_subscriptions WHERE id = ?", (sub_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ────────── 投递记录 ──────────

    def create_delivery(
        self,
        subscription_id: str,
        event_type: str,
        payload: dict[str, Any],
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        deliv_id = _gen_id("dlv")
        now = _now_iso()
        conn = _get_db()
        try:
            conn.execute(
                """INSERT INTO webhook_deliveries
                   (id, subscription_id, event_type, payload, status, attempts,
                    max_attempts, next_retry_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)""",
                (
                    deliv_id,
                    subscription_id,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    max_attempts,
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "id": deliv_id,
            "subscription_id": subscription_id,
            "event_type": event_type,
            "payload": payload,
            "status": "pending",
            "attempts": 0,
            "max_attempts": max_attempts,
            "next_retry_at": now,
            "created_at": now,
            "updated_at": now,
        }

    def update_delivery(
        self,
        deliv_id: str,
        status: str,
        response_code: int | None = None,
        response_body: str = "",
        attempts: int | None = None,
        next_retry_at: str | None = None,
    ) -> None:
        now = _now_iso()
        conn = _get_db()
        try:
            conn.execute(
                """UPDATE webhook_deliveries
                   SET status = ?, response_code = ?, response_body = ?,
                       attempts = COALESCE(?, attempts),
                       next_retry_at = COALESCE(?, next_retry_at),
                       updated_at = ?
                   WHERE id = ?""",
                (
                    status,
                    response_code,
                    response_body[:4000] if response_body else "",
                    attempts,
                    next_retry_at,
                    now,
                    deliv_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def list_deliveries(
        self,
        subscription_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conn = _get_db()
        try:
            clauses: list[str] = []
            params: list[Any] = []
            if subscription_id:
                clauses.append("subscription_id = ?")
                params.append(subscription_id)
            if status:
                clauses.append("status = ?")
                params.append(status)
            sql = "SELECT * FROM webhook_deliveries"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_delivery(r) for r in rows]
        finally:
            conn.close()

    def get_delivery(self, deliv_id: str) -> dict[str, Any] | None:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT * FROM webhook_deliveries WHERE id = ?", (deliv_id,)
            ).fetchone()
            return self._row_to_delivery(row) if row else None
        finally:
            conn.close()

    def list_pending_retries(self, before_iso: str, limit: int = 100) -> list[dict[str, Any]]:
        """列出待重试（status=retry 且 next_retry_at <= before_iso）的投递"""
        conn = _get_db()
        try:
            rows = conn.execute(
                """SELECT * FROM webhook_deliveries
                   WHERE status = 'retry' AND next_retry_at <= ?
                   ORDER BY next_retry_at ASC LIMIT ?""",
                (before_iso, limit),
            ).fetchall()
            return [self._row_to_delivery(r) for r in rows]
        finally:
            conn.close()

    # ────────── 工具方法 ──────────

    @staticmethod
    def _row_to_sub(row: sqlite3.Row, include_secret: bool = False) -> dict[str, Any]:
        try:
            events = json.loads(row["events"]) if row["events"] else []
        except (json.JSONDecodeError, TypeError):
            events = []
        d = {
            "id": row["id"],
            "url": row["url"],
            "events": events,
            "description": row["description"] or "",
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if include_secret:
            d["secret"] = row["secret"]
        return d

    @staticmethod
    def _row_to_delivery(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload"]) if row["payload"] else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        return {
            "id": row["id"],
            "subscription_id": row["subscription_id"],
            "event_type": row["event_type"],
            "payload": payload,
            "status": row["status"],
            "response_code": row["response_code"],
            "response_body": row["response_body"] or "",
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
            "next_retry_at": row["next_retry_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


# ────────── 单例 ──────────

_store: WebhookStore | None = None


def get_webhook_store() -> WebhookStore:
    global _store
    if _store is None:
        _store = WebhookStore()
    return _store
