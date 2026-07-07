"""时序数据存储（S15-3）

为 Prometheus 指标提供轻量级历史时序留存，支撑异常检测算法的滑动窗口。

表结构：
- metrics_timeseries: 每行一个数据点（metric_name + timestamp + value + labels）

复用 SQLite + WAL 模式，与项目其他存储保持一致。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "timeseries.db"


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
        CREATE TABLE IF NOT EXISTS metrics_timeseries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_name TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            value REAL NOT NULL,
            labels TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ts_metric_time ON metrics_timeseries(metric_name, timestamp);
        CREATE INDEX IF NOT EXISTS idx_ts_labels ON metrics_timeseries(labels);
        """
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts: str) -> datetime:
    """解析 ISO8601 时间戳，容错处理 Z 后缀"""
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


class TimeseriesStore:
    """时序数据存储（S15-3）"""

    def record(
        self,
        metric_name: str,
        value: float,
        labels: dict | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """记录一个数据点

        Args:
            metric_name: 指标名，如 "http_requests_total" / "cpu_usage"
            value: 数值
            labels: 标签字典，如 {"host": "prod-01", "service": "nginx"}
            timestamp: 数据点时间戳（ISO8601 UTC），不传则用当前时间

        Returns:
            记录的数据点 dict
        """
        if not metric_name:
            raise ValueError("metric_name 不能为空")
        labels = labels or {}
        ts = timestamp or _now_iso()
        # 校验时间格式合法
        _parse_ts(ts)
        now = _now_iso()
        conn = _get_db()
        try:
            cur = conn.execute(
                """INSERT INTO metrics_timeseries
                   (metric_name, timestamp, value, labels, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    metric_name,
                    ts,
                    float(value),
                    json.dumps(labels, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            conn.commit()
            point_id = cur.lastrowid
        finally:
            conn.close()
        logger.info(
            "timeseries.record",
            metric_name=metric_name,
            value=value,
            timestamp=ts,
            point_id=point_id,
        )
        return {
            "id": point_id,
            "metric_name": metric_name,
            "timestamp": ts,
            "value": float(value),
            "labels": labels,
            "created_at": now,
        }

    def query(
        self,
        metric_name: str,
        start_time: str | None = None,
        end_time: str | None = None,
        labels: dict | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """查询时序数据

        Args:
            metric_name: 指标名
            start_time: 起始时间（ISO8601，含），不传则不限
            end_time: 结束时间（ISO8601，含），不传则不限
            labels: 标签过滤字典，所有 key-value 都需匹配
            limit: 返回数量上限

        Returns:
            数据点列表，按 timestamp 升序
        """
        clauses: list[str] = ["metric_name = ?"]
        params: list[Any] = [metric_name]
        if start_time:
            clauses.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            clauses.append("timestamp <= ?")
            params.append(end_time)
        if labels:
            # 使用 json_extract 对每个 label key 做精确匹配
            for k, v in labels.items():
                clauses.append(f"json_extract(labels, '$.{k}') = ?")
                params.append(str(v))
        sql = (
            "SELECT * FROM metrics_timeseries WHERE "
            + " AND ".join(clauses)
            + " ORDER BY timestamp ASC LIMIT ?"
        )
        params.append(int(limit))
        conn = _get_db()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_point(r) for r in rows]
        finally:
            conn.close()

    def cleanup_old(self, days: int = 30) -> int:
        """清理超过 N 天的旧数据

        Args:
            days: 保留天数，超过此天数的记录被删除

        Returns:
            删除的记录数
        """
        if days < 0:
            raise ValueError("days 不能为负数")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        conn = _get_db()
        try:
            cur = conn.execute(
                "DELETE FROM metrics_timeseries WHERE timestamp < ?",
                (cutoff,),
            )
            conn.commit()
            deleted = cur.rowcount
        finally:
            conn.close()
        logger.info("timeseries.cleanup", days=days, deleted=deleted)
        return deleted

    def get_latest_values(
        self,
        metric_name: str,
        count: int = 100,
        labels: dict | None = None,
    ) -> list[float]:
        """获取最近的 N 个值（用于异常检测的滑动窗口）

        按 timestamp 降序取最近 count 条，再反转为时间升序（旧的在前），
        便于 EWMA 等顺序敏感算法直接使用。

        Args:
            metric_name: 指标名
            count: 取最近 N 个值
            labels: 标签过滤字典

        Returns:
            数值列表，时间升序（旧 → 新）
        """
        if count <= 0:
            return []
        clauses: list[str] = ["metric_name = ?"]
        params: list[Any] = [metric_name]
        if labels:
            for k, v in labels.items():
                clauses.append(f"json_extract(labels, '$.{k}') = ?")
                params.append(str(v))
        sql = (
            "SELECT value FROM metrics_timeseries WHERE "
            + " AND ".join(clauses)
            + " ORDER BY timestamp DESC LIMIT ?"
        )
        params.append(int(count))
        conn = _get_db()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        # DESC 取出（新→旧），反转为升序（旧→新）
        values = [r["value"] for r in rows]
        values.reverse()
        return values

    @staticmethod
    def _row_to_point(row: sqlite3.Row) -> dict[str, Any]:
        try:
            labels = json.loads(row["labels"]) if row["labels"] else {}
        except (json.JSONDecodeError, TypeError):
            labels = {}
        return {
            "id": row["id"],
            "metric_name": row["metric_name"],
            "timestamp": row["timestamp"],
            "value": row["value"],
            "labels": labels,
            "created_at": row["created_at"],
        }


# ────────── 单例 ──────────

_store: TimeseriesStore | None = None


def get_timeseries_store() -> TimeseriesStore:
    global _store
    if _store is None:
        _store = TimeseriesStore()
    return _store
