"""审查队列持久化（W8）

基于 SQLite 的轻量级审查队列，存储待人工审查的抽取结果。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import structlog

from app.knowledge.graph_store import GraphEntity, GraphRelation

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "review_queue.db"


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL CHECK(item_type IN ('entity', 'relation')),
            entity_type TEXT,
            name TEXT,
            relation_type TEXT,
            from_entity TEXT,
            to_entity TEXT,
            properties TEXT DEFAULT '{}',
            confidence REAL DEFAULT 0.0,
            evidence_span TEXT DEFAULT '',
            source_doc_id TEXT DEFAULT '',
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected', 'modified')),
            modified_data TEXT,
            reviewer_note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_review_status ON review_items(status);
        CREATE INDEX IF NOT EXISTS idx_review_doc ON review_items(source_doc_id);
        CREATE INDEX IF NOT EXISTS idx_review_type ON review_items(item_type);
    """)


class ReviewQueue:
    """审查队列"""

    def add_entity(
        self,
        entity_type: str,
        name: str,
        properties: dict,
        confidence: float,
        evidence: str,
        source_doc_id: str,
    ) -> int:
        """添加待审查实体"""
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """INSERT INTO review_items
               (item_type, entity_type, name, properties, confidence,
                evidence_span, source_doc_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "entity",
                entity_type,
                name,
                json.dumps(properties, ensure_ascii=False),
                confidence,
                evidence,
                source_doc_id,
                now,
                now,
            ),
        )
        conn.commit()
        return cursor.lastrowid

    def add_relation(
        self,
        relation_type: str,
        from_entity: str,
        to_entity: str,
        properties: dict,
        confidence: float,
        evidence: str,
        source_doc_id: str,
    ) -> int:
        """添加待审查关系"""
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """INSERT INTO review_items
               (item_type, relation_type, from_entity, to_entity, properties,
                confidence, evidence_span, source_doc_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "relation",
                relation_type,
                from_entity,
                to_entity,
                json.dumps(properties, ensure_ascii=False),
                confidence,
                evidence,
                source_doc_id,
                now,
                now,
            ),
        )
        conn.commit()
        return cursor.lastrowid

    def batch_add(self, entities: list[dict], relations: list[dict]) -> dict:
        """批量添加审查项"""
        e_count = 0
        r_count = 0
        for e in entities:
            try:
                self.add_entity(
                    entity_type=e.get("entity_type", "Concept"),
                    name=e.get("name", ""),
                    properties=e.get("properties", {}),
                    confidence=e.get("confidence", 0.0),
                    evidence=e.get("evidence_span", ""),
                    source_doc_id=e.get("source_doc_id", ""),
                )
                e_count += 1
            except Exception as ex:
                logger.error("review_add_entity_failed", error=str(ex))
        for r in relations:
            try:
                self.add_relation(
                    relation_type=r.get("relation_type", "RELATED_TO"),
                    from_entity=r.get("from_entity", ""),
                    to_entity=r.get("to_entity", ""),
                    properties=r.get("properties", {}),
                    confidence=r.get("confidence", 0.0),
                    evidence=r.get("evidence_span", ""),
                    source_doc_id=r.get("source_doc_id", ""),
                )
                r_count += 1
            except Exception as ex:
                logger.error("review_add_relation_failed", error=str(ex))
        return {"entities_added": e_count, "relations_added": r_count}

    def list_pending(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """列出待审查项"""
        conn = _get_db()
        rows = conn.execute(
            """SELECT * FROM review_items WHERE status = 'pending'
               ORDER BY confidence DESC, created_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_id(self, item_id: int) -> dict | None:
        """按 ID 查询"""
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM review_items WHERE id = ?", (item_id,)
        ).fetchone()
        return dict(row) if row else None

    def approve(self, item_id: int, note: str = "") -> bool:
        """批准并回写知识图谱"""
        item = self.get_by_id(item_id)
        if not item:
            return False

        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE review_items SET status = 'approved', reviewer_note = ?, updated_at = ? WHERE id = ?",
            (note, now, item_id),
        )
        conn.commit()

        # 回写知识图谱
        self._writeback_to_graph(item)
        return True

    def reject(self, item_id: int, note: str = "") -> bool:
        """驳回（不写入图谱）"""
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE review_items SET status = 'rejected', reviewer_note = ?, updated_at = ? WHERE id = ?",
            (note, now, item_id),
        )
        conn.commit()
        return conn.total_changes > 0

    def modify(self, item_id: int, modified_data: dict, note: str = "") -> bool:
        """修改后批准并回写图谱"""
        item = self.get_by_id(item_id)
        if not item:
            return False

        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE review_items
               SET status = 'modified', modified_data = ?, reviewer_note = ?, updated_at = ?
               WHERE id = ?""",
            (json.dumps(modified_data, ensure_ascii=False), note, now, item_id),
        )
        conn.commit()

        # 合并修改数据后回写图谱
        merged = {**item, **modified_data}
        self._writeback_to_graph(merged)
        return True

    def _writeback_to_graph(self, item: dict) -> None:
        """将审查项回写知识图谱"""
        try:
            from app.knowledge.graph_store import get_graph_store

            store = get_graph_store()

            props = json.loads(item.get("properties", "{}"))
            props["review_status"] = "approved"
            props["reviewer_note"] = item.get("reviewer_note", "")
            props["source_type"] = "review_approved"

            if item["item_type"] == "entity":
                entity = GraphEntity(
                    entity_type=item.get("entity_type", "Concept"),
                    name=item.get("name", ""),
                    properties=props,
                    source_doc_id=item.get("source_doc_id", ""),
                    confidence=item.get("confidence", 0.0),
                )
                store.upsert_entity(entity)
                logger.info("review_writeback_entity", name=entity.name)
            elif item["item_type"] == "relation":
                rel = GraphRelation(
                    relation_type=item.get("relation_type", "RELATED_TO"),
                    from_entity=item.get("from_entity", ""),
                    to_entity=item.get("to_entity", ""),
                    properties=props,
                    source_doc_id=item.get("source_doc_id", ""),
                    confidence=item.get("confidence", 0.0),
                )
                store.upsert_relation(rel)
                logger.info(
                    "review_writeback_relation", from_=rel.from_entity, to=rel.to_entity
                )
        except Exception as e:
            logger.error(
                "review_writeback_failed", item_id=item.get("id"), error=str(e)
            )

    def batch_approve(self, item_ids: list[int]) -> int:
        """批量批准"""
        count = 0
        for item_id in item_ids:
            if self.approve(item_id):
                count += 1
        return count

    def get_stats(self) -> dict:
        """审查队列统计"""
        conn = _get_db()
        stats = {}
        for status in ("pending", "approved", "rejected", "modified"):
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM review_items WHERE status = ?", (status,)
            ).fetchone()
            stats[status] = row["cnt"] if row else 0
        return stats


# 全局单例
_queue: ReviewQueue | None = None


def get_review_queue() -> ReviewQueue:
    global _queue
    if _queue is None:
        _queue = ReviewQueue()
    return _queue
