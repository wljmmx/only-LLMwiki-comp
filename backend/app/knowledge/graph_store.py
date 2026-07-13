"""知识图谱 Neo4j 存储服务（W5）

提供实体/关系的写入、查询、去重能力。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError
from neo4j.time import Date, DateTime, Duration, Time

from app.config import get_settings

logger = structlog.get_logger()


def _to_jsonable(obj: Any) -> Any:
    """递归将 Neo4j 返回值转换为 JSON 可序列化的 Python 原生类型。

    问题背景：Neo4j Cypher 的 `datetime()` / `date()` / `time()` 函数写入的
    temporal 属性，被 Python 驱动读回时返回 `neo4j.time.DateTime` 等自定义类型，
    这些类型既不可被 `json.dumps` 序列化（抛 TypeError），也不被 FastAPI 的
    `jsonable_encoder` 正确处理（DateTime 抛 ValueError，Date/Time 返回乱码 dict
    如 `{'_Date__ordinal': ...}`）。

    本函数将：
    - `neo4j.time.DateTime` / `Date` / `Time` → ISO 8601 字符串
    - `neo4j.time.Duration` → ISO 8601 duration 字符串（如 "P1DT1H"）
    - dict / list / tuple → 递归转换

    Args:
        obj: Neo4j 查询返回的任意值（含 properties() 提取的 dict）

    Returns:
        JSON 可序列化的 Python 原生类型
    """
    if isinstance(obj, (DateTime, Date, Time)):
        return obj.isoformat()
    if isinstance(obj, Duration):
        # Duration 无 isoformat()，str() 返回 ISO 8601 duration（如 "P1DT1H"）
        # 不用默认 json.dumps，否则会变成乱码数组 [years, months, days, seconds]
        return str(obj)
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


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
        # 只读查询的 TTL 缓存，减少重复 Neo4j 查询开销
        # key → (expires_at, value)；写操作全量失效（一致性优先于性能）
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_ttl: float = self.settings.graph_cache_ttl
        self._cache_lock = threading.Lock()

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

    # ── 缓存层（TTL，线程安全）──

    def _cache_get(self, key: str) -> Any | None:
        """读取缓存：返回未过期的值；过期则清理并返回 None（视为未命中）"""
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() >= expires_at:
                # 已过期，清理并视为未命中
                del self._cache[key]
                return None
            return value

    def _cache_set(self, key: str, value: Any) -> None:
        """写入缓存：(now + ttl, value)"""
        with self._cache_lock:
            self._cache[key] = (time.time() + self._cache_ttl, value)

    def _cache_invalidate(self, pattern: str | None = None) -> None:
        """失效缓存：pattern=None 全清，否则按前缀清空（str.startswith）"""
        with self._cache_lock:
            if pattern is None:
                cleared = len(self._cache)
                self._cache.clear()
            else:
                keys_to_del = [k for k in self._cache if k.startswith(pattern)]
                for k in keys_to_del:
                    del self._cache[k]
                cleared = len(keys_to_del)
        logger.debug("graph_cache_invalidate", pattern=pattern, cleared=cleared)

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
            data = _to_jsonable(dict(record)) if record else {}
        # 写操作成功后全量失效缓存（一致性优先于性能）
        self._cache_invalidate()
        return data

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
            data = _to_jsonable(dict(record)) if record else {}
        # 写操作成功后全量失效缓存（一致性优先于性能）
        self._cache_invalidate()
        return data

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
        cache_key = f"query_entity:{name}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("graph_cache_hit", key=cache_key)
            return cached
        logger.debug("graph_cache_miss", key=cache_key)
        with self.driver.session() as session:
            result = session.run(
                "MATCH (n:Entity {name: $name}) RETURN properties(n) AS props",
                name=name,
            )
            record = result.single()
            # properties(n) 含 updated_at（Cypher datetime() 写入的 neo4j.time.DateTime）
            # 必须经 _to_jsonable 转换，否则 FastAPI 响应序列化失败
            data = _to_jsonable(dict(record["props"])) if record else None
        self._cache_set(cache_key, data)
        return data

    def query_related(self, name: str, depth: int = 1) -> list[dict]:
        """查询实体的一跳邻居"""
        cache_key = f"query_related:{name}:{depth}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("graph_cache_hit", key=cache_key)
            return cached
        logger.debug("graph_cache_miss", key=cache_key)
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
            data = [_to_jsonable(dict(record)) for record in result]
        self._cache_set(cache_key, data)
        return data

    def query_by_type(self, entity_type: str, limit: int = 50) -> list[dict]:
        """按类型查询实体"""
        cache_key = f"query_by_type:{entity_type}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("graph_cache_hit", key=cache_key)
            return cached
        logger.debug("graph_cache_miss", key=cache_key)
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
            data = [_to_jsonable(dict(record)) for record in result]
        self._cache_set(cache_key, data)
        return data

    def search_entities(self, keyword: str, limit: int = 20) -> list[dict]:
        """模糊搜索实体"""
        cache_key = f"search_entities:{keyword}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("graph_cache_hit", key=cache_key)
            return cached
        logger.debug("graph_cache_miss", key=cache_key)
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
            data = [_to_jsonable(dict(record)) for record in result]
        self._cache_set(cache_key, data)
        return data

    # ── GS-4: GDS 图算法 ──

    def pagerank(self, limit: int = 20) -> list[dict]:
        """GS-4: PageRank 计算实体重要性

        Neo4j GDS 库不可用时，降级为简单度中心性（degree centrality）。
        """
        try:
            with self.driver.session() as session:
                # 尝试 GDS PageRank
                try:
                    session.run(
                        """
                        CALL gds.graph.exists('entity-graph')
                        YIELD exists
                        """
                    )
                    result = session.run(
                        """
                        CALL gds.pageRank.stream('entity-graph', {
                            maxIterations: 20,
                            dampingFactor: 0.85
                        })
                        YIELD nodeId, score
                        WITH gds.util.asNode(nodeId) AS n, score
                        RETURN n.name AS name, n.entity_type AS type, score
                        ORDER BY score DESC
                        LIMIT $limit
                        """,
                        limit=limit,
                    )
                    return [_to_jsonable(dict(r)) for r in result]
                except Exception:
                    pass  # GDS 不可用，降级

                # 降级：简单度中心性
                result = session.run(
                    """
                    MATCH (n:Entity)
                    OPTIONAL MATCH (n)-[r]-()
                    RETURN n.name AS name, n.entity_type AS type,
                           count(r) AS degree
                    ORDER BY degree DESC
                    LIMIT $limit
                    """,
                    limit=limit,
                )
                records = [_to_jsonable(dict(r)) for r in result]
                max_degree = max((r["degree"] for r in records), default=1)
                return [
                    {
                        "name": r["name"],
                        "type": r["type"],
                        "score": round(r["degree"] / max_degree, 4),
                        "method": "degree_centrality",
                    }
                    for r in records
                ]
        except Exception as e:
            logger.warning("gds_pagerank_failed", error=str(e))
            return []

    def community_detect(self, limit: int = 10) -> list[dict]:
        """GS-4: 社区检测（Louvain 近似）

        识别紧密关联的实体群组，用于知识组织和冲突检测。
        GDS 不可用时降级为连通分量（connected components）。
        """
        try:
            with self.driver.session() as session:
                # 尝试 GDS Louvain
                try:
                    result = session.run(
                        """
                        CALL gds.louvain.stream('entity-graph', {
                            maxIterations: 10,
                            tolerance: 0.0001
                        })
                        YIELD nodeId, communityId, intermediateCommunityIds
                        WITH gds.util.asNode(nodeId) AS n, communityId
                        RETURN communityId AS community, collect(n.name)[0..5] AS members,
                               count(*) AS size
                        ORDER BY size DESC
                        LIMIT $limit
                        """,
                        limit=limit,
                    )
                    return [_to_jsonable(dict(r)) for r in result]
                except Exception:
                    pass  # GDS 不可用

                # 降级：连通分量（WCC）
                result = session.run(
                    """
                    MATCH (n:Entity)-[*1..3]-(m:Entity)
                    WITH n, collect(DISTINCT m.name) AS neighbors
                    WITH collect(DISTINCT n.name) AS all_nodes, size(neighbors) AS s
                    RETURN "graph" AS community, all_nodes[0..10] AS members, s AS size
                    LIMIT $limit
                    """,
                    limit=limit,
                )
                return [_to_jsonable(dict(r)) for r in result]
        except Exception as e:
            logger.warning("gds_community_failed", error=str(e))
            return []

    def node_similarity(self, entity_name: str, limit: int = 5) -> list[dict]:
        """GS-4: 节点相似度 — 基于图结构的相似实体发现

        通过共享邻居计算 Jaccard 相似度。
        用于 wiki 合并时的候选发现。
        """
        try:
            with self.driver.session() as session:
                # 尝试 GDS NodeSimilarity
                try:
                    result = session.run(
                        """
                        CALL gds.nodeSimilarity.stream('entity-graph', {
                            topK: $limit
                        })
                        YIELD node1, node2, similarity
                        WITH gds.util.asNode(node1) AS a, gds.util.asNode(node2) AS b, similarity
                        WHERE a.name = $name
                        RETURN b.name AS name, b.entity_type AS type, similarity AS score
                        ORDER BY score DESC
                        LIMIT $limit
                        """,
                        name=entity_name,
                        limit=limit,
                    )
                    return [_to_jsonable(dict(r)) for r in result]
                except Exception:
                    pass  # GDS 不可用

                # 降级：共享邻居 Jaccard
                result = session.run(
                    """
                    MATCH (a:Entity {name: $name})-[r1]-(neighbor)
                    WITH a, collect(DISTINCT neighbor) AS a_neighbors
                    MATCH (b:Entity)-[r2]-(neighbor)
                    WHERE b.name <> $name
                    WITH a, a_neighbors, b, collect(DISTINCT neighbor) AS b_neighbors
                    WITH b,
                         size([n IN a_neighbors WHERE n IN b_neighbors]) AS shared,
                         size(a_neighbors) + size(b_neighbors) - size([n IN a_neighbors WHERE n IN b_neighbors]) AS total
                    WHERE total > 0
                    RETURN b.name AS name, b.entity_type AS type,
                           round(toFloat(shared) / total, 4) AS score
                    ORDER BY score DESC
                    LIMIT $limit
                    """,
                    name=entity_name,
                    limit=limit,
                )
                return [_to_jsonable(dict(r)) for r in result]
        except Exception as e:
            logger.warning("gds_node_similarity_failed", error=str(e))
            return []

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
                "by_type": [_to_jsonable(dict(record)) for record in by_type],
            }


# 全局单例
_graph_store: GraphStore | None = None


def get_graph_store() -> GraphStore:
    global _graph_store
    if _graph_store is None:
        _graph_store = GraphStore()
    return _graph_store
