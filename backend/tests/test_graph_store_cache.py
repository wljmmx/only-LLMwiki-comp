"""GraphStore TTL 查询缓存层测试

验证内容：
    1. 缓存命中：相同查询第二次走缓存（session.run 调用次数=1）
    2. 缓存未命中：不同参数各自查询（session.run 调用次数=2）
    3. TTL 过期：过期后重新查询会调用 session.run
    4. 写操作触发全量失效：upsert_entity 后再次 query 重新查 Neo4j
    5. _cache_invalidate(pattern) 按前缀清空
    6. _cache_invalidate() 全清
    7. 线程安全：_cache_lock 保护并发读写
    8. 缓存 key 不冲突：不同方法不同 key

不连接真实 Neo4j，使用 _FakeDriver / _FakeSession / _FakeResult / _FakeRecord
模拟驱动层，通过统计 session.run 调用次数验证缓存行为。
"""
from __future__ import annotations

import threading
import time

from app.config import get_settings
from app.knowledge.graph_store import GraphEntity, GraphStore

# ────────── Fake Neo4j 驱动层 ──────────


class _FakeRecord:
    """模拟 neo4j Record：支持 dict(record) 和 record[key]"""

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
    """模拟 neo4j Result：支持 single() 和迭代"""

    def __init__(self, single_record=None, records=None) -> None:
        self._single = single_record
        self._records = list(records) if records is not None else []

    def single(self):
        return self._single

    def __iter__(self):
        return iter(self._records)


class _FakeSession:
    """模拟 neo4j Session：支持 run() 和上下文管理，记录调用次数"""

    def __init__(self, run_handler) -> None:
        self._handler = run_handler
        self.run_count = 0

    def run(self, query, **params):
        self.run_count += 1
        return self._handler(query, params)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeDriver:
    """模拟 neo4j Driver：session() 创建 _FakeSession，记录所有 session"""

    def __init__(self, run_handler) -> None:
        self._handler = run_handler
        self.sessions: list[_FakeSession] = []

    def session(self) -> _FakeSession:
        s = _FakeSession(self._handler)
        self.sessions.append(s)
        return s

    def close(self) -> None:
        pass


def _universal_handler(query: str, params: dict) -> _FakeResult:
    """通用 run handler：根据 Cypher 内容返回合适的 FakeResult

    - query_entity（含 properties(n) AS props）→ single record 带 props
    - upsert_entity（含 MERGE (n:Entity）→ single record 带 name/type
    - upsert_relation（含 MERGE (a)-[r:）→ single record 带 type/from/to
    - 其它列表查询 → 返回 1 条 record
    """
    if "properties(n) AS props" in query:
        name = params.get("name", "")
        return _FakeResult(
            single_record=_FakeRecord(
                {"props": {"name": name, "entity_type": "Service", "confidence": 0.9}}
            )
        )
    if "MERGE (n:Entity" in query:
        return _FakeResult(
            single_record=_FakeRecord(
                {"name": params.get("name", ""), "type": params.get("entity_type", "")}
            )
        )
    if "MERGE (a)-[r:" in query:
        return _FakeResult(
            single_record=_FakeRecord(
                {
                    "type": params.get("rel_type", ""),
                    "from": params.get("from_name", ""),
                    "to": params.get("to_name", ""),
                }
            )
        )
    # 列表查询方法（query_related / query_by_type / search_entities）
    return _FakeResult(
        records=[_FakeRecord({"name": "x", "type": "Service", "confidence": 0.5})]
    )


def _make_store(driver: _FakeDriver, ttl: float = 30.0) -> GraphStore:
    """构造一个绕过真实 Neo4j 连接的 GraphStore（直接注入 fake driver）"""
    store = GraphStore.__new__(GraphStore)
    store.settings = get_settings()
    store._driver = driver
    store._cache = {}
    store._cache_ttl = ttl
    store._cache_lock = threading.Lock()
    return store


def _total_runs(driver: _FakeDriver) -> int:
    """统计所有 session 的 run 调用总次数"""
    return sum(s.run_count for s in driver.sessions)


# ────────── 1. 缓存命中 ──────────


def test_cache_hit_only_one_session_run() -> None:
    """相同 query 两次调用，第二次走缓存（session.run 仅调用 1 次）"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    r1 = store.query_entity("nginx")
    r2 = store.query_entity("nginx")

    assert r1 == r2
    assert r1 is not None
    assert _total_runs(driver) == 1  # 第二次命中缓存，未调用 session.run
    assert len(driver.sessions) == 1  # 第二次未创建新 session


# ────────── 2. 缓存未命中（不同参数）──────────


def test_cache_miss_different_params() -> None:
    """不同查询参数各自走 Neo4j（session.run 调用 2 次）"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    store.query_entity("nginx")
    store.query_entity("redis")

    assert _total_runs(driver) == 2
    assert len(store._cache) == 2  # 两条不同 key 的缓存


# ────────── 3. TTL 过期 ──────────


def test_cache_ttl_expiry_requeries() -> None:
    """TTL 过期后重新查询会调用 session.run"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    store.query_entity("nginx")
    assert _total_runs(driver) == 1

    # 手动让缓存条目过期（expires_at 置为过去）
    with store._cache_lock:
        for k in list(store._cache):
            _, value = store._cache[k]
            store._cache[k] = (time.time() - 1, value)

    # 过期后重新查询 → _cache_get 返回 None → 重新走 Neo4j
    store.query_entity("nginx")
    assert _total_runs(driver) == 2


def test_cache_get_cleans_expired_entry() -> None:
    """_cache_get 命中过期条目时返回 None 并清理该条目"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    store._cache_set("k", "v")
    with store._cache_lock:
        _, value = store._cache["k"]
        store._cache["k"] = (time.time() - 1, value)

    result = store._cache_get("k")
    assert result is None
    assert "k" not in store._cache  # 过期条目被清理


# ────────── 4. 写操作触发全量失效 ──────────


def test_write_invalidates_cache() -> None:
    """upsert_entity 后缓存被全量清空，再次 query 重新查 Neo4j"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    store.query_entity("nginx")
    assert _total_runs(driver) == 1
    assert len(store._cache) == 1  # 缓存已填充

    # 写操作 → 全量失效缓存
    store.upsert_entity(GraphEntity(entity_type="Service", name="nginx"))
    assert len(store._cache) == 0  # 缓存被清空

    # 再次查询，缓存未命中 → 重新走 Neo4j
    store.query_entity("nginx")
    assert _total_runs(driver) == 3  # query + upsert + query


def test_upsert_relation_invalidates_cache() -> None:
    """upsert_relation 同样触发全量失效"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    store.query_entity("nginx")
    assert len(store._cache) == 1

    from app.knowledge.graph_store import GraphRelation

    store.upsert_relation(
        GraphRelation(relation_type="DEPENDS_ON", from_entity="nginx", to_entity="redis")
    )
    assert len(store._cache) == 0  # 缓存被清空


# ────────── 5. _cache_invalidate(pattern) 按前缀清空 ──────────


def test_cache_invalidate_pattern() -> None:
    """_cache_invalidate(pattern) 仅清空前缀匹配的条目"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    store._cache_set("query_entity:nginx", {"name": "nginx"})
    store._cache_set("query_entity:redis", {"name": "redis"})
    store._cache_set("query_by_type:Service:50", [])

    store._cache_invalidate("query_entity:")

    assert "query_entity:nginx" not in store._cache
    assert "query_entity:redis" not in store._cache
    assert "query_by_type:Service:50" in store._cache  # 前缀不匹配，保留


# ────────── 6. _cache_invalidate() 全清 ──────────


def test_cache_invalidate_all() -> None:
    """_cache_invalidate() 无参数时清空全部缓存"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    store._cache_set("a", 1)
    store._cache_set("b", 2)
    store._cache_set("c", 3)

    store._cache_invalidate()

    assert len(store._cache) == 0


# ────────── 7. 线程安全 ──────────


def test_cache_thread_safe() -> None:
    """_cache_lock 保护并发读写，多线程操作不抛异常且最终状态一致"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    # 验证锁存在且为 threading.Lock 类型
    assert hasattr(store, "_cache_lock")
    assert isinstance(store._cache_lock, type(threading.Lock()))

    errors: list[Exception] = []

    def worker() -> None:
        try:
            for i in range(50):
                store._cache_set(f"key_{i}", i)
                store._cache_get(f"key_{i}")
                if i % 10 == 0:
                    store._cache_invalidate()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors  # 并发期间无异常


# ────────── 8. 缓存 key 不冲突 ──────────


def test_cache_key_no_conflict_between_methods() -> None:
    """不同方法的不同 key 互不冲突，各占一条缓存"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    store.query_entity("nginx")
    store.query_related("nginx")
    store.query_by_type("Service")
    store.search_entities("ng")

    # 4 个不同方法产生 4 条不同 key
    assert len(store._cache) == 4
    keys = list(store._cache.keys())
    assert any(k.startswith("query_entity:") for k in keys)
    assert any(k.startswith("query_related:") for k in keys)
    assert any(k.startswith("query_by_type:") for k in keys)
    assert any(k.startswith("search_entities:") for k in keys)

    # 同方法不同参数也不冲突
    store.query_by_type("Host")
    assert len(store._cache) == 5


# ────────── 附加：列表方法缓存 ──────────


def test_list_method_cache_hit() -> None:
    """query_related 等列表方法同样命中缓存（含空列表场景）"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    r1 = store.query_related("nginx")
    r2 = store.query_related("nginx")

    assert r1 == r2
    assert _total_runs(driver) == 1  # 第二次走缓存


def test_cache_key_includes_limit_and_depth() -> None:
    """同方法不同 limit/depth 参数视为不同查询，分别缓存"""
    driver = _FakeDriver(_universal_handler)
    store = _make_store(driver, ttl=30.0)

    store.query_by_type("Service", limit=10)
    store.query_by_type("Service", limit=50)

    assert len(store._cache) == 2
    assert _total_runs(driver) == 2
