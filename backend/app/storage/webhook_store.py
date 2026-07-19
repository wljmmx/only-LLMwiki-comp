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

from app.storage.connection import ConnectionPool

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "webhooks.db"


def _get_db() -> sqlite3.Connection:
    return ConnectionPool.get(str(DB_PATH), _init_schema).get_connection()


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

        -- S15-2: 告警路由规则表（severity / payload_matchers / target_subscription_ids）
        CREATE TABLE IF NOT EXISTS alert_rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            event_type_pattern TEXT NOT NULL,
            severity TEXT DEFAULT '',
            payload_matchers TEXT DEFAULT '[]',
            target_subscription_ids TEXT DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            priority INTEGER NOT NULL DEFAULT 100,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled ON alert_rules(enabled);
        CREATE INDEX IF NOT EXISTS idx_alert_rules_priority ON alert_rules(priority);

        -- S15-2: 静默窗口表（维护期间不投递）
        CREATE TABLE IF NOT EXISTS silence_windows (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            event_type_pattern TEXT NOT NULL,
            reason TEXT DEFAULT '',
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            payload_matchers TEXT DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_silence_enabled ON silence_windows(enabled);
        CREATE INDEX IF NOT EXISTS idx_silence_time ON silence_windows(start_time, end_time);
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

    def get_subscription(
        self, sub_id: str, include_secret: bool = False
    ) -> dict[str, Any] | None:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM webhook_subscriptions WHERE id = ?", (sub_id,)
        ).fetchone()
        return self._row_to_sub(row, include_secret=include_secret) if row else None

    def get_subscription_secret(self, sub_id: str) -> str | None:
        """仅内部 dispatch 使用"""
        conn = _get_db()
        row = conn.execute(
            "SELECT secret FROM webhook_subscriptions WHERE id = ? AND active = 1",
            (sub_id,),
        ).fetchone()
        return row["secret"] if row else None

    def update_subscription(
        self,
        sub_id: str,
        url: str | None = None,
        events: list[str] | None = None,
        description: str | None = None,
        active: bool | None = None,
    ) -> dict[str, Any] | None:
        conn = _get_db()
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
        return self.get_subscription(sub_id)

    def rotate_secret(self, sub_id: str) -> str | None:
        """重置 secret，返回新 secret（仅此一次返回明文）"""
        new_secret = _gen_secret()
        now = _now_iso()
        conn = _get_db()
        cur = conn.execute(
            "UPDATE webhook_subscriptions SET secret = ?, updated_at = ? WHERE id = ?",
            (new_secret, now, sub_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        return new_secret

    def delete_subscription(self, sub_id: str) -> bool:
        conn = _get_db()
        cur = conn.execute(
            "DELETE FROM webhook_subscriptions WHERE id = ?", (sub_id,)
        )
        conn.commit()
        return cur.rowcount > 0

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

    def list_deliveries(
        self,
        subscription_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conn = _get_db()
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

    def get_delivery(self, deliv_id: str) -> dict[str, Any] | None:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM webhook_deliveries WHERE id = ?", (deliv_id,)
        ).fetchone()
        return self._row_to_delivery(row) if row else None

    def list_pending_retries(self, before_iso: str, limit: int = 100) -> list[dict[str, Any]]:
        """列出待重试（status=retry 且 next_retry_at <= before_iso）的投递"""
        conn = _get_db()
        rows = conn.execute(
            """SELECT * FROM webhook_deliveries
               WHERE status = 'retry' AND next_retry_at <= ?
               ORDER BY next_retry_at ASC LIMIT ?""",
            (before_iso, limit),
        ).fetchall()
        return [self._row_to_delivery(r) for r in rows]

    # ────────── 告警路由规则 CRUD（S15-2） ──────────

    def create_alert_rule(
        self,
        name: str,
        event_type_pattern: str,
        description: str = "",
        severity: str = "",
        payload_matchers: list[dict] | None = None,
        target_subscription_ids: list[str] | None = None,
        enabled: bool = True,
        priority: int = 100,
    ) -> dict[str, Any]:
        """创建告警路由规则

        Args:
            name: 规则名称
            event_type_pattern: 事件匹配模式（精确 / `incident.*` / `*`）
            description: 描述
            severity: 严重等级过滤（critical/warning/info/空=不限）
            payload_matchers: payload 匹配条件列表 [{"field","op","value"}]
            target_subscription_ids: 目标订阅 ID 列表（空=匹配所有订阅）
            enabled: 是否启用
            priority: 优先级（数字越小越高）

        Returns:
            规则记录
        """
        if not name or not event_type_pattern:
            raise ValueError("name 和 event_type_pattern 必填")
        rule_id = _gen_id("rule")
        now = _now_iso()
        conn = _get_db()
        conn.execute(
            """INSERT INTO alert_rules
               (id, name, description, event_type_pattern, severity,
                payload_matchers, target_subscription_ids, enabled, priority,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rule_id,
                name,
                description,
                event_type_pattern,
                severity,
                json.dumps(payload_matchers or [], ensure_ascii=False),
                json.dumps(target_subscription_ids or [], ensure_ascii=False),
                1 if enabled else 0,
                int(priority),
                now,
                now,
            ),
        )
        conn.commit()
        logger.info(
            "alert_rule.created", rule_id=rule_id, name=name, pattern=event_type_pattern
        )
        return {
            "id": rule_id,
            "name": name,
            "description": description,
            "event_type_pattern": event_type_pattern,
            "severity": severity,
            "payload_matchers": payload_matchers or [],
            "target_subscription_ids": target_subscription_ids or [],
            "enabled": enabled,
            "priority": int(priority),
            "created_at": now,
            "updated_at": now,
        }

    def list_alert_rules(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        """列出告警路由规则（默认按 priority 升序）"""
        conn = _get_db()
        sql = "SELECT * FROM alert_rules"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY priority ASC, created_at DESC"
        rows = conn.execute(sql).fetchall()
        return [self._row_to_rule(r) for r in rows]

    def get_alert_rule(self, rule_id: str) -> dict[str, Any] | None:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM alert_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        return self._row_to_rule(row) if row else None

    def update_alert_rule(
        self,
        rule_id: str,
        name: str | None = None,
        description: str | None = None,
        event_type_pattern: str | None = None,
        severity: str | None = None,
        payload_matchers: list[dict] | None = None,
        target_subscription_ids: list[str] | None = None,
        enabled: bool | None = None,
        priority: int | None = None,
    ) -> dict[str, Any] | None:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM alert_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        if not row:
            return None
        now = _now_iso()
        new_name = name if name is not None else row["name"]
        new_desc = description if description is not None else row["description"]
        new_pattern = (
            event_type_pattern
            if event_type_pattern is not None
            else row["event_type_pattern"]
        )
        if not new_name or not new_pattern:
            raise ValueError("name 和 event_type_pattern 不能为空")
        new_severity = severity if severity is not None else row["severity"]
        new_matchers = (
            json.dumps(payload_matchers, ensure_ascii=False)
            if payload_matchers is not None
            else row["payload_matchers"]
        )
        new_targets = (
            json.dumps(target_subscription_ids, ensure_ascii=False)
            if target_subscription_ids is not None
            else row["target_subscription_ids"]
        )
        new_enabled = (
            (1 if enabled else 0) if enabled is not None else row["enabled"]
        )
        new_priority = (
            int(priority) if priority is not None else row["priority"]
        )
        conn.execute(
            """UPDATE alert_rules
               SET name = ?, description = ?, event_type_pattern = ?,
                   severity = ?, payload_matchers = ?, target_subscription_ids = ?,
                   enabled = ?, priority = ?, updated_at = ?
               WHERE id = ?""",
            (
                new_name,
                new_desc,
                new_pattern,
                new_severity,
                new_matchers,
                new_targets,
                new_enabled,
                new_priority,
                now,
                rule_id,
            ),
        )
        conn.commit()
        return self.get_alert_rule(rule_id)

    def delete_alert_rule(self, rule_id: str) -> bool:
        conn = _get_db()
        cur = conn.execute(
            "DELETE FROM alert_rules WHERE id = ?", (rule_id,)
        )
        conn.commit()
        return cur.rowcount > 0

    # ────────── 静默窗口 CRUD（S15-2） ──────────

    def create_silence_window(
        self,
        name: str,
        event_type_pattern: str,
        start_time: str,
        end_time: str,
        reason: str = "",
        payload_matchers: list[dict] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """创建静默窗口

        Args:
            name: 窗口名称
            event_type_pattern: 事件匹配模式
            start_time: 起始时间（ISO8601 UTC）
            end_time: 结束时间（ISO8601 UTC）
            reason: 静默原因
            payload_matchers: payload 匹配条件（同 alert_rules）
            enabled: 是否启用

        Returns:
            静默窗口记录
        """
        if not name or not event_type_pattern:
            raise ValueError("name 和 event_type_pattern 必填")
        if not start_time or not end_time:
            raise ValueError("start_time 和 end_time 必填")
        # 校验时间格式合法（容错：解析失败抛错）
        datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        win_id = _gen_id("silence")
        now = _now_iso()
        conn = _get_db()
        conn.execute(
            """INSERT INTO silence_windows
               (id, name, event_type_pattern, reason, start_time, end_time,
                payload_matchers, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                win_id,
                name,
                event_type_pattern,
                reason,
                start_time,
                end_time,
                json.dumps(payload_matchers or [], ensure_ascii=False),
                1 if enabled else 0,
                now,
                now,
            ),
        )
        conn.commit()
        logger.info(
            "silence_window.created",
            win_id=win_id,
            name=name,
            start=start_time,
            end=end_time,
        )
        return {
            "id": win_id,
            "name": name,
            "event_type_pattern": event_type_pattern,
            "reason": reason,
            "start_time": start_time,
            "end_time": end_time,
            "payload_matchers": payload_matchers or [],
            "enabled": enabled,
            "created_at": now,
            "updated_at": now,
        }

    def list_silence_windows(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        """列出静默窗口（按 start_time 升序）"""
        conn = _get_db()
        sql = "SELECT * FROM silence_windows"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY start_time ASC"
        rows = conn.execute(sql).fetchall()
        return [self._row_to_silence(r) for r in rows]

    def get_silence_window(self, win_id: str) -> dict[str, Any] | None:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM silence_windows WHERE id = ?", (win_id,)
        ).fetchone()
        return self._row_to_silence(row) if row else None

    def update_silence_window(
        self,
        win_id: str,
        name: str | None = None,
        event_type_pattern: str | None = None,
        reason: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        payload_matchers: list[dict] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM silence_windows WHERE id = ?", (win_id,)
        ).fetchone()
        if not row:
            return None
        now = _now_iso()
        new_name = name if name is not None else row["name"]
        new_pattern = (
            event_type_pattern
            if event_type_pattern is not None
            else row["event_type_pattern"]
        )
        if not new_name or not new_pattern:
            raise ValueError("name 和 event_type_pattern 不能为空")
        new_reason = reason if reason is not None else row["reason"]
        new_start = start_time if start_time is not None else row["start_time"]
        new_end = end_time if end_time is not None else row["end_time"]
        if start_time is not None:
            datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if end_time is not None:
            datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        new_matchers = (
            json.dumps(payload_matchers, ensure_ascii=False)
            if payload_matchers is not None
            else row["payload_matchers"]
        )
        new_enabled = (
            (1 if enabled else 0) if enabled is not None else row["enabled"]
        )
        conn.execute(
            """UPDATE silence_windows
               SET name = ?, event_type_pattern = ?, reason = ?, start_time = ?,
                   end_time = ?, payload_matchers = ?, enabled = ?, updated_at = ?
               WHERE id = ?""",
            (
                new_name,
                new_pattern,
                new_reason,
                new_start,
                new_end,
                new_matchers,
                new_enabled,
                now,
                win_id,
            ),
        )
        conn.commit()
        return self.get_silence_window(win_id)

    def delete_silence_window(self, win_id: str) -> bool:
        conn = _get_db()
        cur = conn.execute(
            "DELETE FROM silence_windows WHERE id = ?", (win_id,)
        )
        conn.commit()
        return cur.rowcount > 0

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

    @staticmethod
    def _row_to_rule(row: sqlite3.Row) -> dict[str, Any]:
        """alert_rules 行 → dict（payload_matchers / target_subscription_ids 反序列化）"""
        try:
            matchers = json.loads(row["payload_matchers"]) if row["payload_matchers"] else []
        except (json.JSONDecodeError, TypeError):
            matchers = []
        try:
            targets = (
                json.loads(row["target_subscription_ids"])
                if row["target_subscription_ids"]
                else []
            )
        except (json.JSONDecodeError, TypeError):
            targets = []
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"] or "",
            "event_type_pattern": row["event_type_pattern"],
            "severity": row["severity"] or "",
            "payload_matchers": matchers,
            "target_subscription_ids": targets,
            "enabled": bool(row["enabled"]),
            "priority": int(row["priority"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_silence(row: sqlite3.Row) -> dict[str, Any]:
        """silence_windows 行 → dict"""
        try:
            matchers = json.loads(row["payload_matchers"]) if row["payload_matchers"] else []
        except (json.JSONDecodeError, TypeError):
            matchers = []
        return {
            "id": row["id"],
            "name": row["name"],
            "event_type_pattern": row["event_type_pattern"],
            "reason": row["reason"] or "",
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "payload_matchers": matchers,
            "enabled": bool(row["enabled"]),
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
