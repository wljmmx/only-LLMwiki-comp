"""变更关联引擎（P2-3）

接收变更事件（部署/配置变更/扩缩容/重启等），与已有 incident 做关联，
辅助根因定位（"是不是这次发布引起的"）。

输入：变更事件（含 type/scope/timestamp/author/ticket_id）
处理：
  1. 变更事件持久化（changes 表）
  2. 给定时间窗口（前后各 N 分钟）检索 incident
  3. 评估关联度：
     - scope 重合（host/service/component）
     - 时间临近度（变更后 N 分钟内出现的告警权重高）
     - 变更类型权重（deployment > config_change > scaling > restart）
  4. 输出 change-incident 关联矩阵 + 风险评分
  5. 高风险关联建议回滚
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from app.aiops.event_correlator import DB_PATH, get_event_correlator

logger = structlog.get_logger()

# 变更类型 → 风险权重（0-1）
CHANGE_TYPE_WEIGHT = {
    "deployment": 0.9,      # 部署新版本，高风险
    "config_change": 0.7,   # 配置变更
    "migration": 0.85,      # 数据迁移
    "scaling": 0.5,         # 扩缩容
    "restart": 0.6,         # 重启
    "rollback": 0.4,        # 回滚（通常是修复动作）
    "patch": 0.65,          # 补丁
    "other": 0.3,
}


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS changes (
            id TEXT PRIMARY KEY,
            change_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            received_at TEXT NOT NULL,
            host TEXT,
            service TEXT,
            component TEXT,
            severity TEXT DEFAULT 'normal',
            author TEXT,
            ticket_id TEXT,
            description TEXT,
            attributes TEXT DEFAULT '{}',
            status TEXT DEFAULT 'completed',
            rollback_of TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_chg_ts ON changes(timestamp);
        CREATE INDEX IF NOT EXISTS idx_chg_service ON changes(service);
        CREATE INDEX IF NOT EXISTS idx_chg_host ON changes(host);
        CREATE INDEX IF NOT EXISTS idx_chg_ticket ON changes(ticket_id);

        CREATE TABLE IF NOT EXISTS change_incident_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            change_id TEXT NOT NULL,
            incident_id TEXT NOT NULL,
            correlation_score REAL NOT NULL,
            scope_overlap INTEGER NOT NULL,
            time_lag_seconds INTEGER NOT NULL,
            reasoning TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(change_id, incident_id)
        );
        CREATE INDEX IF NOT EXISTS idx_link_change ON change_incident_links(change_id);
        CREATE INDEX IF NOT EXISTS idx_link_incident ON change_incident_links(incident_id);
    """)


@dataclass
class Change:
    """变更事件"""
    id: str
    change_type: str
    timestamp: str
    host: str = ""
    service: str = ""
    component: str = ""
    severity: str = "normal"
    author: str = ""
    ticket_id: str = ""
    description: str = ""
    attributes: dict = field(default_factory=dict)
    status: str = "completed"
    rollback_of: str = ""


@dataclass
class ChangeIncidentLink:
    """变更-incident 关联"""
    change_id: str
    incident_id: str
    correlation_score: float       # 0-1
    scope_overlap: int             # 共享 entity 数
    time_lag_seconds: int          # incident 起始时间 - 变更时间（秒，负数=变更前告警）
    reasoning: str


class ChangeCorrelator:
    """变更关联引擎"""

    def __init__(self, time_window_minutes: int = 30) -> None:
        # 关联时间窗口：变更前后各 N 分钟内的 incident 视为候选
        self.time_window = timedelta(minutes=time_window_minutes)

    def ingest(self, changes: list[dict]) -> dict:
        """接收变更事件并存储"""
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        ingested = 0
        skipped = 0
        for ch in changes:
            ch_id = ch.get("id") or self._gen_id(ch)
            existing = conn.execute("SELECT id FROM changes WHERE id = ?", (ch_id,)).fetchone()
            if existing:
                skipped += 1
                continue
            conn.execute(
                """INSERT INTO changes
                   (id, change_type, timestamp, received_at, host, service, component,
                    severity, author, ticket_id, description, attributes, status, rollback_of)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ch_id,
                    ch.get("change_type", "other"),
                    ch.get("timestamp") or now,
                    now,
                    ch.get("host", ""),
                    ch.get("service", ""),
                    ch.get("component", ""),
                    ch.get("severity", "normal"),
                    ch.get("author", ""),
                    ch.get("ticket_id", ""),
                    ch.get("description", ""),
                    json.dumps(ch.get("attributes", {}), ensure_ascii=False),
                    ch.get("status", "completed"),
                    ch.get("rollback_of", ""),
                ),
            )
            ingested += 1
        conn.commit()
        logger.info("changes_ingested", ingested=ingested, skipped=skipped)
        return {"ingested": ingested, "skipped_duplicates": skipped}

    def correlate(
        self,
        since_hours: int = 24,
        time_window_minutes: int | None = None,
    ) -> dict:
        """关联最近变更与 incident

        Args:
            since_hours: 变更时间范围（向前推 N 小时）
            time_window_minutes: 变更-incident 时间窗口（默认 self.time_window）
        """
        conn = _get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
        ch_rows = conn.execute(
            """SELECT * FROM changes WHERE timestamp >= ?
               ORDER BY timestamp DESC""",
            (cutoff,),
        ).fetchall()
        if not ch_rows:
            return {"links": [], "stats": {"changes": 0, "incidents_linked": 0}}

        # 时间窗口可覆盖
        window = timedelta(minutes=time_window_minutes) if time_window_minutes else self.time_window
        # 同时向前推足够时间，覆盖 incident 表
        inc_cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours) - window).isoformat()
        inc_rows = conn.execute(
            """SELECT * FROM incidents
               WHERE started_at >= ? OR status = 'open'
               ORDER BY started_at ASC""",
            (inc_cutoff,),
        ).fetchall()

        links: list[ChangeIncidentLink] = []
        for ch_row in ch_rows:
            ch = self._row_to_change(ch_row)
            for inc_row in inc_rows:
                link = self._compute_link(ch, inc_row, window)
                if link and link.correlation_score >= 0.3:
                    links.append(link)

        # 持久化关联
        self._persist_links(conn, links)

        # 按 score 降序
        links.sort(key=lambda l: l.correlation_score, reverse=True)

        return {
            "links": [self._link_to_dict(l) for l in links],
            "stats": {
                "changes": len(ch_rows),
                "incidents_scanned": len(inc_rows),
                "links": len(links),
                "high_risk": sum(1 for l in links if l.correlation_score >= 0.7),
            },
        }

    def list_changes(
        self, service: str = "", limit: int = 50,
    ) -> list[dict]:
        """列出变更"""
        conn = _get_db()
        if service:
            rows = conn.execute(
                "SELECT * FROM changes WHERE service = ? ORDER BY timestamp DESC LIMIT ?",
                (service, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM changes ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["attributes"] = json.loads(d.get("attributes") or "{}")
            result.append(d)
        return result

    def get_change(self, change_id: str) -> dict | None:
        conn = _get_db()
        r = conn.execute("SELECT * FROM changes WHERE id = ?", (change_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["attributes"] = json.loads(d.get("attributes") or "{}")
        # 加载关联 incident
        link_rows = conn.execute(
            """SELECT cil.*, i.started_at, i.severity, i.scope, i.suspected_root_cause
               FROM change_incident_links cil
               JOIN incidents i ON cil.incident_id = i.incident_id
               WHERE cil.change_id = ?
               ORDER BY cil.correlation_score DESC""",
            (change_id,),
        ).fetchall()
        d["linked_incidents"] = []
        for lr in link_rows:
            ld = dict(lr)
            ld["scope"] = json.loads(ld.get("scope") or "{}")
            d["linked_incidents"].append(ld)
        return d

    def get_incident_changes(self, incident_id: str) -> list[dict]:
        """查询 incident 关联的变更（反向查询）"""
        conn = _get_db()
        rows = conn.execute(
            """SELECT c.*, cil.correlation_score, cil.reasoning, cil.time_lag_seconds
               FROM change_incident_links cil
               JOIN changes c ON cil.change_id = c.id
               WHERE cil.incident_id = ?
               ORDER BY cil.correlation_score DESC""",
            (incident_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["attributes"] = json.loads(d.get("attributes") or "{}")
            result.append(d)
        return result

    # ────────── 关联算法 ──────────

    def _compute_link(
        self, change: Change, inc_row: sqlite3.Row, window: timedelta,
    ) -> ChangeIncidentLink | None:
        """计算单个 change-incident 关联"""
        inc_started = self._parse_ts(inc_row["started_at"])
        ch_ts = self._parse_ts(change.timestamp)
        time_lag = (inc_started - ch_ts).total_seconds()

        # 时间窗口过滤：incident 起始时间必须在 [变更-window, 变更+window] 内
        if abs(time_lag) > window.total_seconds():
            return None

        # scope 重合度
        inc_scope = json.loads(inc_row["scope"] or "{}")
        inc_hosts = set(h.lower() for h in inc_scope.get("hosts", []) if h)
        inc_services = set(s.lower() for s in inc_scope.get("services", []) if s)
        inc_components = set(c.lower() for c in inc_scope.get("components", []) if c)

        ch_host = change.host.lower() if change.host else ""
        ch_service = change.service.lower() if change.service else ""
        ch_component = change.component.lower() if change.component else ""

        overlap: list[str] = []
        if ch_host and ch_host in inc_hosts:
            overlap.append(f"host:{ch_host}")
        if ch_service and ch_service in inc_services:
            overlap.append(f"service:{ch_service}")
        if ch_component and ch_component in inc_components:
            overlap.append(f"component:{ch_component}")

        if not overlap:
            return None  # 无 scope 重合，不关联

        # ── 关联评分 ──
        # 1. scope 重合分（每个共享 entity 0.2，封顶 0.6）
        scope_score = min(0.6, 0.2 * len(overlap))

        # 2. 时间临近分
        #    变更后 0-5 分钟：满分 0.3
        #    变更后 5-30 分钟：0.3 → 0.1 线性衰减
        #    变更前：0.05（可能是变更触发的修复）
        abs_min = abs(time_lag) / 60.0
        if 0 <= time_lag <= 300:       # 0-5 min after
            time_score = 0.3
        elif 300 < time_lag <= 1800:   # 5-30 min after
            time_score = 0.3 - 0.2 * (abs_min - 5) / 25
        elif time_lag < 0:             # before
            time_score = 0.05
        else:                          # >30 min after
            time_score = 0.05

        # 3. 变更类型权重
        type_weight = CHANGE_TYPE_WEIGHT.get(change.change_type, 0.3)

        # 综合分（封顶 1.0）
        score = min(1.0, scope_score + time_score + type_weight * 0.1)

        # 推理说明
        reasoning_parts = []
        reasoning_parts.append(f"scope 重合 {len(overlap)} 项: {', '.join(overlap)}")
        if time_lag >= 0:
            reasoning_parts.append(f"incident 发生在变更后 {int(time_lag)}s")
        else:
            reasoning_parts.append(f"incident 发生在变更前 {int(-time_lag)}s（可能是触发原因）")
        reasoning_parts.append(f"变更类型 {change.change_type} 权重 {type_weight}")

        return ChangeIncidentLink(
            change_id=change.id,
            incident_id=inc_row["incident_id"],
            correlation_score=round(score, 3),
            scope_overlap=len(overlap),
            time_lag_seconds=int(time_lag),
            reasoning="; ".join(reasoning_parts),
        )

    def suggest_rollback(self, incident_id: str) -> dict:
        """基于 incident 关联的变更，给出回滚建议"""
        changes = self.get_incident_changes(incident_id)
        if not changes:
            return {
                "incident_id": incident_id,
                "suggested": False,
                "reason": "无关联变更",
            }
        # 取相关度最高且非 rollback 类型的变更
        candidates = [
            c for c in changes
            if c.get("change_type") != "rollback"
            and c.get("correlation_score", 0) >= 0.5
        ]
        if not candidates:
            return {
                "incident_id": incident_id,
                "suggested": False,
                "reason": "关联变更相关度不足",
                "linked_changes": changes,
            }
        top = max(candidates, key=lambda c: c.get("correlation_score", 0))
        return {
            "incident_id": incident_id,
            "suggested": True,
            "change_id": top["id"],
            "change_type": top["change_type"],
            "service": top.get("service", ""),
            "host": top.get("host", ""),
            "timestamp": top["timestamp"],
            "author": top.get("author", ""),
            "ticket_id": top.get("ticket_id", ""),
            "correlation_score": top.get("correlation_score", 0),
            "reasoning": top.get("reasoning", ""),
            "all_linked_changes": changes,
        }

    # ────────── 工具方法 ──────────

    @staticmethod
    def _row_to_change(r: sqlite3.Row) -> Change:
        return Change(
            id=r["id"],
            change_type=r["change_type"],
            timestamp=r["timestamp"],
            host=r["host"] or "",
            service=r["service"] or "",
            component=r["component"] or "",
            severity=r["severity"] or "normal",
            author=r["author"] or "",
            ticket_id=r["ticket_id"] or "",
            description=r["description"] or "",
            attributes=json.loads(r["attributes"] or "{}"),
            status=r["status"] or "completed",
            rollback_of=r["rollback_of"] or "",
        )

    @staticmethod
    def _parse_ts(ts: str) -> datetime:
        try:
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    @staticmethod
    def _link_to_dict(l: ChangeIncidentLink) -> dict:
        return {
            "change_id": l.change_id,
            "incident_id": l.incident_id,
            "correlation_score": l.correlation_score,
            "scope_overlap": l.scope_overlap,
            "time_lag_seconds": l.time_lag_seconds,
            "reasoning": l.reasoning,
        }

    @staticmethod
    def _gen_id(ch: dict) -> str:
        raw = json.dumps(ch, sort_keys=True, ensure_ascii=False)
        return "chg-" + hashlib.sha1(raw.encode()).hexdigest()[:16]

    def _persist_links(self, conn: sqlite3.Connection, links: list[ChangeIncidentLink]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for l in links:
            conn.execute(
                """INSERT OR REPLACE INTO change_incident_links
                   (change_id, incident_id, correlation_score, scope_overlap,
                    time_lag_seconds, reasoning, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (l.change_id, l.incident_id, l.correlation_score,
                 l.scope_overlap, l.time_lag_seconds, l.reasoning, now),
            )
        conn.commit()


# 全局单例
_correlator: ChangeCorrelator | None = None


def get_change_correlator() -> ChangeCorrelator:
    global _correlator
    if _correlator is None:
        _correlator = ChangeCorrelator()
    return _correlator
