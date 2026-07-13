"""审计日志持久化存储（SQLite）

表结构 audit_log：
- id           自增主键
- timestamp    ISO8601 UTC 时间戳
- user         操作者用户名（未登录为 "anonymous"）
- method       HTTP 方法（POST/PUT/PATCH/DELETE）
- path         请求路径
- status       响应状态码
- duration_ms  处理耗时（毫秒）
- request_id   请求追踪 ID（X-Request-ID 或自动生成 UUID）
- payload_summary  请求体摘要（截断 200 字符，敏感字段脱敏）
- user_agent   User-Agent
- ip           客户端 IP

复用项目 SQLite + WAL 模式，与 webhook_store / document_store 等保持一致。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("audit")

# 审计日志 DB 路径：data/audit.db
DB_PATH = Path(__file__).parent.parent.parent / "data" / "audit.db"


def _get_db() -> sqlite3.Connection:
    """获取 SQLite 连接（幂等初始化 schema）"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """创建 audit_log 表（幂等）"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user TEXT NOT NULL DEFAULT 'anonymous',
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            status INTEGER NOT NULL,
            duration_ms REAL NOT NULL DEFAULT 0,
            request_id TEXT,
            payload_summary TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            ip TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
        CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user);
        CREATE INDEX IF NOT EXISTS idx_audit_method ON audit_log(method);
        CREATE INDEX IF NOT EXISTS idx_audit_path ON audit_log(path);
        """
    )


def _now_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串"""
    return datetime.now(timezone.utc).isoformat()


class AuditStore:
    """审计日志存储 — 写操作记录与查询"""

    def log_write(
        self,
        user: str,
        method: str,
        path: str,
        status: int,
        duration_ms: float,
        request_id: str | None,
        payload_summary: str = "",
        user_agent: str = "",
        ip: str = "",
    ) -> int:
        """写入一条审计日志

        Args:
            user: 操作者用户名
            method: HTTP 方法
            path: 请求路径
            status: 响应状态码
            duration_ms: 处理耗时（毫秒）
            request_id: 请求追踪 ID
            payload_summary: 请求体摘要（已脱敏 + 截断）
            user_agent: User-Agent
            ip: 客户端 IP

        Returns:
            新插入行的 id
        """
        timestamp = _now_iso()
        conn = _get_db()
        try:
            cur = conn.execute(
                """INSERT INTO audit_log
                   (timestamp, user, method, path, status, duration_ms,
                    request_id, payload_summary, user_agent, ip)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    timestamp,
                    user or "anonymous",
                    method,
                    path,
                    int(status),
                    float(duration_ms),
                    request_id or "",
                    payload_summary,
                    user_agent,
                    ip,
                ),
            )
            conn.commit()
            log_id = cur.lastrowid or 0
        finally:
            conn.close()
        logger.info(
            "audit.write",
            id=log_id,
            user=user,
            method=method,
            path=path,
            status=status,
            duration_ms=round(duration_ms, 2),
            request_id=request_id,
        )
        return log_id

    def list_audit_logs(
        self,
        user: str | None = None,
        method: str | None = None,
        path: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """查询审计日志（支持多条件过滤）

        Args:
            user: 按用户名过滤（精确匹配）
            method: 按方法过滤
            path: 按路径过滤（LIKE 模糊匹配，支持子串）
            start: 起始时间（ISO8601，含）
            end: 结束时间（ISO8601，含）
            limit: 返回上限
            offset: 分页偏移

        Returns:
            审计日志记录列表，按时间倒序
        """
        # 限制 limit 上限，避免超大结果集
        limit = max(1, min(limit, 1000))
        clauses: list[str] = []
        params: list[Any] = []
        if user:
            clauses.append("user = ?")
            params.append(user)
        if method:
            clauses.append("method = ?")
            params.append(method.upper())
        if path:
            clauses.append("path LIKE ?")
            params.append(f"%{path}%")
        if start:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end:
            clauses.append("timestamp <= ?")
            params.append(end)
        sql = "SELECT * FROM audit_log"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        conn = _get_db()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def get_audit_stats(self) -> dict[str, Any]:
        """获取审计日志统计摘要

        Returns:
            {"total": int, "by_method": {method: count}, "by_status": {status: count},
             "by_user": {user: count}}
        """
        conn = _get_db()
        try:
            total = conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
            by_method: dict[str, int] = {}
            for row in conn.execute(
                "SELECT method, COUNT(*) AS c FROM audit_log GROUP BY method"
            ).fetchall():
                by_method[row["method"]] = row["c"]
            by_status: dict[str, int] = {}
            for row in conn.execute(
                "SELECT status, COUNT(*) AS c FROM audit_log GROUP BY status"
            ).fetchall():
                by_status[str(row["status"])] = row["c"]
            by_user: dict[str, int] = {}
            for row in conn.execute(
                "SELECT user, COUNT(*) AS c FROM audit_log GROUP BY user ORDER BY c DESC LIMIT 20"
            ).fetchall():
                by_user[row["user"]] = row["c"]
            return {
                "total": total,
                "by_method": by_method,
                "by_status": by_status,
                "by_user": by_user,
            }
        finally:
            conn.close()

    def count(self) -> int:
        """总记录数（测试辅助）"""
        conn = _get_db()
        try:
            return conn.execute("SELECT COUNT(*) AS c FROM audit_log").fetchone()["c"]
        finally:
            conn.close()

    def clear(self) -> None:
        """清空所有审计日志（测试辅助）"""
        conn = _get_db()
        try:
            conn.execute("DELETE FROM audit_log")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "user": row["user"],
            "method": row["method"],
            "path": row["path"],
            "status": row["status"],
            "duration_ms": row["duration_ms"],
            "request_id": row["request_id"],
            "payload_summary": row["payload_summary"],
            "user_agent": row["user_agent"],
            "ip": row["ip"],
        }


# ────────── 单例 ──────────

_store: AuditStore | None = None


def get_audit_store() -> AuditStore:
    """获取 AuditStore 单例"""
    global _store
    if _store is None:
        _store = AuditStore()
    return _store


__all__ = ["AuditStore", "DB_PATH", "get_audit_store"]
