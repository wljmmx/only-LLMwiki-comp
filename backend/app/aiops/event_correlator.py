"""事件关联引擎（P2-2）

接收告警事件流，基于知识库做关联分组 + 根因推断。

输入：告警事件列表（含 host/service/severity/message/timestamp）
处理：
  1. 时间窗口聚合（默认 5 分钟内的事件视为同一批）
  2. 按实体维度分组（host / service / component）
  3. 基于依赖关系（DEPENDS_ON / RUNS_ON）做拓扑关联
  4. 根因推断：上游节点（被依赖方）先告警 → 可能是根因
输出：
  - incidents: 关联分组后的故障列表
  - stats: 统计信息
  - runbook_hints: 每个故障对应的 Runbook 生成提示

事件持久化到 SQLite，便于查询历史。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "events.db"

SEVERITY_RANK = {
    "info": 0,
    "low": 1,
    "warning": 2,
    "high": 3,
    "critical": 4,
    "fatal": 5,
}

# ────────── Incident 状态机（P2-2.2）──────────
# 状态序列：open → ack → investigating → mitigated → resolved
# - 允许前向跳跃（如 open → resolved 处理误报）
# - 允许 reopen（非 open 状态可回退到 open）
# - resolved 为终态，仅允许 reopen
# - closed 为历史遗留别名（等价于 resolved），向后兼容
INCIDENT_STATES: list[str] = [
    "open",
    "ack",
    "investigating",
    "mitigated",
    "resolved",
]

# 合法迁移表：当前状态 → 允许的目标状态集合
INCIDENT_TRANSITIONS: dict[str, set[str]] = {
    "open": {"ack", "investigating", "mitigated", "resolved"},
    "ack": {"investigating", "mitigated", "resolved", "open"},
    "investigating": {"mitigated", "resolved", "open"},
    "mitigated": {"resolved", "investigating", "open"},
    "resolved": {"open"},  # reopen
    "closed": {"open"},  # legacy alias
}

# 终态集合（不可再前向迁移，仅可 reopen）
TERMINAL_STATES = {"resolved", "closed"}

# 状态对应的 timestamp 字段（用于状态机时间戳记录）
STATE_TIMESTAMP_FIELD: dict[str, str] = {
    "ack": "acknowledged_at",
    "resolved": "resolved_at",
    "closed": "resolved_at",  # legacy
}


def is_valid_state(state: str) -> bool:
    return state in INCIDENT_TRANSITIONS


def can_transition(from_state: str, to_state: str) -> bool:
    """检查状态迁移是否合法"""
    allowed = INCIDENT_TRANSITIONS.get(from_state, set())
    return to_state in allowed


class InvalidTransitionError(ValueError):
    """非法状态迁移"""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"非法状态迁移: {from_state} → {to_state}。"
            f"允许的目标: {sorted(INCIDENT_TRANSITIONS.get(from_state, set()))}"
        )


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            received_at TEXT NOT NULL,
            host TEXT,
            service TEXT,
            component TEXT,
            severity TEXT DEFAULT 'warning',
            message TEXT,
            tags TEXT DEFAULT '{}',
            source TEXT DEFAULT 'unknown',
            incident_id TEXT,
            attributes TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_evt_ts ON events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_evt_host ON events(host);
        CREATE INDEX IF NOT EXISTS idx_evt_service ON events(service);
        CREATE INDEX IF NOT EXISTS idx_evt_incident ON events(incident_id);
        CREATE INDEX IF NOT EXISTS idx_evt_severity ON events(severity);

        CREATE TABLE IF NOT EXISTS incidents (
            incident_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            severity TEXT DEFAULT 'warning',
            scope TEXT,
            suspected_root_cause TEXT,
            runbook_hint TEXT,
            alert_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'open',
            created_at TEXT NOT NULL,
            -- P2-2.2 状态机字段
            acknowledged_at TEXT,
            resolved_at TEXT,
            assignee TEXT DEFAULT '',
            transition_history TEXT DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_inc_status ON incidents(status);
        CREATE INDEX IF NOT EXISTS idx_inc_started ON incidents(started_at);
    """)
    # 旧库迁移：补齐新增列（SQLite 不支持 ADD COLUMN IF NOT EXISTS）
    _migrate_incidents_columns(conn)


def _migrate_incidents_columns(conn: sqlite3.Connection) -> None:
    """对历史 incidents 表补齐 P2-2.2 新增列"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(incidents)")}
    migrations = [
        ("acknowledged_at", "TEXT"),
        ("resolved_at", "TEXT"),
        ("assignee", "TEXT DEFAULT ''"),
        ("transition_history", "TEXT DEFAULT '[]'"),
    ]
    for col, decl in migrations:
        if col not in cols:
            conn.execute(f"ALTER TABLE incidents ADD COLUMN {col} {decl}")


@dataclass
class Event:
    """告警事件"""

    id: str
    timestamp: str
    host: str = ""
    service: str = ""
    component: str = ""
    severity: str = "warning"
    message: str = ""
    tags: dict = field(default_factory=dict)
    source: str = "unknown"
    attributes: dict = field(default_factory=dict)


@dataclass
class Incident:
    """关联后的故障"""

    incident_id: str
    started_at: str
    ended_at: str
    severity: str
    scope: dict  # {hosts, services, components}
    suspected_root_cause: str
    runbook_hint: str
    alert_ids: list[str]
    alerts: list[dict]


class EventCorrelator:
    """事件关联引擎"""

    def __init__(self, time_window_minutes: int = 5) -> None:
        self.time_window = timedelta(minutes=time_window_minutes)
        # P2-2.1: 上次去重计数，供 correlate() 报告 noise_filtered 使用
        self._last_dup_count: int = 0

    def ingest(self, events: list[dict]) -> dict:
        """接收事件并存储

        Returns:
            {"ingested": N, "skipped_duplicates": M}
        """
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        ingested = 0
        skipped = 0
        for ev in events:
            ev_id = ev.get("id") or self._gen_id(ev)
            existing = conn.execute(
                "SELECT id FROM events WHERE id = ?", (ev_id,)
            ).fetchone()
            if existing:
                skipped += 1
                continue
            conn.execute(
                """INSERT INTO events
                   (id, timestamp, received_at, host, service, component,
                    severity, message, tags, source, attributes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ev_id,
                    ev.get("timestamp") or now,
                    now,
                    ev.get("host", ""),
                    ev.get("service", ""),
                    ev.get("component", ""),
                    ev.get("severity", "warning"),
                    ev.get("message", ""),
                    json.dumps(ev.get("tags", {}), ensure_ascii=False),
                    ev.get("source", "unknown"),
                    json.dumps(ev.get("attributes", {}), ensure_ascii=False),
                ),
            )
            ingested += 1
        conn.commit()
        logger.info("events_ingested", ingested=ingested, skipped=skipped)
        return {"ingested": ingested, "skipped_duplicates": skipped}

    def correlate(
        self,
        since_minutes: int = 60,
        max_events: int = 500,
    ) -> dict:
        """关联最近事件，输出 incident 分组

        Args:
            since_minutes: 关联时间窗口起点（向前推 N 分钟）
            max_events: 最多处理的事件数
        """
        conn = _get_db()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        ).isoformat()
        rows = conn.execute(
            """SELECT * FROM events
               WHERE timestamp >= ? AND incident_id IS NULL
               ORDER BY timestamp ASC LIMIT ?""",
            (cutoff, max_events),
        ).fetchall()
        if not rows:
            return {"incidents": [], "stats": {"total_alerts": 0, "incidents": 0}}

        events = [self._row_to_event(r) for r in rows]
        incidents = self._correlate_events(events)

        # 持久化 incident + 关联事件
        self._persist_incidents(conn, incidents)

        # P2-2.1/P2-2.3: noise_filtered 包含指纹去重数 + 无实体单事件过滤数
        dup_count = getattr(self, "_last_dup_count", 0)
        noise_filtered = max(
            0, len(events) - sum(len(i.alert_ids) for i in incidents)
        )
        return {
            "incidents": [self._incident_to_dict(i) for i in incidents],
            "stats": {
                "total_alerts": len(events),
                "incidents": len(incidents),
                "noise_filtered": noise_filtered,
                "duplicates_suppressed": dup_count,
            },
        }

    def list_incidents(
        self, status: str = "open", limit: int = 50
    ) -> list[dict]:
        """列出 incident

        Args:
            status: 状态过滤，支持 "all" 或 "" 表示不过滤
            limit: 返回数量上限
        """
        conn = _get_db()
        if status in ("", "all", "*"):
            rows = conn.execute(
                "SELECT * FROM incidents ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM incidents WHERE status = ?
                   ORDER BY started_at DESC LIMIT ?""",
                (status, limit),
            ).fetchall()
        result = []
        for r in rows:
            inc = dict(r)
            scope = json.loads(inc.get("scope") or "{}")
            inc["scope"] = scope
            inc["transition_history"] = json.loads(
                inc.get("transition_history") or "[]"
            )
            # 加载关联事件
            ev_rows = conn.execute(
                "SELECT id, timestamp, host, service, severity, message FROM events WHERE incident_id = ?",
                (inc["incident_id"],),
            ).fetchall()
            inc["alerts"] = [dict(e) for e in ev_rows]
            result.append(inc)
        return result

    def get_incident(self, incident_id: str) -> dict | None:
        conn = _get_db()
        r = conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
        ).fetchone()
        if not r:
            return None
        inc = dict(r)
        inc["scope"] = json.loads(inc.get("scope") or "{}")
        inc["transition_history"] = json.loads(
            inc.get("transition_history") or "[]"
        )
        ev_rows = conn.execute(
            "SELECT id, timestamp, host, service, component, severity, message, tags FROM events WHERE incident_id = ?",
            (inc["incident_id"],),
        ).fetchall()
        alerts = []
        for e in ev_rows:
            d = dict(e)
            d["tags"] = json.loads(d.get("tags") or "{}")
            alerts.append(d)
        inc["alerts"] = alerts
        return inc

    # ────────── P2-2.2 状态机 ──────────

    def transition_incident(
        self,
        incident_id: str,
        target_state: str,
        *,
        note: str = "",
        by: str = "",
    ) -> dict:
        """执行状态迁移

        Args:
            incident_id: incident ID
            target_state: 目标状态（open/ack/investigating/mitigated/resolved）
            note: 迁移备注（写入 transition_history）
            by: 操作人（写入 transition_history）

        Returns:
            更新后的 incident dict

        Raises:
            KeyError: incident 不存在
            InvalidTransitionError: 非法迁移
        """
        if not is_valid_state(target_state):
            raise InvalidTransitionError("?", target_state)

        conn = _get_db()
        r = conn.execute(
            "SELECT status FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if not r:
            raise KeyError(incident_id)

        current = r["status"]
        # legacy: closed 等价于 resolved
        effective_current = "resolved" if current == "closed" else current
        effective_target = "resolved" if target_state == "closed" else target_state

        # 已在目标状态：幂等返回
        if effective_current == effective_target:
            return self.get_incident(incident_id)  # type: ignore[return-value]

        if not can_transition(effective_current, effective_target):
            raise InvalidTransitionError(current, target_state)

        now = datetime.now(timezone.utc).isoformat()

        # 构造 UPDATE 字段
        updates: dict[str, str | None] = {"status": target_state}
        ts_field = STATE_TIMESTAMP_FIELD.get(effective_target)
        if ts_field:
            updates[ts_field] = now
        # 进入 resolved/closed 时同时设置 ended_at
        if effective_target in ("resolved", "closed"):
            updates["ended_at"] = now
        # reopen（target=open）时清空 ended_at/resolved_at
        if effective_target == "open":
            updates["ended_at"] = None
            updates["resolved_at"] = None

        # 追加 transition_history
        history_entry = {
            "from": current,
            "to": target_state,
            "at": now,
            "by": by,
            "note": note,
        }
        # 取当前 history
        cur_hist = conn.execute(
            "SELECT transition_history FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        try:
            hist_list = json.loads(cur_hist["transition_history"] or "[]")
        except (json.JSONDecodeError, TypeError):
            hist_list = []
        hist_list.append(history_entry)
        updates["transition_history"] = json.dumps(
            hist_list, ensure_ascii=False
        )

        # 执行 UPDATE
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        params: list = list(updates.values()) + [incident_id]
        conn.execute(
            f"UPDATE incidents SET {set_clause} WHERE incident_id = ?",
            params,
        )
        conn.commit()
        logger.info(
            "incident_transitioned",
            incident_id=incident_id,
            from_state=current,
            to_state=target_state,
            by=by,
        )
        return self.get_incident(incident_id)  # type: ignore[return-value]

    def close_incident(self, incident_id: str, note: str = "") -> bool:
        """关闭 incident（向后兼容包装）

        内部调用 transition_incident 走 resolved 状态。
        """
        try:
            self.transition_incident(
                incident_id, "resolved", note=note, by="legacy-close"
            )
            return True
        except (KeyError, InvalidTransitionError):
            return False

    # ────────── 关联核心算法 ──────────

    def _correlate_events(self, events: list[Event]) -> list[Incident]:
        """关联算法：时间窗口 + 实体维度"""
        if not events:
            return []

        # 0. P2-2.1 指纹去重：同指纹重复告警只保留首条，其余计入 duplicate_count
        deduped, dup_count = self._deduplicate(events)
        if dup_count:
            logger.info("alert_dedup", duplicates=dup_count, kept=len(deduped))
        # 记录去重计数，供 correlate() 计算 noise_filtered 使用
        self._last_dup_count = dup_count

        # 1. 按时间窗口分批
        batches = self._time_window_batch(deduped)

        # 2. 每批内按实体维度聚类
        incidents: list[Incident] = []
        for batch in batches:
            clusters = self._cluster_by_entity(batch)
            for cluster in clusters:
                inc = self._build_incident(cluster)
                if inc:
                    incidents.append(inc)

        # 3. 跨时间窗口合并（同一 scope 的 incident 合并）
        merged = self._merge_incidents(incidents)

        # 4. 根因推断
        for inc in merged:
            self._infer_root_cause(inc)

        return merged

    # ────────── P2-2.1 指纹去重 ──────────

    @staticmethod
    def _fingerprint(e: Event) -> str:
        """生成告警指纹（用于去重）

        同 host+service+component+severity+message 视为同一告警的重复触发。
        message 做轻量归一化：去除多余空白、转小写、截断到 200 字。
        """
        msg_norm = " ".join((e.message or "").lower().split())[:200]
        raw = "|".join(
            [
                (e.host or "").lower(),
                (e.service or "").lower(),
                (e.component or "").lower(),
                (e.severity or "").lower(),
                msg_norm,
            ]
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _deduplicate(
        self, events: list[Event]
    ) -> tuple[list[Event], int]:
        """P2-2.1 指纹去重：同指纹事件只保留首条（按时间戳最早）

        重复事件计数为 noise（duplicate），不进入 incident。

        Returns:
            (deduped_events, duplicate_count)
        """
        if not events:
            return [], 0
        # 按时间戳排序，保留最早出现的
        sorted_evts = sorted(events, key=lambda e: e.timestamp)
        seen: dict[str, Event] = {}
        dup_count = 0
        for e in sorted_evts:
            fp = self._fingerprint(e)
            if fp in seen:
                dup_count += 1
            else:
                seen[fp] = e
        # 保持原时间顺序
        deduped = sorted(seen.values(), key=lambda e: e.timestamp)
        return deduped, dup_count

    def _time_window_batch(self, events: list[Event]) -> list[list[Event]]:
        """按时间窗口分批"""
        if not events:
            return []
        sorted_evts = sorted(events, key=lambda e: e.timestamp)
        batches: list[list[Event]] = []
        current: list[Event] = [sorted_evts[0]]
        window_start = self._parse_ts(sorted_evts[0].timestamp)
        for e in sorted_evts[1:]:
            ts = self._parse_ts(e.timestamp)
            if ts - window_start <= self.time_window:
                current.append(e)
            else:
                batches.append(current)
                current = [e]
                window_start = ts
        if current:
            batches.append(current)
        return batches

    def _cluster_by_entity(self, events: list[Event]) -> list[list[Event]]:
        """按实体维度聚类（同一 host/service/component 视为相关）"""
        # 用 Union-Find
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # 每个事件按 (host, service, component) 建立 entity key
        evt_keys: list[set[str]] = []
        for e in events:
            keys: set[str] = set()
            if e.host:
                keys.add(f"host:{e.host.lower()}")
            if e.service:
                keys.add(f"svc:{e.service.lower()}")
            if e.component:
                keys.add(f"comp:{e.component.lower()}")
            # 同 message 关键词也视为可能相关
            if e.message:
                # 取前 20 字作为 message 指纹
                keys.add(f"msg:{e.message[:20].lower()}")
            evt_keys.append(keys)
            for k in keys:
                parent.setdefault(k, k)

        # 同事件内 keys 互相 union
        for keys in evt_keys:
            klist = list(keys)
            for i in range(1, len(klist)):
                union(klist[0], klist[i])

        # 跨事件 union（共享 entity key 的事件）
        for i in range(len(events)):
            for j in range(i + 1, len(events)):
                if evt_keys[i] & evt_keys[j]:
                    for k in evt_keys[i]:
                        if k in evt_keys[j]:
                            union(k, k)
                            break

        # 按 root 分组
        groups: dict[str, list[Event]] = defaultdict(list)
        for i, e in enumerate(events):
            if not evt_keys[i]:
                # 无任何 entity 信息，单独成组
                root = f"lonely:{e.id}"
                parent[root] = root
                groups[root].append(e)
            else:
                root = find(next(iter(evt_keys[i])))
                groups[root].append(e)

        # 单事件且无 entity 关联 → noise，过滤掉不计入 incident
        # noise_filtered 计数 = len(events) - sum(incident.alert_ids) 在调用方计算
        clusters: list[list[Event]] = []
        noise_count = 0
        for grp in groups.values():
            if len(grp) == 1 and not self._has_entity(grp[0]):
                # noise：单事件且无 entity 关联，过滤掉
                noise_count += 1
                continue
            clusters.append(grp)
        if noise_count:
            logger.info("noise_filtered", count=noise_count)
        return clusters

    def _build_incident(self, events: list[Event]) -> Incident | None:
        if not events:
            return None
        ts_sorted = sorted(events, key=lambda e: e.timestamp)
        hosts = sorted({e.host for e in events if e.host})
        services = sorted({e.service for e in events if e.service})
        components = sorted({e.component for e in events if e.component})
        severity = max(
            (e.severity for e in events), key=lambda s: SEVERITY_RANK.get(s, 0)
        )
        scope = {"hosts": hosts, "services": services, "components": components}

        # incident_id = hash(scope + 起始时间)
        scope_str = json.dumps(scope, sort_keys=True)
        inc_id = (
            "inc-"
            + hashlib.sha1(
                f"{scope_str}|{ts_sorted[0].timestamp}".encode()
            ).hexdigest()[:12]
        )

        return Incident(
            incident_id=inc_id,
            started_at=ts_sorted[0].timestamp,
            ended_at=ts_sorted[-1].timestamp,
            severity=severity,
            scope=scope,
            suspected_root_cause="",
            runbook_hint=self._build_runbook_hint(scope, events),
            alert_ids=[e.id for e in events],
            alerts=[self._event_to_dict(e) for e in events],
        )

    def _merge_incidents(self, incidents: list[Incident]) -> list[Incident]:
        """合并相同 scope 的 incident"""
        if not incidents:
            return []
        merged: dict[str, Incident] = {}
        for inc in incidents:
            key = json.dumps(inc.scope, sort_keys=True)
            if key in merged:
                # 合并
                m = merged[key]
                m.alert_ids.extend(inc.alert_ids)
                m.alerts.extend(inc.alerts)
                m.ended_at = max(m.ended_at, inc.ended_at)
                if SEVERITY_RANK.get(inc.severity, 0) > SEVERITY_RANK.get(
                    m.severity, 0
                ):
                    m.severity = inc.severity
            else:
                merged[key] = inc
        return list(merged.values())

    def _infer_root_cause(self, inc: Incident) -> None:
        """根因推断（启发式）

        策略：
        1. 若多个 host 上同一 service 同时告警 → 推断为 service 问题
        2. 若同一 host 上多个 service 告警 → 推断为 host 问题
        3. 取最早告警的事件作为根因候选
        """
        alerts = inc.alerts
        if not alerts:
            return
        sorted_alerts = sorted(alerts, key=lambda a: a["timestamp"])
        first = sorted_alerts[0]

        hosts = inc.scope.get("hosts", [])
        services = inc.scope.get("services", [])
        components = inc.scope.get("components", [])

        hints: list[str] = []
        if len(hosts) > 1 and len(services) == 1:
            hints.append(f"多主机同时出现 {services[0]} 告警，疑似服务侧问题")
        elif len(services) > 1 and len(hosts) == 1:
            hints.append(f"主机 {hosts[0]} 上多服务告警，疑似主机侧问题")
        elif len(components) == 1 and components:
            hints.append(f"组件 {components[0]} 异常")
        if first.get("message"):
            hints.append(f"最早告警: {first['message'][:100]}")

        inc.suspected_root_cause = "; ".join(hints) if hints else "根因待分析"

    def _build_runbook_hint(self, scope: dict, events: list[Event]) -> str:
        """生成 Runbook 自动生成的提示参数"""
        if not events:
            return ""
        # 取最高 severity 的事件作为 symptom
        ev = max(events, key=lambda e: SEVERITY_RANK.get(e.severity, 0))
        symptom = ev.message or ev.service or ev.host or "未知故障"
        return json.dumps(
            {
                "symptom": symptom,
                "service": scope.get("services", [""])[0]
                if scope.get("services")
                else "",
                "host": scope.get("hosts", [""])[0] if scope.get("hosts") else "",
            },
            ensure_ascii=False,
        )

    # ────────── 工具方法 ──────────

    @staticmethod
    def _has_entity(e: Event) -> bool:
        return bool(e.host or e.service or e.component)

    @staticmethod
    def _parse_ts(ts: str) -> datetime:
        try:
            # 兼容带/不带时区的 ISO 格式
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    @staticmethod
    def _row_to_event(r: sqlite3.Row) -> Event:
        return Event(
            id=r["id"],
            timestamp=r["timestamp"],
            host=r["host"] or "",
            service=r["service"] or "",
            component=r["component"] or "",
            severity=r["severity"] or "warning",
            message=r["message"] or "",
            tags=json.loads(r["tags"] or "{}"),
            source=r["source"] or "unknown",
            attributes=json.loads(r["attributes"] or "{}"),
        )

    @staticmethod
    def _event_to_dict(e: Event) -> dict:
        return {
            "id": e.id,
            "timestamp": e.timestamp,
            "host": e.host,
            "service": e.service,
            "component": e.component,
            "severity": e.severity,
            "message": e.message,
            "tags": e.tags,
            "source": e.source,
        }

    @staticmethod
    def _incident_to_dict(i: Incident) -> dict:
        return {
            "incident_id": i.incident_id,
            "started_at": i.started_at,
            "ended_at": i.ended_at,
            "severity": i.severity,
            "scope": i.scope,
            "suspected_root_cause": i.suspected_root_cause,
            "runbook_hint": i.runbook_hint,
            "alert_count": len(i.alert_ids),
            "alerts": i.alerts,
        }

    @staticmethod
    def _gen_id(ev: dict) -> str:
        raw = json.dumps(ev, sort_keys=True, ensure_ascii=False)
        return "evt-" + hashlib.sha1(raw.encode()).hexdigest()[:16]

    def _persist_incidents(
        self, conn: sqlite3.Connection, incidents: list[Incident]
    ) -> None:
        """持久化 incident + 关联事件

        重要（P2-2.2）：已存在的 incident 不覆盖 status / ended_at /
        acknowledged_at / resolved_at / assignee / transition_history，
        只更新关联引擎产出的元信息（severity / scope / 根因 / runbook_hint /
        alert_count）。新 incident 才以 status='open' 入库。
        """
        now = datetime.now(timezone.utc).isoformat()
        for inc in incidents:
            existing = conn.execute(
                "SELECT incident_id FROM incidents WHERE incident_id = ?",
                (inc.incident_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE incidents SET
                       started_at = ?, severity = ?, scope = ?,
                       suspected_root_cause = ?, runbook_hint = ?, alert_count = ?
                       WHERE incident_id = ?""",
                    (
                        inc.started_at,
                        inc.severity,
                        json.dumps(inc.scope, ensure_ascii=False),
                        inc.suspected_root_cause,
                        inc.runbook_hint,
                        len(inc.alert_ids),
                        inc.incident_id,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO incidents
                       (incident_id, started_at, ended_at, severity, scope,
                        suspected_root_cause, runbook_hint, alert_count, status,
                        created_at, acknowledged_at, resolved_at, assignee,
                        transition_history)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, NULL, NULL, '', '[]')""",
                    (
                        inc.incident_id,
                        inc.started_at,
                        inc.ended_at,
                        inc.severity,
                        json.dumps(inc.scope, ensure_ascii=False),
                        inc.suspected_root_cause,
                        inc.runbook_hint,
                        len(inc.alert_ids),
                        now,
                    ),
                )
            for aid in inc.alert_ids:
                conn.execute(
                    "UPDATE events SET incident_id = ? WHERE id = ?",
                    (inc.incident_id, aid),
                )
        conn.commit()


# 全局单例
_correlator: EventCorrelator | None = None


def get_event_correlator() -> EventCorrelator:
    global _correlator
    if _correlator is None:
        _correlator = EventCorrelator()
    return _correlator
