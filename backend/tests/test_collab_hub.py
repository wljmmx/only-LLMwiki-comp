"""实时协作 Hub 单元测试（S15-5）

覆盖：
- CollabHub: connect / disconnect / get_or_create_room / _remove_room_if_empty
- 编辑锁: acquire_lock / release_lock / 锁拒绝 / 持有者离开自动释放
- 心跳: touch_heartbeat / cleanup_stale
- 消息广播: broadcast / _send_to / _send_presence / relay_edit_event / relay_cursor
- 状态查询: get_room_state / list_rooms
- 后台清理循环: start_cleanup_loop / stop_cleanup_loop

使用 MockWebSocket 模拟 WebSocket 连接，避免真实网络 IO。
异步测试统一通过 asyncio.run() 执行（项目无 pytest-asyncio 依赖）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

import pytest
from starlette.websockets import WebSocketState

os.environ.setdefault("OPSKG_API_TOKEN", "")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.realtime.collab_hub import (  # noqa: E402
    CLEANUP_INTERVAL,
    HEARTBEAT_TIMEOUT,
    CollabHub,
    CollabRoom,
    ConnectionInfo,
    get_collab_hub,
)
from app.routers.realtime_router import _resolve_user  # noqa: E402

# ═══════════════ MockWebSocket ═══════════════


class MockWebSocket:
    """模拟 starlette.WebSocket 的最小可用实现"""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.client_state = WebSocketState.CONNECTED
        self._incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def accept(self) -> None:
        self.client_state = WebSocketState.CONNECTED

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.closed:
            raise RuntimeError("WebSocket is closed")
        self.sent.append(message)

    async def receive_json(self) -> dict[str, Any]:
        item = await self._incoming.get()
        if item is None:
            from starlette.websockets import WebSocketDisconnect

            raise WebSocketDisconnect(code=1000)
        return item

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason
        self.client_state = WebSocketState.DISCONNECTED

    def push_message(self, message: dict[str, Any]) -> None:
        """测试用：向接收队列推消息"""
        self._incoming.put_nowait(message)

    def simulate_disconnect(self) -> None:
        """模拟客户端断开"""
        self._incoming.put_nowait(None)


# ═══════════════ fixture & helpers ═══════════════


@pytest.fixture
def fresh_hub():
    """每个测试独立的 CollabHub 实例"""
    hub = CollabHub()
    yield hub
    if hub._cleanup_task is not None and not hub._cleanup_task.done():
        hub._cleanup_task.cancel()


def _make_conn(
    hub: CollabHub, slug: str, user_id: str, username: str | None = None
) -> tuple[MockWebSocket, ConnectionInfo]:
    """快速构造一个已加入房间的连接（同步直插，绕过 async connect）"""
    ws = MockWebSocket()
    username = username or user_id
    room = hub.get_or_create_room(slug)
    info = ConnectionInfo(
        user_id=user_id,
        username=username,
        display_name=username.capitalize(),
        role="viewer",
        ws=ws,
    )
    room.connections[user_id] = info
    return ws, info


def _run(coro: Any) -> Any:
    """统一运行 async 测试代码"""
    return asyncio.run(coro)


# ═══════════════ 房间管理 ═══════════════


class TestRoomManagement:
    def test_get_or_create_room_creates_new(self, fresh_hub):
        room = fresh_hub.get_or_create_room("nginx-502")
        assert isinstance(room, CollabRoom)
        assert room.slug == "nginx-502"
        assert room.is_empty
        assert fresh_hub.get_room("nginx-502") is room

    def test_get_or_create_room_idempotent(self, fresh_hub):
        room1 = fresh_hub.get_or_create_room("nginx-502")
        room2 = fresh_hub.get_or_create_room("nginx-502")
        assert room1 is room2

    def test_get_room_returns_none_if_absent(self, fresh_hub):
        assert fresh_hub.get_room("non-existent") is None

    def test_remove_room_if_empty(self, fresh_hub):
        fresh_hub.get_or_create_room("nginx-502")
        assert "nginx-502" in fresh_hub.rooms
        fresh_hub._remove_room_if_empty("nginx-502")
        assert "nginx-502" not in fresh_hub.rooms

    def test_remove_room_if_empty_keeps_nonempty(self, fresh_hub):
        _make_conn(fresh_hub, "nginx-502", "u1")
        fresh_hub._remove_room_if_empty("nginx-502")
        assert "nginx-502" in fresh_hub.rooms

    def test_collab_room_online_users(self, fresh_hub):
        _make_conn(fresh_hub, "slug", "u1", "alice")
        _make_conn(fresh_hub, "slug", "u2", "bob")
        room = fresh_hub.get_room("slug")
        users = room.online_users
        assert len(users) == 2
        assert {u["username"] for u in users} == {"alice", "bob"}

    def test_collab_room_is_empty(self, fresh_hub):
        room = fresh_hub.get_or_create_room("slug")
        assert room.is_empty
        _make_conn(fresh_hub, "slug", "u1")
        assert not room.is_empty


# ═══════════════ 连接生命周期 ═══════════════


class TestConnectDisconnect:
    def test_connect_adds_connection_and_broadcasts(self, fresh_hub):
        ws = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws))
        room = fresh_hub.get_room("slug")
        assert "u1" in room.connections
        assert len(ws.sent) >= 1
        types = [m["type"] for m in ws.sent]
        assert "user_joined" in types
        assert "presence" in types

    def test_connect_replaces_duplicate_user(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws2))
        room = fresh_hub.get_room("slug")
        assert ws1.closed
        assert len(room.connections) == 1
        assert room.connections["u1"].ws is ws2

    def test_disconnect_removes_connection(self, fresh_hub):
        ws = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws))
        _run(fresh_hub.disconnect("slug", "u1"))
        # 房间为空后被自动移除
        assert fresh_hub.get_room("slug") is None

    def test_disconnect_releases_lock_if_held(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.acquire_lock("slug", "u1"))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        _run(fresh_hub.disconnect("slug", "u1"))
        room = fresh_hub.get_room("slug")
        assert room.lock_holder is None

    def test_disconnect_unknown_user_noop(self, fresh_hub):
        # 不存在的 user_id / slug 不应抛异常
        _run(fresh_hub.disconnect("slug", "nonexistent"))
        _run(fresh_hub.disconnect("nonexistent-slug", "u1"))

    def test_disconnect_broadcasts_user_left(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        ws1.sent.clear()
        _run(fresh_hub.disconnect("slug", "u2"))
        types = [m["type"] for m in ws1.sent]
        assert "user_left" in types
        assert "presence" in types


# ═══════════════ 编辑锁 ═══════════════


class TestEditLock:
    def test_acquire_lock_succeeds_when_free(self, fresh_hub):
        ws = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws))
        ok = _run(fresh_hub.acquire_lock("slug", "u1"))
        assert ok
        room = fresh_hub.get_room("slug")
        assert room.lock_holder == "u1"
        assert room.lock_acquired_at is not None

    def test_acquire_lock_denied_when_held_by_other(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        ws2.sent.clear()

        ok1 = _run(fresh_hub.acquire_lock("slug", "u1"))
        ok2 = _run(fresh_hub.acquire_lock("slug", "u2"))
        assert ok1
        assert not ok2
        types = [m["type"] for m in ws2.sent]
        assert "lock_denied" in types
        denied = next(m for m in ws2.sent if m["type"] == "lock_denied")
        assert denied["holder"]["user_id"] == "u1"

    def test_acquire_lock_idempotent_for_holder(self, fresh_hub):
        ws = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws))
        ok1 = _run(fresh_hub.acquire_lock("slug", "u1"))
        ok2 = _run(fresh_hub.acquire_lock("slug", "u1"))
        assert ok1
        assert ok2  # 已持有者重复请求视为成功

    def test_acquire_lock_unknown_room_returns_false(self, fresh_hub):
        ok = _run(fresh_hub.acquire_lock("nonexistent", "u1"))
        assert not ok

    def test_acquire_lock_unknown_user_returns_false(self, fresh_hub):
        _make_conn(fresh_hub, "slug", "u1")
        ok = _run(fresh_hub.acquire_lock("slug", "nonexistent"))
        assert not ok

    def test_release_lock_by_holder(self, fresh_hub):
        ws = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws))
        _run(fresh_hub.acquire_lock("slug", "u1"))
        ok = _run(fresh_hub.release_lock("slug", "u1"))
        assert ok
        room = fresh_hub.get_room("slug")
        assert room.lock_holder is None

    def test_release_lock_by_non_holder_fails(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        _run(fresh_hub.acquire_lock("slug", "u1"))
        ok = _run(fresh_hub.release_lock("slug", "u2"))
        assert not ok
        room = fresh_hub.get_room("slug")
        assert room.lock_holder == "u1"

    def test_release_lock_unknown_room(self, fresh_hub):
        ok = _run(fresh_hub.release_lock("nonexistent", "u1"))
        assert not ok

    def test_lock_acquired_broadcasts_to_others(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        ws2.sent.clear()
        _run(fresh_hub.acquire_lock("slug", "u1"))
        types = [m["type"] for m in ws2.sent]
        assert "lock_acquired" in types


# ═══════════════ 心跳 ═══════════════


class TestHeartbeat:
    def test_touch_heartbeat_updates_timestamp(self, fresh_hub):
        _make_conn(fresh_hub, "slug", "u1")
        old = fresh_hub.get_room("slug").connections["u1"].last_heartbeat
        time.sleep(0.01)
        ok = fresh_hub.touch_heartbeat("slug", "u1")
        assert ok
        new = fresh_hub.get_room("slug").connections["u1"].last_heartbeat
        assert new > old

    def test_touch_heartbeat_unknown_returns_false(self, fresh_hub):
        assert not fresh_hub.touch_heartbeat("nonexistent", "u1")
        _make_conn(fresh_hub, "slug", "u1")
        assert not fresh_hub.touch_heartbeat("slug", "nonexistent")

    def test_cleanup_stale_removes_timed_out(self, fresh_hub, monkeypatch):
        # 缩短超时时间便于测试
        monkeypatch.setattr("app.realtime.collab_hub.HEARTBEAT_TIMEOUT", 0.05)
        ws = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws))
        time.sleep(0.1)
        removed = _run(fresh_hub.cleanup_stale())
        assert removed == 1
        # 房间应被移除（连接清空后 _remove_room_if_empty 触发）
        assert fresh_hub.get_room("slug") is None

    def test_cleanup_stale_keeps_active(self, fresh_hub, monkeypatch):
        monkeypatch.setattr("app.realtime.collab_hub.HEARTBEAT_TIMEOUT", 100.0)
        ws = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws))
        removed = _run(fresh_hub.cleanup_stale())
        assert removed == 0
        assert fresh_hub.get_room("slug") is not None

    def test_cleanup_stale_releases_lock(self, fresh_hub, monkeypatch):
        monkeypatch.setattr("app.realtime.collab_hub.HEARTBEAT_TIMEOUT", 0.05)
        ws = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws))
        _run(fresh_hub.acquire_lock("slug", "u1"))
        time.sleep(0.1)
        _run(fresh_hub.cleanup_stale())
        # 房间应已被移除
        assert fresh_hub.get_room("slug") is None


# ═══════════════ 消息广播 ═══════════════


class TestBroadcast:
    def test_broadcast_delivers_to_all(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        ws1.sent.clear()
        ws2.sent.clear()

        delivered = _run(fresh_hub.broadcast("slug", {"type": "test"}))
        assert delivered == 2
        assert ws1.sent == [{"type": "test"}]
        assert ws2.sent == [{"type": "test"}]

    def test_broadcast_excludes_user(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        ws1.sent.clear()
        ws2.sent.clear()

        delivered = _run(
            fresh_hub.broadcast("slug", {"type": "test"}, exclude_user="u1")
        )
        assert delivered == 1
        assert ws1.sent == []
        assert ws2.sent == [{"type": "test"}]

    def test_broadcast_unknown_room_returns_zero(self, fresh_hub):
        delivered = _run(fresh_hub.broadcast("nonexistent", {"type": "test"}))
        assert delivered == 0

    def test_broadcast_skips_disconnected(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        ws2.client_state = WebSocketState.DISCONNECTED
        ws1.sent.clear()
        ws2.sent.clear()

        delivered = _run(fresh_hub.broadcast("slug", {"type": "test"}))
        assert delivered == 1

    def test_send_to_specific_user(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        ws1.sent.clear()
        ws2.sent.clear()

        ok = _run(fresh_hub._send_to("slug", "u2", {"type": "private"}))
        assert ok
        assert ws1.sent == []
        assert ws2.sent == [{"type": "private"}]

    def test_send_to_unknown_user_returns_false(self, fresh_hub):
        _make_conn(fresh_hub, "slug", "u1")
        ok = _run(fresh_hub._send_to("slug", "nonexistent", {"type": "x"}))
        assert not ok

    def test_send_presence_includes_lock_holder(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        _run(fresh_hub.acquire_lock("slug", "u1"))
        ws1.sent.clear()
        ws2.sent.clear()

        _run(fresh_hub._send_presence("slug"))
        presences = [m for m in ws1.sent if m["type"] == "presence"]
        assert len(presences) == 1
        assert presences[0]["lock_holder"] == "u1"
        assert len(presences[0]["users"]) == 2


# ═══════════════ 编辑事件 / cursor 转发 ═══════════════


class TestRelayEvents:
    def test_relay_edit_event_excludes_sender(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        ws1.sent.clear()
        ws2.sent.clear()

        delivered = _run(
            fresh_hub.relay_edit_event("slug", "u1", {"action": "insert", "text": "hi"})
        )
        assert delivered == 1
        assert ws1.sent == []
        assert ws2.sent[0]["type"] == "edit_event"
        assert ws2.sent[0]["user_id"] == "u1"
        assert ws2.sent[0]["payload"]["text"] == "hi"

    def test_relay_cursor_excludes_sender(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("slug", "u2", "bob", "Bob", "viewer", ws2))
        ws1.sent.clear()
        ws2.sent.clear()

        delivered = _run(fresh_hub.relay_cursor("slug", "u2", {"line": 10, "col": 5}))
        assert delivered == 1
        assert ws2.sent == []
        assert ws1.sent[0]["type"] == "cursor"
        assert ws1.sent[0]["user_id"] == "u2"

    def test_relay_edit_event_no_other_users(self, fresh_hub):
        ws = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws))
        delivered = _run(fresh_hub.relay_edit_event("slug", "u1", {"x": 1}))
        assert delivered == 0


# ═══════════════ 状态查询 ═══════════════


class TestStateQuery:
    def test_get_room_state(self, fresh_hub):
        ws = MockWebSocket()
        _run(fresh_hub.connect("slug", "u1", "alice", "Alice", "admin", ws))
        _run(fresh_hub.acquire_lock("slug", "u1"))
        state = fresh_hub.get_room_state("slug")
        assert state["slug"] == "slug"
        assert state["online_count"] == 1
        assert state["lock_holder"] == "u1"
        assert state["lock_acquired_at"] is not None
        assert len(state["online_users"]) == 1

    def test_get_room_state_unknown_returns_none(self, fresh_hub):
        assert fresh_hub.get_room_state("nonexistent") is None

    def test_list_rooms_sorted_by_slug(self, fresh_hub):
        ws1 = MockWebSocket()
        ws2 = MockWebSocket()
        _run(fresh_hub.connect("zebra", "u1", "alice", "Alice", "admin", ws1))
        _run(fresh_hub.connect("apple", "u2", "bob", "Bob", "viewer", ws2))
        rooms = fresh_hub.list_rooms()
        slugs = [r["slug"] for r in rooms]
        assert slugs == ["apple", "zebra"]

    def test_list_rooms_empty(self, fresh_hub):
        assert fresh_hub.list_rooms() == []


# ═══════════════ 后台清理循环 ═══════════════


class TestCleanupLoop:
    def test_start_cleanup_loop_idempotent(self, fresh_hub):
        # 必须在同一 event loop 内调用两次才能验证幂等性
        async def _scenario():
            await fresh_hub.start_cleanup_loop()
            task1 = fresh_hub._cleanup_task
            await fresh_hub.start_cleanup_loop()
            task2 = fresh_hub._cleanup_task
            assert task1 is task2
            await fresh_hub.stop_cleanup_loop()

        _run(_scenario())

    def test_stop_cleanup_loop(self, fresh_hub):
        async def _scenario():
            await fresh_hub.start_cleanup_loop()
            assert fresh_hub._cleanup_task is not None
            await fresh_hub.stop_cleanup_loop()
            assert fresh_hub._cleanup_task is None

        _run(_scenario())

    def test_stop_cleanup_loop_without_start(self, fresh_hub):
        # 不应抛异常
        _run(fresh_hub.stop_cleanup_loop())
        assert fresh_hub._cleanup_task is None


# ═══════════════ 单例 ═══════════════


class TestSingleton:
    def test_get_collab_hub_returns_same_instance(self):
        hub1 = get_collab_hub()
        hub2 = get_collab_hub()
        assert hub1 is hub2


# ═══════════════ 路由辅助函数 ═══════════════


class TestResolveUser:
    def test_resolve_user_dev_mode_no_token(self, monkeypatch):
        # dev 模式：api_token="" 且 token=None → anonymous
        class FakeSettings:
            api_token = ""

        monkeypatch.setattr(
            "app.auth.token_auth.get_settings", lambda: FakeSettings()
        )
        user = _resolve_user(None)
        assert user is not None
        assert user["user_id"] == "anon"
        assert user["role"] == "admin"

    def test_resolve_user_invalid_token(self, monkeypatch):
        # 配置 api_token 但传入无效 token → None
        class FakeSettings:
            api_token = "secret123"

        monkeypatch.setattr(
            "app.auth.token_auth.get_settings", lambda: FakeSettings()
        )
        user = _resolve_user("wrong-token")
        assert user is None

    def test_resolve_user_legacy_token(self, monkeypatch):
        class FakeSettings:
            api_token = "secret123"

        monkeypatch.setattr(
            "app.auth.token_auth.get_settings", lambda: FakeSettings()
        )
        user = _resolve_user("secret123")
        assert user is not None
        assert user["user_id"] == "legacy"
        assert user["role"] == "admin"

    def test_resolve_user_no_token_with_required_auth(self, monkeypatch):
        # 配置 api_token 但不传 token → None
        class FakeSettings:
            api_token = "secret123"

        monkeypatch.setattr(
            "app.auth.token_auth.get_settings", lambda: FakeSettings()
        )
        user = _resolve_user(None)
        assert user is None


# ═══════════════ 常量检查 ═══════════════


class TestConstants:
    def test_heartbeat_timeout_positive(self):
        assert HEARTBEAT_TIMEOUT > 0

    def test_cleanup_interval_positive(self):
        assert CLEANUP_INTERVAL > 0

    def test_cleanup_interval_less_than_heartbeat_timeout(self):
        # 清理频率必须高于超时时间，否则 stale 连接可能不被及时清理
        assert CLEANUP_INTERVAL <= HEARTBEAT_TIMEOUT
