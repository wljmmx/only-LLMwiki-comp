"""知识图谱 Neo4j 存储服务（W5）

提供实体/关系的写入、查询、去重能力。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from neo4j import GraphDatabase, Driver
from neo4j.exceptions import Neo4jError
import structlog

from app.config import get_settings

logger = structlog.get_logger()


@dataclass
class GraphEntity:
    """图谱实体"""

    entity_type: str
    name: str
    properties: dict[str, Any] = field(default_factory=dict)
    source_doc_id: str = ""
    confidence: float = 0.0


@dataclass
class GraphRelation:
    """图谱关系"""

    relation_type: str
    from_entity: str
    to_entity: str
    properties: dict[str, Any] = field(default_factory=dict)
    source_doc_id: str = ""
    confidence: float = 0.0


class GraphStore:
    """Neo4j 知识图谱存储"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._driver: Driver | None = None

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_user, self.settings.neo4j_password),
            )
            self._init_indexes()
        return self._driver

    def _init_indexes(self) -> None:
        """创建必要的索引和约束"""
        with self.driver.session() as session:
            indexes = [
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Entity) REQUIRE n.name IS UNIQUE",
                "CREATE INDEX IF NOT EXISTS FOR (n:Entity) ON (n.entity_type)",
                "CREATE INDEX IF NOT EXISTS FOR (n:Entity) ON (n.source_doc_id)",
            ]
            for idx in indexes:
                try:
                    session.run(idx)
                except Neo4jError as e:
                    logger.warning("neo4j_index_error", error=str(e))

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None

    # ── 写入 ──

    def upsert_entity(self, entity: GraphEntity) -> dict:
        """创建或更新实体节点"""
        with self.driver.session() as session:
            result = session.run(
                """
                MERGE (n:Entity {name: $name})
                SET n.entity_type = $entity_type,
                    n += $properties,
                    n.source_doc_id = $source_doc_id,
                    n.confidence = $confidence,
                    n.updated_at = datetime()
                RETURN n.name AS name, n.entity_type AS type
                """,
                name=entity.name,
                entity_type=entity.entity_type,
                properties=entity.properties,
                source_doc_id=entity.source_doc_id,
                confidence=entity.confidence,
            )
            record = result.single()
            return dict(record) if record else {}

    def upsert_relation(self, rel: GraphRelation) -> dict:
        """创建或更新关系"""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (a:Entity {name: $from_name})
                MATCH (b:Entity {name: $to_name})
                MERGE (a)-[r:$rel_type]->(b)
                SET r += $properties,
                    r.source_doc_id = $source_doc_id,
                    r.confidence = $confidence,
                    r.updated_at = datetime()
                RETURN type(r) AS type, a.name AS from, b.name AS to
                """,
                from_name=rel.from_entity,
                to_name=rel.to_entity,
                rel_type=rel.relation_type,
                properties=rel.properties,
                source_doc_id=rel.source_doc_id,
                confidence=rel.confidence,
            )
            record = result.single()
            return dict(record) if record else {}

    def batch_upsert(
        self,
        entities: list[GraphEntity],
        relations: list[GraphRelation],
    ) -> dict:
        """批量写入实体和关系"""
        entity_count = 0
        rel_count = 0
        errors = []

        for entity in entities:
            try:
                self.upsert_entity(entity)
                entity_count += 1
            except Exception as e:
                errors.append(f"entity:{entity.name}:{e}")

        for rel in relations:
            try:
                self.upsert_relation(rel)
                rel_count += 1
            except Exception as e:
                errors.append(f"relation:{rel.relation_type}:{e}")

        logger.info(
            "graph_batch_upsert",
            entities=entity_count,
            relations=rel_count,
            errors=len(errors),
        )
        return {
            "entities_written": entity_count,
            "relations_written": rel_count,
            "errors": errors,
        }

    # ── 查询 ──

    def query_entity(self, name: str) -> dict | None:
        """查询单个实体"""
        with self.driver.session() as session:
            result = session.run(
                "MATCH (n:Entity {name: $name}) RETURN properties(n) AS props",
                name=name,
            )
            record = result.single()
            return dict(record["props"]) if record else None

    def query_related(self, name: str, depth: int = 1) -> list[dict]:
        """查询实体的一跳邻居"""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (n:Entity {name: $name})-[r]-(m:Entity)
                RETURN n.name AS source, type(r) AS relation, m.name AS target,
                       m.entity_type AS target_type, r.confidence AS confidence
                LIMIT 100
                """,
                name=name,
            )
            return [dict(record) for record in result]

    def query_by_type(self, entity_type: str, limit: int = 50) -> list[dict]:
        """按类型查询实体"""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (n:Entity {entity_type: $type})
                RETURN n.name AS name, n.entity_type AS type, n.confidence AS confidence
                ORDER BY n.confidence DESC
                LIMIT $limit
                """,
                type=entity_type,
                limit=limit,
            )
            return [dict(record) for record in result]

    def search_entities(self, keyword: str, limit: int = 20) -> list[dict]:
        """模糊搜索实体"""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (n:Entity)
                WHERE n.name CONTAINS $keyword OR n.entity_type CONTAINS $keyword
                RETURN n.name AS name, n.entity_type AS type, n.confidence AS confidence
                LIMIT $limit
                """,
                keyword=keyword,
                limit=limit,
            )
            return [dict(record) for record in result]

    def get_stats(self) -> dict:
        """获取图谱统计"""
        with self.driver.session() as session:
            entities = session.run("MATCH (n:Entity) RETURN count(n) AS total").single()
            relations = session.run(
                "MATCH ()-[r]->() RETURN count(r) AS total"
            ).single()
            by_type = session.run(
                """
                MATCH (n:Entity)
                RETURN n.entity_type AS type, count(n) AS count
                ORDER BY count DESC
                """
            )
            return {
                "total_entities": entities["total"] if entities else 0,
                "total_relations": relations["total"] if relations else 0,
                "by_type": [dict(record) for record in by_type],
            }


# 全局单例
_graph_store: GraphStore | None = None


def get_graph_store() -> GraphStore:
    global _graph_store
    if _graph_store is None:
        _graph_store = GraphStore()
    return _graph_store
