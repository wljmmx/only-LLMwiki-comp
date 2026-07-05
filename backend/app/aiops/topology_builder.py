"""服务拓扑自动构建（P2-4 + P2-4.1 共现推断）

从已上传文档中扫描抽取 Host/Service/Component 实体和它们之间的关系
（RUNS_ON / DEPENDS_ON / USES），聚合形成服务拓扑图。

输出：
- nodes: 节点列表（type, name, occurrences, source_docs）
- edges: 边列表（source, target, relation, occurrences, source_docs, inferred, confidence）
- 邻接关系 + 影响分析（给定服务，找上下游依赖链）

P2-4.1 共现推断：
- 基于节点 source_docs 计算两两共现强度（overlap coefficient）
- 对共现强度超过阈值的节点对，若不存在显式边，则按节点类型推断 relation
- 推断边标记 inferred=1 并附带 confidence（0~1）
- 显式抽取的边 inferred=0, confidence=1.0

持久化到 SQLite（topology_nodes / topology_edges 表），支持增量更新。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import structlog

from app.extraction.rule_extractor import RuleBasedExtractor
from app.parsers import get_parser
from app.storage import get_document_store

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "events.db"


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS topology_nodes (
            node_id TEXT PRIMARY KEY,           -- "Type:name"
            node_type TEXT NOT NULL,            -- Host|Service|Component
            name TEXT NOT NULL,
            occurrences INTEGER DEFAULT 0,
            source_docs TEXT DEFAULT '[]',      -- JSON list of doc_ids
            first_seen TEXT,
            last_seen TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_node_type ON topology_nodes(node_type);
        CREATE INDEX IF NOT EXISTS idx_node_name ON topology_nodes(name);

        CREATE TABLE IF NOT EXISTS topology_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,               -- node_id
            target TEXT NOT NULL,               -- node_id
            relation TEXT NOT NULL,             -- RUNS_ON|DEPENDS_ON|USES
            occurrences INTEGER DEFAULT 0,
            source_docs TEXT DEFAULT '[]',
            first_seen TEXT,
            last_seen TEXT,
            -- P2-4.1 共现推断字段
            inferred INTEGER DEFAULT 0,         -- 0=显式抽取, 1=共现推断
            confidence REAL DEFAULT 1.0,        -- 0~1
            UNIQUE(source, target, relation)
        );
        CREATE INDEX IF NOT EXISTS idx_edge_source ON topology_edges(source);
        CREATE INDEX IF NOT EXISTS idx_edge_target ON topology_edges(target);
        CREATE INDEX IF NOT EXISTS idx_edge_relation ON topology_edges(relation);
    """)
    # 旧库迁移：补齐 P2-4.1 新增列（必须在 idx_edge_inferred 之前）
    _migrate_topology_columns(conn)
    # idx_edge_inferred 依赖 inferred 列，迁移后再创建
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_edge_inferred ON topology_edges(inferred)"
    )


def _migrate_topology_columns(conn: sqlite3.Connection) -> None:
    """对历史 topology_edges 表补齐 P2-4.1 新增列"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(topology_edges)")}
    migrations = [
        ("inferred", "INTEGER DEFAULT 0"),
        ("confidence", "REAL DEFAULT 1.0"),
    ]
    for col, decl in migrations:
        if col not in cols:
            conn.execute(f"ALTER TABLE topology_edges ADD COLUMN {col} {decl}")
            # 已有边默认标记为显式抽取
            if col == "inferred":
                conn.execute(
                    "UPDATE topology_edges SET inferred = 0 WHERE inferred IS NULL"
                )
            elif col == "confidence":
                conn.execute(
                    "UPDATE topology_edges SET confidence = 1.0 WHERE confidence IS NULL"
                )


NODE_TYPES = ("Host", "Service", "Component")


class TopologyBuilder:
    """服务拓扑构建器"""

    def __init__(self) -> None:
        self.store = get_document_store()
        self.extractor = RuleBasedExtractor()

    def rebuild(self, max_docs: int = 100) -> dict:
        """全量重建拓扑（清空旧数据后重新扫描所有文档）

        Args:
            max_docs: 最多扫描的文档数
        """
        # 清空
        conn = _get_db()
        conn.execute("DELETE FROM topology_nodes")
        conn.execute("DELETE FROM topology_edges")
        conn.commit()
        return self._scan_and_store(conn, max_docs)

    def update(self, doc_id: str) -> dict:
        """增量更新：扫描单个文档并合并到拓扑"""
        conn = _get_db()
        return self._scan_doc_and_merge(conn, doc_id)

    def get_topology(
        self,
        node_type: str | None = None,
        relation: str | None = None,
    ) -> dict:
        """获取拓扑数据

        Args:
            node_type: 过滤节点类型（Host/Service/Component）
            relation: 过滤边类型（RUNS_ON/DEPENDS_ON/USES）
        """
        conn = _get_db()
        # 节点
        if node_type:
            node_rows = conn.execute(
                "SELECT * FROM topology_nodes WHERE node_type = ? ORDER BY occurrences DESC",
                (node_type,),
            ).fetchall()
        else:
            node_rows = conn.execute(
                "SELECT * FROM topology_nodes ORDER BY occurrences DESC"
            ).fetchall()
        nodes = []
        for r in node_rows:
            d = dict(r)
            d["source_docs"] = json.loads(d.get("source_docs") or "[]")
            nodes.append(d)

        # 边
        if relation:
            edge_rows = conn.execute(
                """SELECT * FROM topology_edges WHERE relation = ?
                   ORDER BY occurrences DESC""",
                (relation,),
            ).fetchall()
        else:
            edge_rows = conn.execute(
                "SELECT * FROM topology_edges ORDER BY occurrences DESC"
            ).fetchall()
        edges = []
        for r in edge_rows:
            d = dict(r)
            d["source_docs"] = json.loads(d.get("source_docs") or "[]")
            edges.append(d)

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "nodes": len(nodes),
                "edges": len(edges),
                "by_type": self._count_by(conn, "topology_nodes", "node_type"),
                "by_relation": self._count_by(conn, "topology_edges", "relation"),
            },
        }

    def get_neighbors(self, node_name: str, depth: int = 1) -> dict:
        """获取节点的邻居（上下游依赖）

        Args:
            node_name: 节点名称（不区分大小写）
            depth: 遍历深度（1=直接邻居，2=二级依赖）
        """
        conn = _get_db()
        # 找到节点（按 name 模糊匹配）
        node = conn.execute(
            "SELECT * FROM topology_nodes WHERE lower(name) = lower(?)",
            (node_name,),
        ).fetchone()
        if not node:
            return {"node": None, "neighbors": [], "edges": []}

        node_id = node["node_id"]
        visited: set[str] = {node_id}
        all_edges: list[dict] = []
        all_nodes: list[dict] = [dict(node)]
        current_layer = [node_id]

        for d in range(depth):
            next_layer: list[str] = []
            for nid in current_layer:
                # 出边（nid 是 source）
                for r in conn.execute(
                    "SELECT * FROM topology_edges WHERE source = ?", (nid,)
                ).fetchall():
                    target = r["target"]
                    if target not in visited:
                        visited.add(target)
                        next_layer.append(target)
                        tn = conn.execute(
                            "SELECT * FROM topology_nodes WHERE node_id = ?", (target,)
                        ).fetchone()
                        if tn:
                            all_nodes.append(dict(tn))
                    all_edges.append(dict(r))
                # 入边（nid 是 target）
                for r in conn.execute(
                    "SELECT * FROM topology_edges WHERE target = ?", (nid,)
                ).fetchall():
                    source = r["source"]
                    if source not in visited:
                        visited.add(source)
                        next_layer.append(source)
                        sn = conn.execute(
                            "SELECT * FROM topology_nodes WHERE node_id = ?", (source,)
                        ).fetchone()
                        if sn:
                            all_nodes.append(dict(sn))
                    all_edges.append(dict(r))
            current_layer = next_layer

        # 序列化 source_docs
        for n in all_nodes:
            n["source_docs"] = json.loads(n.get("source_docs") or "[]")
        for e in all_edges:
            e["source_docs"] = json.loads(e.get("source_docs") or "[]")

        # 标注方向：
        # - downstream（受我故障影响）= 指向我的节点（依赖我）
        #   即 e.target == node_id，对应 e.source
        # - upstream（潜在根因候选）= 我指向的节点（我依赖）
        #   即 e.source == node_id，对应 e.target
        downstream = [
            n
            for n in all_nodes
            if n["node_id"] != node_id
            and any(
                e["target"] == node_id and e["source"] == n["node_id"]
                for e in all_edges
            )
        ]
        upstream = [
            n
            for n in all_nodes
            if n["node_id"] != node_id
            and any(
                e["source"] == node_id and e["target"] == n["node_id"]
                for e in all_edges
            )
        ]

        return {
            "node": dict(node),
            "neighbors": all_nodes,
            "edges": all_edges,
            "upstream": upstream,
            "downstream": downstream,
            "depth": depth,
        }

    def impact_analysis(self, node_name: str) -> dict:
        """影响分析：给定节点故障，分析受影响的上游/下游

        - 下游（依赖此节点的）：直接受影响
        - 上游（此节点依赖的）：可能根因候选
        """
        neighbors = self.get_neighbors(node_name, depth=2)
        if not neighbors["node"]:
            return {"node": None, "error": f"未找到节点: {node_name}"}

        return {
            "node": neighbors["node"],
            "impacted_downstream": neighbors["downstream"],  # 我挂了，谁受影响
            "potential_root_cause": neighbors["upstream"],  # 我挂了，可能是谁的问题
            "edges": neighbors["edges"],
            "summary": {
                "impacted_count": len(neighbors["downstream"]),
                "root_cause_candidates": len(neighbors["upstream"]),
            },
        }

    # ────────── 内部实现 ──────────

    def _scan_and_store(self, conn: sqlite3.Connection, max_docs: int) -> dict:
        """扫描所有文档，构建拓扑"""
        docs = self.store.list(limit=max_docs)
        total_nodes = 0
        total_edges = 0
        scanned = 0
        for doc in docs:
            try:
                nodes, edges = self._extract_from_doc(doc)
                self._merge_to_db(conn, doc["doc_id"], nodes, edges)
                scanned += 1
                total_nodes += len(nodes)
                total_edges += len(edges)
            except Exception as e:
                logger.warning(
                    "topology_scan_failed", doc_id=doc["doc_id"], error=str(e)
                )
        logger.info(
            "topology_built",
            docs_scanned=scanned,
            nodes=total_nodes,
            edges=total_edges,
        )
        return {
            "docs_scanned": scanned,
            "nodes_extracted": total_nodes,
            "edges_extracted": total_edges,
        }

    def _scan_doc_and_merge(self, conn: sqlite3.Connection, doc_id: str) -> dict:
        """扫描单个文档并合并"""
        doc = self.store.get(doc_id)
        if not doc:
            return {"doc_id": doc_id, "error": "文档不存在"}
        try:
            nodes, edges = self._extract_from_doc(doc)
            self._merge_to_db(conn, doc_id, nodes, edges)
            return {
                "doc_id": doc_id,
                "nodes_added": len(nodes),
                "edges_added": len(edges),
            }
        except Exception as e:
            return {"doc_id": doc_id, "error": str(e)}

    def _extract_from_doc(self, doc_meta: dict) -> tuple[list[dict], list[dict]]:
        """从单个文档抽取节点和边"""
        content = self.store.read_content(doc_meta["doc_id"])
        if not content:
            return [], []
        fmt = doc_meta.get("format", "txt")
        parser = get_parser(fmt)
        parsed = parser.parse(doc_meta.get("stored_path", ""), doc_meta["doc_id"])
        entities, relations = self.extractor.extract(parsed)

        # 节点：仅保留 Host/Service/Component
        nodes = []
        for e in entities:
            if e.entity_type in NODE_TYPES:
                nodes.append(
                    {
                        "node_type": e.entity_type,
                        "name": e.name,
                    }
                )
        # 边：从抽取的 relations（DEPENDS_ON）+ 推断的 RUNS_ON/USES
        edges = []
        for r in relations:
            edges.append(
                {
                    "source": r.from_entity,
                    "target": r.to_entity,
                    "relation": r.relation_type,
                }
            )
        # 推断 RUNS_ON：service → host（基于同文档抽取）
        # 简化：每个 Service 节点与同文档 Host 节点建立 RUNS_ON 关系
        hosts = [n for n in nodes if n["node_type"] == "Host"]
        services = [n for n in nodes if n["node_type"] == "Service"]
        for s in services:
            for h in hosts:
                edges.append(
                    {
                        "source": s["name"],
                        "target": h["name"],
                        "relation": "RUNS_ON",
                    }
                )
        # 推断 USES：service → component
        components = [n for n in nodes if n["node_type"] == "Component"]
        for s in services:
            for c in components:
                edges.append(
                    {
                        "source": s["name"],
                        "target": c["name"],
                        "relation": "USES",
                    }
                )
        return nodes, edges

    def _merge_to_db(
        self,
        conn: sqlite3.Connection,
        doc_id: str,
        nodes: list[dict],
        edges: list[dict],
    ) -> None:
        """合并到数据库（增量）"""
        now = datetime.now(timezone.utc).isoformat()
        for n in nodes:
            node_id = f"{n['node_type']}:{n['name'].lower()}"
            existing = conn.execute(
                "SELECT * FROM topology_nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
            if existing:
                docs = json.loads(existing["source_docs"] or "[]")
                if doc_id not in docs:
                    docs.append(doc_id)
                conn.execute(
                    """UPDATE topology_nodes
                       SET occurrences = ?, source_docs = ?, last_seen = ?
                       WHERE node_id = ?""",
                    (existing["occurrences"] + 1, json.dumps(docs), now, node_id),
                )
            else:
                conn.execute(
                    """INSERT INTO topology_nodes
                       (node_id, node_type, name, occurrences, source_docs,
                        first_seen, last_seen)
                       VALUES (?, ?, ?, 1, ?, ?, ?)""",
                    (
                        node_id,
                        n["node_type"],
                        n["name"],
                        json.dumps([doc_id]),
                        now,
                        now,
                    ),
                )

        for e in edges:
            # 推断 source/target 的 node_id（按 name 匹配）
            src_id = self._find_node_id(conn, e["source"])
            tgt_id = self._find_node_id(conn, e["target"])
            if not src_id or not tgt_id:
                continue
            existing = conn.execute(
                """SELECT * FROM topology_edges
                   WHERE source = ? AND target = ? AND relation = ?""",
                (src_id, tgt_id, e["relation"]),
            ).fetchone()
            if existing:
                docs = json.loads(existing["source_docs"] or "[]")
                if doc_id not in docs:
                    docs.append(doc_id)
                conn.execute(
                    """UPDATE topology_edges
                       SET occurrences = ?, source_docs = ?, last_seen = ?
                       WHERE id = ?""",
                    (
                        existing["occurrences"] + 1,
                        json.dumps(docs),
                        now,
                        existing["id"],
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO topology_edges
                       (source, target, relation, occurrences, source_docs,
                        first_seen, last_seen, inferred, confidence)
                       VALUES (?, ?, ?, 1, ?, ?, ?, 0, 1.0)""",
                    (src_id, tgt_id, e["relation"], json.dumps([doc_id]), now, now),
                )
        conn.commit()

    # ────────── P2-4.1 共现推断 ──────────

    def infer_cooccurrence_edges(
        self,
        min_cooccurrence: int = 2,
        min_confidence: float = 0.3,
    ) -> dict:
        """基于节点 source_docs 共现强度推断缺失的拓扑边（P2-4.1）

        算法：
        1. 从 DB 读取所有 topology_nodes 的 (node_id, node_type, source_docs)
        2. 对节点两两组合计算共现文档数 |A∩B| 与 overlap coefficient
           overlap = |A∩B| / min(|A|, |B|)   # 对小集合敏感
        3. 共现文档数 >= min_cooccurrence 且 overlap >= min_confidence 的节点对：
           - 若不存在显式边（inferred=0），按节点类型推断 relation：
             Service → Host        : RUNS_ON
             Service → Component   : USES
             同类型节点对           : DEPENDS_ON
           - 插入 inferred=1, confidence=overlap 的边
        4. 返回推断结果统计

        Args:
            min_cooccurrence: 共现文档数下限（默认 2）
            min_confidence: overlap coefficient 下限（默认 0.3）

        Returns:
            {
                "considered_pairs": int,  # 评估的节点对数
                "inferred_edges": int,    # 实际新增的推断边数
                "skipped_existing": int,  # 已存在显式边被跳过的对数
                "edges": [...]            # 新增的边列表
            }
        """
        conn = _get_db()
        rows = conn.execute(
            "SELECT node_id, node_type, source_docs FROM topology_nodes"
        ).fetchall()
        if len(rows) < 2:
            return {
                "considered_pairs": 0,
                "inferred_edges": 0,
                "skipped_existing": 0,
                "edges": [],
            }

        # 解析每个节点的 source_docs 为 set
        nodes_info: list[tuple[str, str, set[str]]] = []
        for r in rows:
            docs = set(json.loads(r["source_docs"] or "[]"))
            nodes_info.append((r["node_id"], r["node_type"], docs))

        # 收集已存在的显式边集合（按 (source, target) 无向去重判断）
        existing_edges = set()
        for er in conn.execute("SELECT source, target FROM topology_edges").fetchall():
            existing_edges.add((er["source"], er["target"]))
            existing_edges.add((er["target"], er["source"]))  # 双向都视为已存在

        now = datetime.now(timezone.utc).isoformat()
        inferred_edges: list[dict] = []
        skipped = 0
        considered = 0

        for (id_a, type_a, docs_a), (id_b, type_b, docs_b) in combinations(
            nodes_info, 2
        ):
            intersection = docs_a & docs_b
            cooccur = len(intersection)
            if cooccur < min_cooccurrence:
                continue
            considered += 1
            min_size = min(len(docs_a), len(docs_b))
            if min_size == 0:
                continue
            overlap = cooccur / min_size
            if overlap < min_confidence:
                continue
            # 已存在显式边则跳过
            if (id_a, id_b) in existing_edges:
                skipped += 1
                continue

            relation = self._infer_relation(type_a, type_b)
            if relation is None:
                continue
            # 约定方向：source 为 Service/主动方，target 为 Host/Component/被动方
            source_id, target_id = id_a, id_b
            if relation == "RUNS_ON":
                # Service → Host
                if type_b == "Service" and type_a == "Host":
                    source_id, target_id = id_b, id_a
            elif relation == "USES":
                # Service → Component
                if type_b == "Service" and type_a == "Component":
                    source_id, target_id = id_b, id_a

            edge_doc = {
                "source": source_id,
                "target": target_id,
                "relation": relation,
                "inferred": 1,
                "confidence": round(overlap, 4),
                "cooccurrence": cooccur,
                "source_docs": sorted(intersection),
            }
            inferred_edges.append(edge_doc)

            conn.execute(
                """INSERT OR IGNORE INTO topology_edges
                   (source, target, relation, occurrences, source_docs,
                    first_seen, last_seen, inferred, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    source_id,
                    target_id,
                    relation,
                    cooccur,
                    json.dumps(sorted(intersection)),
                    now,
                    now,
                    round(overlap, 4),
                ),
            )
            # 标记已存在以避免本批次重复推断
            existing_edges.add((source_id, target_id))
            existing_edges.add((target_id, source_id))

        conn.commit()
        logger.info(
            "topology_inferred",
            considered_pairs=considered,
            inferred_edges=len(inferred_edges),
            skipped_existing=skipped,
        )
        return {
            "considered_pairs": considered,
            "inferred_edges": len(inferred_edges),
            "skipped_existing": skipped,
            "edges": inferred_edges,
        }

    @staticmethod
    def _infer_relation(type_a: str, type_b: str) -> str | None:
        """根据两个节点的类型推断 relation，无法推断则返回 None"""
        pair = {type_a, type_b}
        if pair == {"Service", "Host"}:
            return "RUNS_ON"
        if pair == {"Service", "Component"}:
            return "USES"
        if type_a == type_b:
            # 同类型节点（Host-Host / Service-Service / Component-Component）
            return "DEPENDS_ON"
        # Host-Component 等组合不推断
        return None

    @staticmethod
    def _find_node_id(conn: sqlite3.Connection, name: str) -> str | None:
        """按 name 找 node_id（不区分大小写）"""
        if not name:
            return None
        r = conn.execute(
            "SELECT node_id FROM topology_nodes WHERE lower(name) = lower(?)",
            (name,),
        ).fetchone()
        return r["node_id"] if r else None

    @staticmethod
    def _count_by(conn: sqlite3.Connection, table: str, col: str) -> dict:
        rows = conn.execute(
            f"SELECT {col} as k, COUNT(*) as v FROM {table} GROUP BY {col}"
        ).fetchall()
        return {r["k"]: r["v"] for r in rows}


# 全局单例
_builder: TopologyBuilder | None = None


def get_topology_builder() -> TopologyBuilder:
    global _builder
    if _builder is None:
        _builder = TopologyBuilder()
    return _builder
