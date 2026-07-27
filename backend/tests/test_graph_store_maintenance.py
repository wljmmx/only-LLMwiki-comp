"""GraphStore 维护方法单元测试

验证内容：
1. query_orphan_entities：查询无关系的实体（含缓存）
2. query_low_confidence_entities：按阈值过滤低置信度实体
3. query_entities_by_source：按 source_doc_id 查询
4. query_duplicate_entities：查询同名称不同源的重复实体
5. delete_entity：删除实体及其关系，触发缓存失效
6. batch_delete_entities：批量删除（含成功/失败计数）
7. delete_relations_by_source：按源文档删除关系
8. delete_by_source：组合删除（关系 + 实体）
9. cleanup_low_confidence：清理低置信度实体
10. cleanup_orphan_entities：清理孤立实体
11. 写操作触发缓存失效：删除后查询重新走 Neo4j

使用 _FakeDriver / _FakeSession 模拟 Neo4j，不连接真实服务。
"""
from __future__ import annotations

import threading

from app.config import get_settings
from app.knowledge.graph_store import GraphStore

# ────────── Fake Neo4j 驱动层（与 test_graph_store_cache.py 一致）──────────


class _FakeRecord:
    def __init__(self, data: dict) -> None:
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def get(self, key, default=None):
        return self._data.get(key, default)


class _FakeResult:
    def __init__(self, single_record=None, records=None) -> None:
        self._single = single_record
        self._records = list(records) if records is not None else []

    def single(self):
        return self._single

    def peek(self):
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


class _FakeSession:
    def __init__(self, run_handler) -> None:
        self._handler = run_handler
        self.run_count = 0
        self.last_query: str = ""
        self.last_params: dict = {}

    def run(self, query, **params):
        self.run_count += 1
        self.last_query = query
        self.last_params = params
        return self._handler(query, params)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeDriver:
    def __init__(self, run_handler) -> None:
        self._handler = run_handler
        self.sessions: list[_FakeSession] = []

    def session(self) -> _FakeSession:
        s = _FakeSession(self._handler)
        self.sessions.append(s)
        return s

    def close(self) -> None:
        pass


def _make_store(driver: _FakeDriver, ttl: float = 30.0) -> GraphStore:
    """构造绕过真实 Neo4j 连接的 GraphStore"""
    store = GraphStore.__new__(GraphStore)
    store.settings = get_settings()
    store._driver = driver
    store._cache = {}
    store._cache_ttl = ttl
    store._cache_lock = threading.Lock()
    return store


# ────────── 1. query_orphan_entities ──────────


def test_query_orphan_entities_returns_list() -> None:
    """query_orphan_entities 返回孤立实体列表"""
    def handler(query, params):
        return _FakeResult(records=[
            _FakeRecord({"name": "orphan-1", "type": "Service", "confidence": 0.3,
                         "source_doc_id": "doc-A"}),
            _FakeRecord({"name": "orphan-2", "type": "Concept", "confidence": 0.4,
                         "source_doc_id": "doc-B"}),
        ])

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.query_orphan_entities(limit=100)
    assert len(result) == 2
    assert result[0]["name"] == "orphan-1"
    assert result[0]["type"] == "Service"
    assert result[1]["name"] == "orphan-2"


def test_query_orphan_entities_uses_cache() -> None:
    """query_orphan_entities 二次调用走缓存（session.run 仅 1 次）"""
    def handler(query, params):
        return _FakeResult(records=[
            _FakeRecord({"name": "orphan", "type": "Service", "confidence": 0.3,
                         "source_doc_id": "doc-A"}),
        ])

    driver = _FakeDriver(handler)
    store = _make_store(driver, ttl=30.0)

    store.query_orphan_entities(limit=100)
    store.query_orphan_entities(limit=100)

    # 第二次走缓存
    assert sum(s.run_count for s in driver.sessions) == 1


# ────────── 2. query_low_confidence_entities ──────────


def test_query_low_confidence_entities_returns_below_threshold() -> None:
    """query_low_confidence_entities 返回低于阈值的实体"""
    def handler(query, params):
        # 验证参数传递
        assert params["threshold"] == 0.5
        assert params["limit"] == 200
        return _FakeResult(records=[
            _FakeRecord({"name": "low-1", "type": "Service", "confidence": 0.2,
                         "source_doc_id": "doc-A"}),
        ])

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.query_low_confidence_entities(threshold=0.5, limit=200)
    assert len(result) == 1
    assert result[0]["name"] == "low-1"
    assert result[0]["confidence"] == 0.2


def test_query_low_confidence_entities_uses_cache() -> None:
    """相同参数二次调用走缓存"""
    call_count = [0]

    def handler(query, params):
        call_count[0] += 1
        return _FakeResult(records=[
            _FakeRecord({"name": "low", "type": "Service", "confidence": 0.2,
                         "source_doc_id": "doc-A"}),
        ])

    driver = _FakeDriver(handler)
    store = _make_store(driver, ttl=30.0)

    store.query_low_confidence_entities(threshold=0.5, limit=200)
    store.query_low_confidence_entities(threshold=0.5, limit=200)

    assert call_count[0] == 1  # 第二次走缓存


# ────────── 3. query_entities_by_source ──────────


def test_query_entities_by_source_returns_entities() -> None:
    """query_entities_by_source 按 source_doc_id 查询"""
    def handler(query, params):
        assert params["doc_id"] == "doc-A"
        return _FakeResult(records=[
            _FakeRecord({"name": "ent-1", "type": "Service", "confidence": 0.9,
                         "source_doc_id": "doc-A"}),
            _FakeRecord({"name": "ent-2", "type": "Concept", "confidence": 0.8,
                         "source_doc_id": "doc-A"}),
        ])

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.query_entities_by_source("doc-A", limit=500)
    assert len(result) == 2
    for r in result:
        assert r["source_doc_id"] == "doc-A"


# ────────── 4. query_duplicate_entities ──────────


def test_query_duplicate_entities_returns_groups() -> None:
    """query_duplicate_entities 返回重复实体分组"""
    def handler(query, params):
        return _FakeResult(records=[
            _FakeRecord({
                "name": "nginx",
                "cnt": 2,
                "sources": ["doc-A", "doc-B"],
                "types": ["Service", "Service"],
            }),
        ])

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.query_duplicate_entities(limit=100)
    assert len(result) == 1
    assert result[0]["name"] == "nginx"
    assert result[0]["cnt"] == 2


# ────────── 5. delete_entity ──────────


def test_delete_entity_returns_deleted_info() -> None:
    """delete_entity 返回删除信息"""
    def handler(query, params):
        if "DELETE r" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 0}))
        if "DELETE n" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 1}))
        return _FakeResult()

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.delete_entity("nginx")
    assert result["deleted"] is True
    assert result["name"] == "nginx"
    assert result["nodes_removed"] == 1


def test_delete_entity_not_found_returns_deleted_false() -> None:
    """删除不存在的实体返回 deleted=False"""
    def handler(query, params):
        if "DELETE n" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 0}))
        return _FakeResult()

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.delete_entity("nonexistent")
    assert result["deleted"] is False
    assert result["nodes_removed"] == 0


def test_delete_entity_invalidates_cache() -> None:
    """delete_entity 后再次查询应重新走 Neo4j（缓存失效）"""
    call_count = [0]

    def handler(query, params):
        call_count[0] += 1
        if "DELETE n" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 1}))
        if "DELETE r" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 0}))
        # query_entity 路径（properties(n) AS props）
        return _FakeResult(single_record=_FakeRecord({
            "props": {"name": "nginx", "entity_type": "Service"}
        }))

    driver = _FakeDriver(handler)
    store = _make_store(driver, ttl=30.0)

    # 第一次查询：缓存写入
    store.query_entity("nginx")
    initial_count = call_count[0]

    # 删除实体：缓存全清
    store.delete_entity("nginx")

    # 再次查询：缓存未命中，重新走 Neo4j
    store.query_entity("nginx")
    assert call_count[0] > initial_count


# ────────── 6. batch_delete_entities ──────────


def test_batch_delete_entities_returns_counts() -> None:
    """batch_delete_entities 返回成功/失败计数"""
    def handler(query, params):
        if "DELETE n" in query:
            # 第一个实体删除成功，第二个不存在
            name = params.get("name", "")
            deleted = 1 if name == "nginx" else 0
            return _FakeResult(single_record=_FakeRecord({"deleted": deleted}))
        if "DELETE r" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 0}))
        return _FakeResult()

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.batch_delete_entities(["nginx", "nonexistent"])
    assert result["deleted_count"] == 1
    assert result["failed_count"] == 1
    assert "nginx" in result["deleted"]
    assert any(f["name"] == "nonexistent" for f in result["failed"])


def test_batch_delete_entities_empty_list() -> None:
    """空列表返回 0"""
    driver = _FakeDriver(lambda q, p: _FakeResult())
    store = _make_store(driver)

    result = store.batch_delete_entities([])
    assert result["deleted_count"] == 0
    assert result["failed_count"] == 0


def test_batch_delete_entities_handles_exceptions() -> None:
    """delete_entity 抛错时记入 failed"""
    def handler(query, params):
        if "DELETE n" in query:
            raise RuntimeError("forced error")
        return _FakeResult()

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.batch_delete_entities(["broken"])
    assert result["deleted_count"] == 0
    assert result["failed_count"] == 1
    assert "error" in result["failed"][0]


# ────────── 7. delete_relations_by_source ──────────


def test_delete_relations_by_source_returns_count() -> None:
    """delete_relations_by_source 返回删除的关系数"""
    def handler(query, params):
        assert params["doc_id"] == "doc-A"
        return _FakeResult(single_record=_FakeRecord({"deleted": 5}))

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.delete_relations_by_source("doc-A")
    assert result["source_doc_id"] == "doc-A"
    assert result["relations_removed"] == 5


# ────────── 8. delete_by_source ──────────


def test_delete_by_source_combines_relation_and_entity_deletion() -> None:
    """delete_by_source 先删关系再删实体"""
    call_log: list[str] = []

    def handler(query, params):
        if "DELETE r" in query and "source_doc_id" in query:
            call_log.append("delete_relations")
            return _FakeResult(single_record=_FakeRecord({"deleted": 3}))
        if "source_doc_id: $doc_id" in query or "source_doc_id:" in query:
            call_log.append("query_entities_by_source")
            return _FakeResult(records=[
                _FakeRecord({"name": "ent-1", "type": "Service",
                             "confidence": 0.9, "source_doc_id": "doc-A"}),
                _FakeRecord({"name": "ent-2", "type": "Concept",
                             "confidence": 0.8, "source_doc_id": "doc-A"}),
            ])
        if "DELETE n" in query:
            call_log.append("delete_entity")
            return _FakeResult(single_record=_FakeRecord({"deleted": 1}))
        if "DELETE r" in query:
            call_log.append("delete_entity_relations")
            return _FakeResult(single_record=_FakeRecord({"deleted": 0}))
        return _FakeResult()

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.delete_by_source("doc-A")
    assert result["source_doc_id"] == "doc-A"
    assert result["relations_removed"] == 3
    assert result["entities_deleted"] == 2  # 两个实体
    assert result["entities_failed"] == 0


# ────────── 9. cleanup_low_confidence ──────────


def test_cleanup_low_confidence_returns_deleted_counts() -> None:
    """cleanup_low_confidence 删除低置信度实体"""
    def handler(query, params):
        if "n.confidence < $threshold" in query:
            return _FakeResult(records=[
                _FakeRecord({"name": "low-1", "type": "Service",
                             "confidence": 0.2, "source_doc_id": "doc-A"}),
                _FakeRecord({"name": "low-2", "type": "Concept",
                             "confidence": 0.3, "source_doc_id": "doc-B"}),
            ])
        if "DELETE n" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 1}))
        if "DELETE r" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 0}))
        return _FakeResult()

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.cleanup_low_confidence(threshold=0.5, limit=500)
    assert result["deleted_count"] == 2
    assert result["failed_count"] == 0


def test_cleanup_low_confidence_no_entities() -> None:
    """无低置信度实体时返回 0"""
    def handler(query, params):
        if "n.confidence < $threshold" in query:
            return _FakeResult(records=[])  # 空结果
        return _FakeResult()

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.cleanup_low_confidence(threshold=0.5, limit=500)
    assert result["deleted_count"] == 0
    assert result["failed_count"] == 0


# ────────── 10. cleanup_orphan_entities ──────────


def test_cleanup_orphan_entities_returns_deleted_counts() -> None:
    """cleanup_orphan_entities 删除孤立实体"""
    def handler(query, params):
        if "NOT (n)--()" in query:
            return _FakeResult(records=[
                _FakeRecord({"name": "orphan-1", "type": "Service",
                             "confidence": 0.3, "source_doc_id": "doc-A"}),
            ])
        if "DELETE n" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 1}))
        if "DELETE r" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 0}))
        return _FakeResult()

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.cleanup_orphan_entities(limit=500)
    assert result["deleted_count"] == 1
    assert result["failed_count"] == 0


def test_cleanup_orphan_entities_no_orphans() -> None:
    """无孤立实体时返回 0"""
    def handler(query, params):
        if "NOT (n)--()" in query:
            return _FakeResult(records=[])
        return _FakeResult()

    driver = _FakeDriver(handler)
    store = _make_store(driver)

    result = store.cleanup_orphan_entities(limit=500)
    assert result["deleted_count"] == 0


# ────────── 11. 写操作触发缓存失效 ──────────


def test_batch_delete_invalidates_cache() -> None:
    """batch_delete_entities 后缓存全清"""
    def handler(query, params):
        if "DELETE n" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 1}))
        if "DELETE r" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 0}))
        if "properties(n) AS props" in query:
            return _FakeResult(single_record=_FakeRecord({
                "props": {"name": params.get("name", ""), "entity_type": "Service"}
            }))
        return _FakeResult()

    driver = _FakeDriver(handler)
    store = _make_store(driver, ttl=30.0)

    # 第一次查询缓存写入
    store.query_entity("nginx")
    runs_before = sum(s.run_count for s in driver.sessions)

    # 批量删除触发缓存失效
    store.batch_delete_entities(["nginx"])

    # 再次查询应重新走 Neo4j
    store.query_entity("nginx")
    runs_after = sum(s.run_count for s in driver.sessions)
    assert runs_after > runs_before + 1  # 删除+查询都触发了 Neo4j


def test_delete_relations_by_source_invalidates_cache() -> None:
    """delete_relations_by_source 触发缓存失效"""
    call_count = [0]

    def handler(query, params):
        call_count[0] += 1
        if "DELETE r" in query and "source_doc_id" in query:
            return _FakeResult(single_record=_FakeRecord({"deleted": 1}))
        if "properties(n) AS props" in query:
            return _FakeResult(single_record=_FakeRecord({
                "props": {"name": "nginx", "entity_type": "Service"}
            }))
        return _FakeResult()

    driver = _FakeDriver(handler)
    store = _make_store(driver, ttl=30.0)

    store.query_entity("nginx")
    initial = call_count[0]

    store.delete_relations_by_source("doc-A")

    store.query_entity("nginx")
    # 删除后再次查询应触发 Neo4j
    assert call_count[0] > initial + 1
