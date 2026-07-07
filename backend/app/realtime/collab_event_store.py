"""协作事件持久化存储（S16-6）

将协作房间内的关键事件（用户加入/离开、编辑锁获取/释放/拒绝）持久化到 SQLite，
支持历史回放与时间线查询。

表结构：
    collab_events
        id          INTEGER PRIMARY KEY AUTOINCREMENT
        slug        TEXT NOT NULL          -- wiki 页面 slug
        timestamp   REAL NOT NULL          -- 事件发生的秒级时间戳（与广播注入一致）
        event_type  TEXT NOT NULL          -- user_joined | user_left | lock_acquired | lock_released | lock_denied
        user_id     TEXT NOT NULL          -- 触发事件的用户 ID
        display_name TEXT                   -- 用户显示名（可能为空）
        message     TEXT NOT NULL          -- 事件描述（人类可读）
        created_at  TEXT NOT NULL          -- 入库时间（ISO8601）

索引：
    idx_collab_events_slug_ts  (slug, timestamp DESC)  -- 按时间倒序查询
    idx_collab_events_slug_id  (slug, id DESC)          -- 按 id 游标分页

设计要点：
1. 与 collab_hub 解耦：collab_hub 在 broadcast/_send_to 时调用 append_event，
   持久化失败不影响广播流程（try/except 守护）
2. 仅持久化 5 类"事件型"消息（user_joined/user_left/lock_acquired/lock_released/
   lock_denied），不持久化 presence/heartbeat/edit_event/cursor 等高频或瞬时消息
3. 分页采用 id 游标（before_id），比 OFFSET 高效且稳定
4. WAL 模式，与其它 SQLite 库一致
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "collab_events.db"

# 需持久化的事件类型白名单（5 类）
PERSISTED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "user_joined",
        "user_left",
        "lock_acquired",
        "lock_released",
        "lock_denied",
    }
)

# 单次查询默认上限
DEFAULT_LIMIT = 100
# 单次查询绝对上限
MAX_LIMIT = 500


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
        CREATE TABLE IF NOT EXISTS collab_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL,
            timestamp REAL NOT NULL,
            event_type TEXT NOT NULL,
            user_id TEXT NOT NULL,
            display_name TEXT,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_collab_events_slug_ts
            ON collab_events(slug, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_collab_events_slug_id
            ON collab_events(slug, id DESC);
        """
    )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "slug": row["slug"],
        "timestamp": row["timestamp"],
        "event_type": row["event_type"],
        "user_id": row["user_id"],
        "display_name": row["display_name"] or "",
        "message": row["message"],
        "created_at": row["created_at"],
    }


class CollabEventStore:
    """协作事件持久化存储"""

    def append_event(
        self,
        slug: str,
        timestamp: float,
        event_type: str,
        user_id: str,
        display_name: str,
        message: str,
    ) -> int | None:
        """追加一条事件。返回新行 id，失败返回 None。

        Args:
            slug: wiki 页面 slug
            timestamp: 事件发生的秒级时间戳
            event_type: 事件类型（必须在 PERSISTED_EVENT_TYPES 中）
            user_id: 触发事件的用户 ID
            display_name: 用户显示名
            message: 事件描述
        """
        if event_type not in PERSISTED_EVENT_TYPES:
            return None
        try:
            conn = _get_db()
            now = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                """
                INSERT INTO collab_events
                    (slug, timestamp, event_type, user_id, display_name, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug,
                    float(timestamp),
                    event_type,
                    str(user_id),
                    display_name or "",
                    message or "",
                    now,
                ),
            )
            conn.commit()
            row_id = cur.lastrowid
            conn.close()
            return row_id
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "collab.event_store.append_failed",
                slug=slug,
                event_type=event_type,
                error=str(e),
            )
            return None

    def list_events(
        self,
        slug: str,
        limit: int = DEFAULT_LIMIT,
        before_id: int | None = None,
    ) -> dict[str, Any]:
        """查询某 slug 的事件历史（按 id 倒序，即最新在前）。

        Args:
            slug: wiki 页面 slug
            limit: 返回条数上限（自动夹紧到 [1, MAX_LIMIT]）
            before_id: 分页游标，仅返回 id < before_id 的事件（实现"加载更多"）

        Returns:
            {"events": [...], "has_more": bool, "count": int}
            events 按 id 倒序排列（最新在前）
        """
        limit = max(1, min(limit, MAX_LIMIT))
        try:
            conn = _get_db()
            if before_id is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM collab_events
                    WHERE slug = ? AND id < ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (slug, int(before_id), limit + 1),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM collab_events
                    WHERE slug = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (slug, limit + 1),
                ).fetchall()
            conn.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "collab.event_store.list_failed",
                slug=slug,
                error=str(e),
            )
            return {"events": [], "has_more": False, "count": 0}

        # 多取 1 条用于判断 has_more
        has_more = len(rows) > limit
        events = [_row_to_dict(r) for r in rows[:limit]]
        return {
            "events": events,
            "has_more": has_more,
            "count": len(events),
        }

    def list_events_since(
        self,
        slug: str,
        since_timestamp: float,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """查询某时间点之后的事件（按时间戳升序，最旧在前）。

        用于客户端增量同步：已知历史最近事件时间戳，拉取之后新增事件。

        Args:
            slug: wiki 页面 slug
            since_timestamp: 起始时间戳（秒），不含等于
            limit: 返回条数上限

        Returns:
            {"events": [...], "has_more": bool, "count": int}
            events 按时间戳升序排列（最旧在前）
        """
        limit = max(1, min(limit, MAX_LIMIT))
        try:
            conn = _get_db()
            rows = conn.execute(
                """
                SELECT * FROM collab_events
                WHERE slug = ? AND timestamp > ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (slug, float(since_timestamp), limit + 1),
            ).fetchall()
            conn.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "collab.event_store.list_since_failed",
                slug=slug,
                error=str(e),
            )
            return {"events": [], "has_more": False, "count": 0}

        has_more = len(rows) > limit
        events = [_row_to_dict(r) for r in rows[:limit]]
        return {
            "events": events,
            "has_more": has_more,
            "count": len(events),
        }

    def count_events(self, slug: str) -> int:
        """统计某 slug 的事件总数。"""
        try:
            conn = _get_db()
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM collab_events WHERE slug = ?",
                (slug,),
            ).fetchone()
            conn.close()
            return int(row["cnt"]) if row else 0
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "collab.event_store.count_failed",
                slug=slug,
                error=str(e),
            )
            return 0

    def clear_events(self, slug: str | None = None) -> int:
        """清除事件。slug 为 None 时清除全部。返回删除行数。

        主要用于运维/测试场景。
        """
        try:
            conn = _get_db()
            if slug is None:
                cur = conn.execute("DELETE FROM collab_events")
            else:
                cur = conn.execute(
                    "DELETE FROM collab_events WHERE slug = ?", (slug,)
                )
            conn.commit()
            deleted = cur.rowcount
            conn.close()
            return deleted
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "collab.event_store.clear_failed",
                slug=slug,
                error=str(e),
            )
            return 0


# ────────── 全局单例 ──────────

_store: CollabEventStore | None = None


def get_collab_event_store() -> CollabEventStore:
    """获取全局 CollabEventStore 单例"""
    global _store
    if _store is None:
        _store = CollabEventStore()
    return _store
