"""协作事件持久化存储测试（S16-6）

覆盖：
- CollabEventStore: append_event / list_events / list_events_since / count_events / clear_events
- 白名单过滤：非 5 类事件不持久化
- 分页游标：before_id 正确切片
- 增量同步：since_timestamp 正确过滤
- has_more 判断：多取 1 条
- 异常静默：DB 路径不可达时不抛
- collab_hub 集成：broadcast 触发持久化
- _format_event_message：5 类事件描述生成

每个测试用例独立 DB（tempfile），避免相互污染。
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import pytest
from starlette.websockets import WebSocketState

os.environ.setdefault("OPSKG_API_TOKEN", "")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.realtime import collab_event_store as ces_module  # noqa: E402
from app.realtime.collab_event_store import (  # noqa: E402
    MAX_LIMIT,
    PERSISTED_EVENT_TYPES,
    CollabEventStore,
    get_collab_event_store,
)
from app.realtime.collab_hub import (  # noqa: E402
    CollabHub,
    _format_event_message,
)

# ═══════════════ MockWebSocket ═══════════════


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.client_state = WebSocketState.CONNECTED

    async def accept(self) -> None:
        self.client_state = WebSocketState.CONNECTED

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.closed:
            raise RuntimeError("WebSocket is closed")
        self.sent.append(message)

    async def receive_json(self) -> dict[str, Any]:
        return {}

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = True
        self.client_state = WebSocketState.DISCONNECTED


# ═══════════════ fixture：临时 DB ═══════════════


@pytest.fixture
def temp_store(monkeypatch, tmp_path):
    """每个测试用例使用独立临时 DB"""
    db_path = tmp_path / "test_collab_events.db"
    monkeypatch.setattr(ces_module, "DB_PATH", db_path)
    # 重置单例
    ces_module._store = None
    store = CollabEventStore()
    yield store
    # 清理
    store.clear_events()


# ═══════════════ _format_event_message ═══════════════


class TestFormatEventMessage:
    def test_user_joined(self):
        msg = _format_event_message("user_joined", "u1", "Alice")
        assert "Alice" in msg
        assert "加入" in msg

    def test_user_left_with_reason(self):
        msg = _format_event_message("user_left", "u1", "Alice", "heartbeat_timeout")
        assert "Alice" in msg
        assert "离开" in msg
        assert "heartbeat_timeout" in msg

    def test_user_left_without_reason(self):
        msg = _format_event_message("user_left", "u1", "Alice")
        assert "Alice" in msg
        assert "离开" in msg
        assert "（）" not in msg

    def test_lock_acquired(self):
        msg = _format_event_message("lock_acquired", "u1", "Bob")
        assert "Bob" in msg
        assert "获取" in msg
        assert "编辑锁" in msg

    def test_lock_released_with_reason(self):
        msg = _format_event_message("lock_released", "u1", "Bob", "user_left")
        assert "Bob" in msg
        assert "释放" in msg
        assert "user_left" in msg

    def test_lock_denied(self):
        msg = _format_event_message("lock_denied", "u1", "Bob")
        assert "Bob" in msg
        assert "持有" in msg
        assert "拒绝" in msg

    def test_unknown_type_fallback(self):
        msg = _format_event_message("custom_event", "u1", "Alice")
        assert "Alice" in msg
        assert "custom_event" in msg

    def test_empty_display_name_fallback_to_user_id(self):
        msg = _format_event_message("user_joined", "u1", "")
        assert "u1" in msg

    def test_empty_both_fallback_to_unknown(self):
        msg = _format_event_message("user_joined", "", "")
        assert "未知用户" in msg


# ═══════════════ append_event ═══════════════


class TestAppendEvent:
    def test_append_returns_row_id(self, temp_store):
        rid = temp_store.append_event(
            "slug-1", 1000.0, "user_joined", "u1", "Alice", "Alice 加入了协作"
        )
        assert rid is not None
        assert rid > 0

    def test_append_increments_id(self, temp_store):
        rid1 = temp_store.append_event("s", 1.0, "user_joined", "u1", "A", "m1")
        rid2 = temp_store.append_event("s", 2.0, "user_left", "u1", "A", "m2")
        assert rid2 == rid1 + 1

    def test_append_non_persisted_type_returns_none(self, temp_store):
        """非 5 类事件不持久化"""
        for t in ["presence", "heartbeat_ack", "edit_event", "cursor", "error", "custom"]:
            rid = temp_store.append_event("s", 1.0, t, "u1", "A", "m")
            assert rid is None

    def test_append_all_5_persisted_types(self, temp_store):
        for t in PERSISTED_EVENT_TYPES:
            rid = temp_store.append_event("s", 1.0, t, "u1", "A", "m")
            assert rid is not None

    def test_append_empty_display_name(self, temp_store):
        rid = temp_store.append_event("s", 1.0, "user_joined", "u1", "", "msg")
        assert rid is not None


# ═══════════════ list_events ═══════════════


class TestListEvents:
    def test_empty_slug(self, temp_store):
        result = temp_store.list_events("nonexistent")
        assert result["events"] == []
        assert result["has_more"] is False
        assert result["count"] == 0

    def test_list_returns_newest_first(self, temp_store):
        """按 id 倒序（最新在前）"""
        temp_store.append_event("s", 1.0, "user_joined", "u1", "A", "first")
        temp_store.append_event("s", 2.0, "user_left", "u1", "A", "second")
        temp_store.append_event("s", 3.0, "lock_acquired", "u1", "A", "third")

        result = temp_store.list_events("s")
        assert result["count"] == 3
        assert result["events"][0]["message"] == "third"
        assert result["events"][1]["message"] == "second"
        assert result["events"][2]["message"] == "first"

    def test_list_with_limit(self, temp_store):
        for i in range(10):
            temp_store.append_event("s", float(i), "user_joined", "u1", "A", f"m{i}")

        result = temp_store.list_events("s", limit=3)
        assert result["count"] == 3
        assert result["has_more"] is True
        # 最新 3 条（id 倒序）
        assert result["events"][0]["message"] == "m9"
        assert result["events"][1]["message"] == "m8"
        assert result["events"][2]["message"] == "m7"

    def test_list_limit_exceeds_total(self, temp_store):
        temp_store.append_event("s", 1.0, "user_joined", "u1", "A", "m")
        result = temp_store.list_events("s", limit=100)
        assert result["count"] == 1
        assert result["has_more"] is False

    def test_list_with_before_id_pagination(self, temp_store):
        """分页游标：before_id 仅返回 id < before_id"""
        ids = []
        for i in range(5):
            rid = temp_store.append_event("s", float(i), "user_joined", "u1", "A", f"m{i}")
            ids.append(rid)

        # 第一页：最新 2 条
        page1 = temp_store.list_events("s", limit=2)
        assert page1["count"] == 2
        assert page1["has_more"] is True
        assert page1["events"][0]["id"] == ids[4]
        assert page1["events"][1]["id"] == ids[3]

        # 第二页：before_id = page1 最后一个 id
        page2 = temp_store.list_events("s", limit=2, before_id=page1["events"][1]["id"])
        assert page2["count"] == 2
        assert page2["has_more"] is True
        assert page2["events"][0]["id"] == ids[2]
        assert page2["events"][1]["id"] == ids[1]

        # 第三页
        page3 = temp_store.list_events("s", limit=2, before_id=page2["events"][1]["id"])
        assert page3["count"] == 1
        assert page3["has_more"] is False
        assert page3["events"][0]["id"] == ids[0]

    def test_list_limit_clamped_to_min_1(self, temp_store):
        temp_store.append_event("s", 1.0, "user_joined", "u1", "A", "m")
        result = temp_store.list_events("s", limit=0)
        assert result["count"] == 1  # limit 被夹紧到 1

    def test_list_limit_clamped_to_max(self, temp_store):
        result = temp_store.list_events("s", limit=MAX_LIMIT + 100)
        # 不报错即可（实际无数据）
        assert result["count"] == 0

    def test_list_slug_isolation(self, temp_store):
        """不同 slug 的事件互不干扰"""
        temp_store.append_event("slug-a", 1.0, "user_joined", "u1", "A", "a1")
        temp_store.append_event("slug-b", 2.0, "user_joined", "u1", "A", "b1")
        temp_store.append_event("slug-a", 3.0, "user_left", "u1", "A", "a2")

        result_a = temp_store.list_events("slug-a")
        result_b = temp_store.list_events("slug-b")
        assert result_a["count"] == 2
        assert result_b["count"] == 1

    def test_list_event_dict_structure(self, temp_store):
        temp_store.append_event("s", 1234567890.0, "user_joined", "u1", "Alice", "msg")
        result = temp_store.list_events("s")
        ev = result["events"][0]
        assert set(ev.keys()) == {
            "id", "slug", "timestamp", "event_type",
            "user_id", "display_name", "message", "created_at"
        }
        assert ev["slug"] == "s"
        assert ev["timestamp"] == 1234567890.0
        assert ev["event_type"] == "user_joined"
        assert ev["user_id"] == "u1"
        assert ev["display_name"] == "Alice"
        assert ev["message"] == "msg"


# ═══════════════ list_events_since ═══════════════


class TestListEventsSince:
    def test_since_returns_only_newer(self, temp_store):
        temp_store.append_event("s", 100.0, "user_joined", "u1", "A", "old")
        temp_store.append_event("s", 200.0, "user_left", "u1", "A", "new1")
        temp_store.append_event("s", 300.0, "lock_acquired", "u1", "A", "new2")

        result = temp_store.list_events_since("s", since_timestamp=150.0)
        assert result["count"] == 2
        # 升序（最旧在前）
        assert result["events"][0]["message"] == "new1"
        assert result["events"][1]["message"] == "new2"

    def test_since_excludes_equal(self, temp_store):
        """since_timestamp 不含等于"""
        temp_store.append_event("s", 100.0, "user_joined", "u1", "A", "m")
        result = temp_store.list_events_since("s", since_timestamp=100.0)
        assert result["count"] == 0

    def test_since_empty(self, temp_store):
        result = temp_store.list_events_since("s", since_timestamp=0.0)
        assert result["count"] == 0

    def test_since_with_limit(self, temp_store):
        for i in range(5):
            temp_store.append_event("s", float(i + 1), "user_joined", "u1", "A", f"m{i}")
        result = temp_store.list_events_since("s", since_timestamp=0.0, limit=2)
        assert result["count"] == 2
        assert result["has_more"] is True


# ═══════════════ count_events ═══════════════


class TestCountEvents:
    def test_count_empty(self, temp_store):
        assert temp_store.count_events("s") == 0

    def test_count_after_append(self, temp_store):
        temp_store.append_event("s", 1.0, "user_joined", "u1", "A", "m1")
        temp_store.append_event("s", 2.0, "user_left", "u1", "A", "m2")
        assert temp_store.count_events("s") == 2

    def test_count_slug_isolation(self, temp_store):
        temp_store.append_event("s1", 1.0, "user_joined", "u1", "A", "m")
        temp_store.append_event("s2", 2.0, "user_joined", "u1", "A", "m")
        assert temp_store.count_events("s1") == 1
        assert temp_store.count_events("s2") == 1


# ═══════════════ clear_events ═══════════════


class TestClearEvents:
    def test_clear_specific_slug(self, temp_store):
        temp_store.append_event("s1", 1.0, "user_joined", "u1", "A", "m")
        temp_store.append_event("s2", 2.0, "user_joined", "u1", "A", "m")
        deleted = temp_store.clear_events("s1")
        assert deleted == 1
        assert temp_store.count_events("s1") == 0
        assert temp_store.count_events("s2") == 1

    def test_clear_all(self, temp_store):
        temp_store.append_event("s1", 1.0, "user_joined", "u1", "A", "m")
        temp_store.append_event("s2", 2.0, "user_joined", "u1", "A", "m")
        deleted = temp_store.clear_events()
        assert deleted == 2
        assert temp_store.count_events("s1") == 0
        assert temp_store.count_events("s2") == 0

    def test_clear_nonexistent(self, temp_store):
        deleted = temp_store.clear_events("nonexistent")
        assert deleted == 0


# ═══════════════ CollabHub 集成 ═══════════════


class TestCollabHubPersistenceIntegration:
    """验证 collab_hub.broadcast/_send_to 触发持久化"""

    @pytest.fixture(autouse=True)
    def setup_temp_db(self, monkeypatch, tmp_path):
        db_path = tmp_path / "test_integration.db"
        monkeypatch.setattr(ces_module, "DB_PATH", db_path)
        ces_module._store = None
        yield
        CollabEventStore().clear_events()

    def _connect_user(self, hub, slug, user_id, display_name):
        ws = MockWebSocket()
        asyncio.run(
            hub.connect(
                slug=slug,
                user_id=user_id,
                username=user_id,
                display_name=display_name,
                role="viewer",
                ws=ws,
            )
        )
        return ws

    def test_user_joined_persisted(self):
        hub = CollabHub()
        self._connect_user(hub, "test-slug", "u1", "Alice")

        store = get_collab_event_store()
        events = store.list_events("test-slug")["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "user_joined"
        assert events[0]["user_id"] == "u1"
        assert events[0]["display_name"] == "Alice"
        assert "Alice" in events[0]["message"]
        assert "加入" in events[0]["message"]

    def test_user_left_persisted(self):
        hub = CollabHub()
        self._connect_user(hub, "s", "u1", "Alice")
        asyncio.run(hub.disconnect("s", "u1"))

        store = get_collab_event_store()
        events = store.list_events("s")["events"]
        # user_joined + user_left = 2
        types = [e["event_type"] for e in events]
        assert "user_joined" in types
        assert "user_left" in types

    def test_lock_acquired_persisted(self):
        hub = CollabHub()
        self._connect_user(hub, "s", "u1", "Alice")
        asyncio.run(hub.acquire_lock("s", "u1"))

        store = get_collab_event_store()
        events = store.list_events("s")["events"]
        types = [e["event_type"] for e in events]
        assert "lock_acquired" in types

    def test_lock_released_persisted(self):
        hub = CollabHub()
        self._connect_user(hub, "s", "u1", "Alice")
        asyncio.run(hub.acquire_lock("s", "u1"))
        asyncio.run(hub.release_lock("s", "u1"))

        store = get_collab_event_store()
        events = store.list_events("s")["events"]
        types = [e["event_type"] for e in events]
        assert "lock_released" in types

    def test_lock_denied_persisted(self):
        hub = CollabHub()
        # u1 先连接并获取锁
        self._connect_user(hub, "s", "u1", "Alice")
        asyncio.run(hub.acquire_lock("s", "u1"))
        # u2 连接，尝试获取锁被拒
        self._connect_user(hub, "s", "u2", "Bob")
        asyncio.run(hub.acquire_lock("s", "u2"))

        store = get_collab_event_store()
        events = store.list_events("s")["events"]
        types = [e["event_type"] for e in events]
        assert "lock_denied" in types

    def test_non_event_messages_not_persisted(self):
        """presence / heartbeat_ack 等不持久化"""
        hub = CollabHub()
        self._connect_user(hub, "s", "u1", "Alice")

        store = get_collab_event_store()
        # 仅 user_joined 一条（presence 不持久化）
        assert store.count_events("s") == 1

    def test_persistence_failure_silent(self, monkeypatch):
        """持久化失败不影响广播"""
        # 让 append_event 抛异常
        def fake_append(*args, **kwargs):
            raise RuntimeError("DB locked")

        monkeypatch.setattr(CollabEventStore, "append_event", fake_append)

        hub = CollabHub()
        ws = MockWebSocket()
        # 广播不应抛异常
        delivered = asyncio.run(
            hub.broadcast("s", {"type": "user_joined", "user": {"user_id": "u1"}})
        )
        # 无连接，delivered=0，但不抛异常
        assert delivered == 0

    def test_timestamp_persisted_from_message(self):
        """持久化使用 message 中注入的 timestamp"""
        hub = CollabHub()
        ws = MockWebSocket()
        # 显式传 timestamp
        asyncio.run(
            hub.broadcast(
                "s",
                {
                    "type": "user_joined",
                    "user": {"user_id": "u1", "display_name": "A"},
                    "timestamp": 99999.0,
                },
            )
        )

        store = get_collab_event_store()
        events = store.list_events("s")["events"]
        assert len(events) == 1
        assert events[0]["timestamp"] == 99999.0


# ═══════════════ API 端点集成 ═══════════════


class TestRealtimeEventsAPI:
    """验证 GET /realtime/events/{slug} 端点"""

    @pytest.fixture(autouse=True)
    def setup_temp_db(self, monkeypatch, tmp_path):
        db_path = tmp_path / "test_api.db"
        monkeypatch.setattr(ces_module, "DB_PATH", db_path)
        ces_module._store = None
        yield
        CollabEventStore().clear_events()

    def test_api_list_empty(self):
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        r = client.get("/realtime/events/nonexistent")
        assert r.status_code == 200
        data = r.json()
        assert data["slug"] == "nonexistent"
        assert data["events"] == []
        assert data["has_more"] is False
        assert data["count"] == 0
        assert data["total"] == 0

    def test_api_list_with_data(self):
        from fastapi.testclient import TestClient

        from app.main import app

        # 先写入数据
        store = get_collab_event_store()
        store.append_event("api-slug", 1000.0, "user_joined", "u1", "Alice", "msg")

        client = TestClient(app)
        r = client.get("/realtime/events/api-slug")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["total"] == 1
        assert data["events"][0]["event_type"] == "user_joined"

    def test_api_count_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        store = get_collab_event_store()
        store.append_event("c-slug", 1.0, "user_joined", "u1", "A", "m")
        store.append_event("c-slug", 2.0, "user_left", "u1", "A", "m")

        client = TestClient(app)
        r = client.get("/realtime/events/c-slug/count")
        assert r.status_code == 200
        assert r.json()["count"] == 2

    def test_api_limit_validation_ge_1(self):
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        r = client.get("/realtime/events/s?limit=0")
        assert r.status_code == 422

    def test_api_limit_validation_le_500(self):
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        r = client.get("/realtime/events/s?limit=501")
        assert r.status_code == 422

    def test_api_before_id_pagination(self):
        from fastapi.testclient import TestClient

        from app.main import app

        store = get_collab_event_store()
        ids = []
        for i in range(5):
            rid = store.append_event("p-slug", float(i), "user_joined", "u1", "A", f"m{i}")
            ids.append(rid)

        client = TestClient(app)
        # 第一页
        r1 = client.get("/realtime/events/p-slug?limit=2")
        d1 = r1.json()
        assert d1["count"] == 2
        assert d1["has_more"] is True

        # 第二页
        before_id = d1["events"][1]["id"]
        r2 = client.get(f"/realtime/events/p-slug?limit=2&before_id={before_id}")
        d2 = r2.json()
        assert d2["count"] == 2
        assert d2["events"][0]["id"] < before_id

    def test_api_since_timestamp(self):
        from fastapi.testclient import TestClient

        from app.main import app

        store = get_collab_event_store()
        store.append_event("st-slug", 100.0, "user_joined", "u1", "A", "old")
        store.append_event("st-slug", 200.0, "user_left", "u1", "A", "new")

        client = TestClient(app)
        r = client.get("/realtime/events/st-slug?since_timestamp=150")
        d = r.json()
        assert d["count"] == 1
        assert d["events"][0]["message"] == "new"
