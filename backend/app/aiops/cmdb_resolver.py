"""P2-3.6 CMDB 实体归一化（IP↔主机名映射）

维护 IP 到主机名的映射表，支持：
- IP → hostname 查询（resolve）
- 短名 → FQDN 查询
- 自动从拓扑节点 metadata.ip 构建映射（auto source）
- 拓扑节点归一化：将 IP 节点名替换为 hostname，并合并重复节点

持久化到 SQLite（cmdb_mappings 表），复用 topology_builder 的 events.db。

与 topology_builder 的关系：
- CMDBResolver.normalize_topology 接收 TopologyBuilder 实例，扫描其节点并归一化
- TopologyBuilder.normalize_by_cmdb 委托给 CMDBResolver（避免循环 import，方法内导入）
- 不破坏现有 merge_aliases 的 FQDN 逻辑
"""
from __future__ import annotations

import ipaddress
import json
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import structlog

from app.aiops.topology_builder import _get_db

if TYPE_CHECKING:
    from app.aiops.topology_builder import TopologyBuilder

logger = structlog.get_logger()


def _is_ip(value: str) -> bool:
    """判断字符串是否为合法 IP 地址（v4/v6）"""
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


class CMDBResolver:
    """CMDB 实体归一化解析器

    维护 IP↔hostname 双向映射，提供实体名归一化与拓扑归一化能力。

    表结构（cmdb_mappings，复用 events.db）：
        id          INTEGER PRIMARY KEY AUTOINCREMENT
        ip          TEXT NOT NULL UNIQUE
        hostname    TEXT NOT NULL
        source      TEXT NOT NULL DEFAULT 'manual'   -- manual | auto
        created_at  TEXT NOT NULL
    """

    def __init__(self) -> None:
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """确保 cmdb_mappings 表存在（幂等）"""
        conn = _get_db()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cmdb_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL UNIQUE,
                hostname TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cmdb_ip ON cmdb_mappings(ip);
            CREATE INDEX IF NOT EXISTS idx_cmdb_hostname ON cmdb_mappings(hostname);
            """
        )
        conn.commit()

    # ────────── 实体归一化 ──────────

    def resolve(self, entity: str) -> str:
        """归一化实体名

        - 输入 IP（如 "10.0.0.1"）→ 返回对应主机名（如 "web1.example.com"）
        - 输入主机名 → 返回主机名本身（已归一化）
        - 输入短名（如 "web1"）→ 返回完整 FQDN（若有映射）
        - 无映射 → 返回原值

        Args:
            entity: 待归一化的实体名（IP / hostname / 短名）

        Returns:
            归一化后的实体名
        """
        if not entity:
            return entity

        conn = _get_db()

        # 1. IP → hostname
        if _is_ip(entity):
            r = conn.execute(
                "SELECT hostname FROM cmdb_mappings WHERE ip = ?",
                (entity,),
            ).fetchone()
            if r:
                return r["hostname"]
            return entity  # 无映射，返回原 IP

        # 2. hostname 精确匹配 → 自身（已归一化）
        r = conn.execute(
            "SELECT hostname FROM cmdb_mappings WHERE hostname = ?",
            (entity,),
        ).fetchone()
        if r:
            return r["hostname"]  # 即 entity 本身

        # 3. 短名 → FQDN（仅当 entity 不含点，视为短名）
        if "." not in entity:
            short = entity.lower()
            rows = conn.execute(
                "SELECT hostname FROM cmdb_mappings "
                "WHERE lower(hostname) = ? OR lower(hostname) LIKE ?",
                (short, short + ".%"),
            ).fetchall()
            candidates = [row["hostname"] for row in rows]
            if candidates:
                # 取最短的（最接近短名）
                return min(candidates, key=len)

        # 4. 无映射
        return entity

    # ────────── 映射管理 ──────────

    def add_mapping(
        self, ip: str, hostname: str, source: str = "manual"
    ) -> dict:
        """添加 IP↔hostname 双向映射

        若同 IP 的映射已存在：
        - hostname 相同 → 不变
        - hostname 不同 → 更新（manual 可覆盖 auto）

        Args:
            ip: IP 地址
            hostname: 主机名（FQDN 或短名）
            source: manual | auto

        Returns:
            {"ip": str, "hostname": str, "source": str, "created": bool}
        """
        if not ip or not hostname:
            raise ValueError("ip 和 hostname 不能为空")
        if not _is_ip(ip):
            raise ValueError(f"非法 IP 地址: {ip}")
        if source not in ("manual", "auto"):
            raise ValueError(f"source 必须为 manual 或 auto，实际: {source}")

        now = datetime.now(timezone.utc).isoformat()
        conn = _get_db()
        existing = conn.execute(
            "SELECT id, hostname, source FROM cmdb_mappings WHERE ip = ?",
            (ip,),
        ).fetchone()
        if existing:
            if existing["hostname"] != hostname:
                # manual 可覆盖 auto；auto 不覆盖 manual
                old_source = existing["source"]
                if source == "manual" or old_source == "auto":
                    conn.execute(
                        "UPDATE cmdb_mappings SET hostname = ?, source = ?, "
                        "created_at = ? WHERE id = ?",
                        (hostname, source, now, existing["id"]),
                    )
                    conn.commit()
                    logger.info(
                        "cmdb_mapping_updated",
                        ip=ip,
                        hostname=hostname,
                        source=source,
                    )
                return {
                    "ip": ip,
                    "hostname": hostname,
                    "source": source,
                    "created": False,
                }
            return {
                "ip": ip,
                "hostname": hostname,
                "source": existing["source"],
                "created": False,
            }

        conn.execute(
            """INSERT INTO cmdb_mappings (ip, hostname, source, created_at)
               VALUES (?, ?, ?, ?)""",
            (ip, hostname, source, now),
        )
        conn.commit()
        logger.info(
            "cmdb_mapping_added", ip=ip, hostname=hostname, source=source
        )
        return {
            "ip": ip,
            "hostname": hostname,
            "source": source,
            "created": True,
        }

    def list_mappings(self) -> list[dict]:
        """列出所有映射（按创建时间倒序）"""
        conn = _get_db()
        rows = conn.execute(
            "SELECT ip, hostname, source, created_at FROM cmdb_mappings "
            "ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "ip": r["ip"],
                "hostname": r["hostname"],
                "source": r["source"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def remove_mapping(self, ip: str) -> bool:
        """删除映射

        Returns:
            True 删除成功，False 映射不存在
        """
        conn = _get_db()
        cur = conn.execute(
            "DELETE FROM cmdb_mappings WHERE ip = ?",
            (ip,),
        )
        conn.commit()
        deleted = cur.rowcount > 0
        if deleted:
            logger.info("cmdb_mapping_removed", ip=ip)
        return deleted

    # ────────── 拓扑归一化 ──────────

    def auto_build_from_topology(self, builder: TopologyBuilder) -> int:
        """从 TopologyBuilder 节点的 metadata.ip 自动构建映射

        扫描所有节点，若 metadata.ip 存在且节点 name 不是 IP，
        则建立 ip → name 的 auto 映射（已存在同 IP 映射则跳过）。

        Returns:
            新增的 auto 映射数
        """
        topo = builder.get_topology()
        added = 0
        for node in topo["nodes"]:
            meta = node.get("metadata") or {}
            ip = meta.get("ip")
            if not ip or not _is_ip(ip):
                continue
            name = node.get("name", "")
            if not name or _is_ip(name):
                continue  # name 本身是 IP，无可映射的 hostname
            # 已存在同 IP 映射则跳过
            existing = self.resolve(ip)
            if existing != ip:
                continue
            self.add_mapping(ip, name, source="auto")
            added += 1
        return added

    def normalize_topology(self, builder: TopologyBuilder) -> dict:
        """归一化拓扑：将 IP 节点名替换为 hostname，合并重复节点

        流程：
        1. 调用 auto_build_from_topology 从 metadata.ip 构建映射
        2. 扫描所有节点，对 name 是 IP 的：
           a. resolve(ip) 得到 hostname
           b. 若 hostname != ip（有映射）：
              - 同类型下 hostname 节点已存在 → 合并（source_docs/occurrences/metadata 并集，边迁移）
              - 否则 → 仅改名（name + node_id 更新，边引用迁移）
        3. 返回统计

        Returns:
            {
                "normalized_count": int,   # 归一化的节点数（改名 + 合并）
                "merged_count": int,       # 合并的节点数
                "mappings_used": list[str] # 用到的 IP 列表
            }
        """
        # 1. 自动构建映射
        self.auto_build_from_topology(builder)

        # 2. 重新读取拓扑（auto_build 可能新增了映射）
        topo = builder.get_topology()
        mappings_used: list[str] = []
        normalized_count = 0
        merged_count = 0

        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()

        # 收集 IP → hostname 映射（基于当前节点）
        ip_to_hostname: dict[str, str] = {}
        for node in topo["nodes"]:
            name = node.get("name", "")
            if _is_ip(name):
                hostname = self.resolve(name)
                if hostname != name:
                    ip_to_hostname[name] = hostname
                    if name not in mappings_used:
                        mappings_used.append(name)

        # 应用归一化
        for ip, hostname in ip_to_hostname.items():
            # 查找 IP 节点（按 name 匹配，可能多个类型都有同名 IP 节点）
            ip_rows = conn.execute(
                "SELECT node_id, node_type, name, occurrences, source_docs, "
                "first_seen, last_seen, metadata "
                "FROM topology_nodes WHERE lower(name) = lower(?)",
                (ip,),
            ).fetchall()

            for ip_row in ip_rows:
                node_type = ip_row["node_type"]
                target_node_id = f"{node_type}:{hostname.lower()}"

                # 查找同类型下 hostname 节点是否已存在
                target_row = conn.execute(
                    "SELECT node_id, occurrences, source_docs, first_seen, "
                    "last_seen, metadata "
                    "FROM topology_nodes WHERE node_id = ?",
                    (target_node_id,),
                ).fetchone()

                if target_row:
                    # 合并：IP 节点 → hostname 节点
                    self._merge_nodes(conn, ip_row, target_row, now)
                    merged_count += 1
                    normalized_count += 1
                else:
                    # 仅改名：IP 节点 → hostname 节点
                    self._rename_node(conn, ip_row, hostname, target_node_id, now)
                    normalized_count += 1

        conn.commit()
        logger.info(
            "cmdb_topology_normalized",
            normalized_count=normalized_count,
            merged_count=merged_count,
            mappings_used=len(mappings_used),
        )
        return {
            "normalized_count": normalized_count,
            "merged_count": merged_count,
            "mappings_used": mappings_used,
        }

    def _merge_nodes(
        self,
        conn: sqlite3.Connection,
        ip_row: sqlite3.Row,
        target_row: sqlite3.Row,
        now: str,
    ) -> None:
        """把 ip_row 节点合并到 target_row 节点

        - source_docs 取并集
        - occurrences 取和
        - first_seen 取较早，last_seen 取较晚
        - metadata 合并（target 旧值优先）
        - 边迁移：所有指向/来自 IP 节点的边重定向到 target（合并重复边）
        - 删除 IP 节点
        """
        ip_node_id = ip_row["node_id"]
        target_node_id = target_row["node_id"]

        # 合并节点字段
        ip_docs = set(json.loads(ip_row["source_docs"] or "[]"))
        target_docs = set(json.loads(target_row["source_docs"] or "[]"))
        merged_docs = ip_docs | target_docs
        merged_occ = (ip_row["occurrences"] or 0) + (target_row["occurrences"] or 0)

        merged_first = ip_row["first_seen"]
        if target_row["first_seen"] and (
            not merged_first or target_row["first_seen"] < merged_first
        ):
            merged_first = target_row["first_seen"]

        merged_last = target_row["last_seen"]
        if ip_row["last_seen"] and (
            not merged_last or ip_row["last_seen"] > merged_last
        ):
            merged_last = ip_row["last_seen"]

        # metadata 合并：target 旧值优先
        ip_meta = json.loads(ip_row["metadata"] or "{}")
        target_meta = json.loads(target_row["metadata"] or "{}")
        merged_meta = {**ip_meta, **target_meta}

        conn.execute(
            "UPDATE topology_nodes SET occurrences = ?, source_docs = ?, "
            "first_seen = ?, last_seen = ?, metadata = ? WHERE node_id = ?",
            (
                merged_occ,
                json.dumps(sorted(merged_docs)),
                merged_first,
                merged_last,
                json.dumps(merged_meta, ensure_ascii=False),
                target_node_id,
            ),
        )

        # 迁移边
        edges_to_redirect = conn.execute(
            "SELECT source, target, relation, occurrences, source_docs, "
            "first_seen, last_seen, inferred, confidence "
            "FROM topology_edges WHERE source = ? OR target = ?",
            (ip_node_id, ip_node_id),
        ).fetchall()
        for e in edges_to_redirect:
            new_source = (
                target_node_id if e["source"] == ip_node_id else e["source"]
            )
            new_target = (
                target_node_id if e["target"] == ip_node_id else e["target"]
            )
            if new_source == new_target:
                continue  # 跳过自环
            existing_edge = conn.execute(
                "SELECT id, occurrences, source_docs FROM topology_edges "
                "WHERE source = ? AND target = ? AND relation = ?",
                (new_source, new_target, e["relation"]),
            ).fetchone()
            if existing_edge:
                merged_e_docs = set(
                    json.loads(existing_edge["source_docs"] or "[]")
                )
                merged_e_docs |= set(json.loads(e["source_docs"] or "[]"))
                conn.execute(
                    "UPDATE topology_edges SET occurrences = ?, source_docs = ?, "
                    "last_seen = ? WHERE id = ?",
                    (
                        (existing_edge["occurrences"] or 0)
                        + (e["occurrences"] or 0),
                        json.dumps(sorted(merged_e_docs)),
                        now,
                        existing_edge["id"],
                    ),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO topology_edges "
                    "(source, target, relation, occurrences, source_docs, "
                    " first_seen, last_seen, inferred, confidence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_source,
                        new_target,
                        e["relation"],
                        e["occurrences"],
                        e["source_docs"],
                        e["first_seen"],
                        now,
                        e["inferred"],
                        e["confidence"],
                    ),
                )
        # 删除旧边
        conn.execute(
            "DELETE FROM topology_edges WHERE source = ? OR target = ?",
            (ip_node_id, ip_node_id),
        )
        # 删除 IP 节点
        conn.execute(
            "DELETE FROM topology_nodes WHERE node_id = ?", (ip_node_id,)
        )

    def _rename_node(
        self,
        conn: sqlite3.Connection,
        ip_row: sqlite3.Row,
        new_name: str,
        new_node_id: str,
        now: str,
    ) -> None:
        """把 IP 节点改名（name + node_id 更新，边引用迁移）

        用于目标 hostname 节点不存在的情况：仅做名字替换，不合并。

        注意：调用方需确保 new_node_id 不存在（否则 PRIMARY KEY 冲突）。
        """
        ip_node_id = ip_row["node_id"]

        # 更新 name 和 node_id
        conn.execute(
            "UPDATE topology_nodes SET name = ?, node_id = ?, last_seen = ? "
            "WHERE node_id = ?",
            (new_name, new_node_id, now, ip_node_id),
        )
        # 迁移 edges 引用：旧 node_id → 新 node_id
        conn.execute(
            "UPDATE topology_edges SET source = ? WHERE source = ?",
            (new_node_id, ip_node_id),
        )
        conn.execute(
            "UPDATE topology_edges SET target = ? WHERE target = ?",
            (new_node_id, ip_node_id),
        )


# 全局单例
_resolver: CMDBResolver | None = None


def get_cmdb_resolver() -> CMDBResolver:
    """获取 CMDBResolver 全局单例"""
    global _resolver
    if _resolver is None:
        _resolver = CMDBResolver()
    return _resolver
