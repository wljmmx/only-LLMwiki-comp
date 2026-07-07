"""协作 Hub：管理 wiki 页面的在线用户与编辑锁。

数据结构：
    rooms: dict[slug, CollabRoom]
    CollabRoom:
        connections: dict[user_id, ConnectionInfo]
        lock_holder: str | None  # 持有编辑锁的 user_id

消息协议（JSON）：
    客户端 → 服务端：
        {"type": "heartbeat"}
        {"type": "acquire_lock"}
        {"type": "release_lock"}
        {"type": "edit_event", "payload": {...}}  # 增量编辑提示
        {"type": "cursor", "payload": {"line": N, "col": N}}

    服务端 → 客户端：
        {"type": "presence", "users": [...]}
        {"type": "user_joined", "user": {...}}
        {"type": "user_left", "user_id": "..."}
        {"type": "lock_acquired", "user_id": "..."}
        {"type": "lock_released", "user_id": "..."}
        {"type": "lock_denied", "reason": "..."}
        {"type": "edit_event", "user_id": "...", "payload": {...}}
        {"type": "cursor", "user_id": "...", "payload": {...}}
        {"type": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from starlette.websockets import WebSocket, WebSocketState

logger = structlog.get_logger()

# 心跳超时（秒）：超过该时长未收到 heartbeat 的连接视为断开
HEARTBEAT_TIMEOUT = 60.0
# 心跳清理循环间隔（秒）
CLEANUP_INTERVAL = 30.0


def _format_event_message(
    event_type: str, user_id: str, display_name: str, reason: str = ""
) -> str:
    """S16-6：生成事件的人类可读描述（持久化到 collab_events.message 列）。

    与前端 useCollab.ts 中 appendEvent 生成的 message 保持语义一致，
    便于历史回放与实时事件流在 UI 上无缝衔接。
    """
    name = display_name or user_id or "未知用户"
    if event_type == "user_joined":
        return f"{name} 加入了协作"
    if event_type == "user_left":
        suffix = f"（{reason}）" if reason else ""
        return f"{name} 离开了协作{suffix}"
    if event_type == "lock_acquired":
        return f"{name} 获取了编辑锁"
    if event_type == "lock_released":
        suffix = f"（{reason}）" if reason else ""
        return f"{name} 释放了编辑锁{suffix}"
    if event_type == "lock_denied":
        return f"{name} 持有编辑锁，拒绝新请求"
    return f"{name} 触发了 {event_type}"


class CollabRoomFull(Exception):
    """房间已满或全局房间数上限达到时抛出（S16-4 上限守卫）。

    reason 取值：
    - "max_rooms_exceeded": 全局房间数已达上限
    - "room_full": 单房间连接数已达上限
    """

    def __init__(self, reason: str, message: str = "") -> None:
        self.reason = reason
        self.message = message or reason
        super().__init__(self.message)


@dataclass
class ConnectionInfo:
    """单个 WebSocket 连接的状态"""

    user_id: str
    username: str
    display_name: str
    role: str
    ws: WebSocket
    last_heartbeat: float = field(default_factory=time.time)


@dataclass
class CollabRoom:
    """单个 wiki 页面的协作房间"""

    slug: str
    connections: dict[str, ConnectionInfo] = field(default_factory=dict)
    """user_id → ConnectionInfo"""

    lock_holder: str | None = None
    """当前持有编辑锁的 user_id（None 表示无人持有）"""

    lock_acquired_at: float | None = None

    @property
    def online_users(self) -> list[dict[str, Any]]:
        return [
            {
                "user_id": info.user_id,
                "username": info.username,
                "display_name": info.display_name,
                "role": info.role,
            }
            for info in self.connections.values()
        ]

    @property
    def is_empty(self) -> bool:
        return not self.connections


class CollabHub:
    """协作 Hub：管理所有 wiki 页面的协作房间"""

    def __init__(self) -> None:
        self.rooms: dict[str, CollabRoom] = {}
        self._cleanup_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ────────── 指标辅助（S16-4） ──────────

    def _update_collab_gauges(self) -> None:
        """重算 collab_rooms_total / collab_connections_total Gauge。

        在 connect / disconnect / cleanup_stale 后调用。
        采用全量重算而非增量，避免状态漂移；基于 self 状态而非全局单例，
        便于测试隔离。
        """
        try:
            from app.observability.metrics import business_metrics

            total_conns = sum(len(r.connections) for r in self.rooms.values())
            business_metrics["collab_rooms_total"].set(len(self.rooms))
            business_metrics["collab_connections_total"].set(total_conns)
        except Exception:  # noqa: BLE001
            # 指标采集失败不应影响业务路径
            pass

    @staticmethod
    def _inc_collab_messages(msg_type: str, count: int) -> None:
        """累加协作消息计数器（按 type 分桶）"""
        if count <= 0:
            return
        try:
            from app.observability.metrics import business_metrics

            business_metrics["collab_messages_total"].labels(
                type=str(msg_type or "unknown")
            ).inc(count)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _observe_broadcast_duration(duration_seconds: float) -> None:
        """观察广播延迟分布"""
        try:
            from app.observability.metrics import business_metrics

            business_metrics["collab_broadcast_duration_seconds"].observe(
                duration_seconds
            )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _get_limits() -> tuple[int, int]:
        """读取上限配置 (max_rooms, max_connections_per_room)"""
        try:
            from app.config import get_settings

            s = get_settings()
            return s.collab_max_rooms, s.collab_max_connections_per_room
        except Exception:  # noqa: BLE001
            return 1000, 50

    # ────────── 房间管理 ──────────

    def get_or_create_room(self, slug: str) -> CollabRoom:
        room = self.rooms.get(slug)
        if room is None:
            room = CollabRoom(slug=slug)
            self.rooms[slug] = room
            logger.info("collab.room.created", slug=slug)
        return room

    def get_room(self, slug: str) -> CollabRoom | None:
        return self.rooms.get(slug)

    def _remove_room_if_empty(self, slug: str) -> None:
        room = self.rooms.get(slug)
        if room and room.is_empty:
            del self.rooms[slug]
            logger.info("collab.room.removed", slug=slug)

    # ────────── 连接生命周期 ──────────

    async def connect(
        self,
        slug: str,
        user_id: str,
        username: str,
        display_name: str,
        role: str,
        ws: WebSocket,
    ) -> CollabRoom:
        """加入房间（调用方负责先 ws.accept()）

        Raises:
            CollabRoomFull: 房间数达上限或单房间连接数达上限
        """
        max_rooms, max_per_room = self._get_limits()
        async with self._lock:
            # S16-4：全局房间数上限守卫（仅当新房间需创建时触发）
            if slug not in self.rooms and len(self.rooms) >= max_rooms:
                logger.warning(
                    "collab.connect.rejected_max_rooms",
                    slug=slug,
                    rooms=len(self.rooms),
                    max_rooms=max_rooms,
                )
                raise CollabRoomFull(
                    "max_rooms_exceeded",
                    f"全局协作房间数已达上限 {max_rooms}",
                )
            room = self.get_or_create_room(slug)
            # S16-4：单房间连接上限守卫（仅对新加入者触发，重复连接替换不计数）
            if (
                user_id not in room.connections
                and len(room.connections) >= max_per_room
            ):
                logger.warning(
                    "collab.connect.rejected_room_full",
                    slug=slug,
                    online=len(room.connections),
                    max_per_room=max_per_room,
                )
                # 房间刚因这次失败连接而创建，若仍空则回收
                self._remove_room_if_empty(slug)
                raise CollabRoomFull(
                    "room_full",
                    f"房间 {slug} 已满（上限 {max_per_room}）",
                )
            # 同一 user_id 不允许重复连接（避免状态混乱）
            if user_id in room.connections:
                old = room.connections[user_id]
                try:
                    await old.ws.close(code=4001, reason="重复连接被替换")
                except Exception:  # noqa: BLE001
                    pass
                del room.connections[user_id]
            room.connections[user_id] = ConnectionInfo(
                user_id=user_id,
                username=username,
                display_name=display_name,
                role=role,
                ws=ws,
            )
            logger.info(
                "collab.user.joined",
                slug=slug,
                user_id=user_id,
                username=username,
                online_count=len(room.connections),
            )

        # S16-4：更新 Gauge 指标
        self._update_collab_gauges()

        # 广播 user_joined + presence（在 _lock 之外发送，避免阻塞）
        await self.broadcast(
            slug,
            {
                "type": "user_joined",
                "user": {
                    "user_id": user_id,
                    "username": username,
                    "display_name": display_name,
                    "role": role,
                },
            },
        )
        await self._send_presence(slug)
        return room

    async def disconnect(self, slug: str, user_id: str) -> None:
        """离开房间：移除连接，释放锁（如持有），广播 user_left"""
        async with self._lock:
            room = self.rooms.get(slug)
            if not room or user_id not in room.connections:
                return
            del room.connections[user_id]
            was_lock_holder = room.lock_holder == user_id
            if was_lock_holder:
                room.lock_holder = None
                room.lock_acquired_at = None
            logger.info(
                "collab.user.left",
                slug=slug,
                user_id=user_id,
                online_count=len(room.connections),
                released_lock=was_lock_holder,
            )
            self._remove_room_if_empty(slug)

        # S16-4：更新 Gauge 指标
        self._update_collab_gauges()

        # 广播 user_left + lock_released（如适用）
        if was_lock_holder:
            await self.broadcast(
                slug, {"type": "lock_released", "user_id": user_id, "reason": "user_left"}
            )
        await self.broadcast(slug, {"type": "user_left", "user_id": user_id})
        await self._send_presence(slug)

    # ────────── 编辑锁 ──────────

    async def acquire_lock(self, slug: str, user_id: str) -> bool:
        """请求编辑锁。成功返回 True，已被他人持有时返回 False。"""
        async with self._lock:
            room = self.rooms.get(slug)
            if not room or user_id not in room.connections:
                return False
            if room.lock_holder is not None and room.lock_holder != user_id:
                # 已被他人持有
                holder_info = room.connections.get(room.lock_holder)
                await self._send_to(
                    slug,
                    user_id,
                    {
                        "type": "lock_denied",
                        "reason": "held_by_other",
                        "holder": {
                            "user_id": room.lock_holder,
                            "username": holder_info.username if holder_info else None,
                            "display_name": holder_info.display_name if holder_info else None,
                        },
                    },
                )
                return False
            room.lock_holder = user_id
            room.lock_acquired_at = time.time()

        # 广播 lock_acquired（在 _lock 之外）
        await self.broadcast(slug, {"type": "lock_acquired", "user_id": user_id})
        logger.info("collab.lock.acquired", slug=slug, user_id=user_id)
        return True

    async def release_lock(self, slug: str, user_id: str) -> bool:
        """释放编辑锁。仅当调用者是锁持有者时生效。"""
        async with self._lock:
            room = self.rooms.get(slug)
            if not room or room.lock_holder != user_id:
                return False
            room.lock_holder = None
            room.lock_acquired_at = None

        await self.broadcast(slug, {"type": "lock_released", "user_id": user_id})
        logger.info("collab.lock.released", slug=slug, user_id=user_id)
        return True

    # ────────── 心跳 ──────────

    def touch_heartbeat(self, slug: str, user_id: str) -> bool:
        """更新连接的最后心跳时间。返回 False 表示连接不存在。"""
        room = self.rooms.get(slug)
        if not room or user_id not in room.connections:
            return False
        room.connections[user_id].last_heartbeat = time.time()
        return True

    async def cleanup_stale(self) -> int:
        """清理心跳超时的连接。返回清理的连接数。"""
        now = time.time()
        stale: list[tuple[str, str]] = []
        async with self._lock:
            for slug, room in list(self.rooms.items()):
                for user_id, info in list(room.connections.items()):
                    if now - info.last_heartbeat > HEARTBEAT_TIMEOUT:
                        stale.append((slug, user_id))
                        # 直接移除，避免在 disconnect 中重复加锁
                        del room.connections[user_id]
                        if room.lock_holder == user_id:
                            room.lock_holder = None
                            room.lock_acquired_at = None
                self._remove_room_if_empty(slug)

        # S16-4：清理后更新 Gauge
        if stale:
            self._update_collab_gauges()

        for slug, user_id in stale:
            try:
                await self.broadcast(
                    slug,
                    {
                        "type": "user_left",
                        "user_id": user_id,
                        "reason": "heartbeat_timeout",
                    },
                )
                await self._send_presence(slug)
            except Exception:  # noqa: BLE001
                pass
            logger.info(
                "collab.user.cleaned_up", slug=slug, user_id=user_id, reason="heartbeat_timeout"
            )
        return len(stale)

    async def start_cleanup_loop(self) -> None:
        """启动后台心跳清理任务（幂等）"""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return

        async def _loop() -> None:
            while True:
                try:
                    await asyncio.sleep(CLEANUP_INTERVAL)
                    await self.cleanup_stale()
                except asyncio.CancelledError:
                    break
                except Exception:  # noqa: BLE001
                    logger.exception("collab.cleanup_loop.error")

        self._cleanup_task = asyncio.create_task(_loop())
        logger.info("collab.cleanup_loop.started")

    async def stop_cleanup_loop(self) -> None:
        if self._cleanup_task is None:
            return
        self._cleanup_task.cancel()
        try:
            await self._cleanup_task
        except asyncio.CancelledError:
            pass
        self._cleanup_task = None
        logger.info("collab.cleanup_loop.stopped")

    # ────────── 消息广播 ──────────

    @staticmethod
    def _persist_event(slug: str, message: dict[str, Any]) -> None:
        """S16-6：将事件型消息持久化到 collab_event_store。

        仅持久化 5 类事件（user_joined/user_left/lock_acquired/lock_released/
        lock_denied）。持久化失败静默处理，不影响广播流程。

        从 message 中提取：
        - timestamp: 秒级时间戳（broadcast/_send_to 已注入）
        - event_type: message["type"]
        - user_id: message["user_id"]（user_joined 从 message["user"]["user_id"] 取）
        - display_name: message["user"]["display_name"] 或 message["holder"]["display_name"]
        - message: 人类可读描述（根据类型生成）
        """
        event_type = str(message.get("type", ""))
        if event_type not in (
            "user_joined",
            "user_left",
            "lock_acquired",
            "lock_released",
            "lock_denied",
        ):
            return
        try:
            from app.realtime.collab_event_store import get_collab_event_store

            store = get_collab_event_store()
            ts = float(message.get("timestamp", time.time()))

            # 提取 user_id 与 display_name（不同消息结构不同）
            user_id = ""
            display_name = ""
            if event_type == "user_joined":
                user_obj = message.get("user") or {}
                user_id = str(user_obj.get("user_id", ""))
                display_name = str(user_obj.get("display_name", ""))
            elif event_type == "user_left":
                user_id = str(message.get("user_id", ""))
                display_name = ""
            elif event_type in ("lock_acquired", "lock_released"):
                user_id = str(message.get("user_id", ""))
                display_name = ""
            elif event_type == "lock_denied":
                user_id = str(message.get("holder", {}).get("user_id", ""))
                display_name = str(message.get("holder", {}).get("display_name", ""))

            # 生成人类可读描述
            reason = message.get("reason", "")
            msg_text = _format_event_message(event_type, user_id, display_name, reason)

            store.append_event(
                slug=slug,
                timestamp=ts,
                event_type=event_type,
                user_id=user_id,
                display_name=display_name,
                message=msg_text,
            )
        except Exception:  # noqa: BLE001
            # 持久化失败不影响业务流程
            pass

    async def broadcast(
        self, slug: str, message: dict[str, Any], exclude_user: str | None = None
    ) -> int:
        """向房间内所有连接广播消息。返回实际送达数。"""
        # S16-3：统一注入服务端时间戳（秒），便于前端事件流展示
        if "timestamp" not in message:
            message = {**message, "timestamp": time.time()}
        # S16-6：持久化事件型消息（在广播前，确保即使无连接也能落库）
        self._persist_event(slug, message)
        room = self.rooms.get(slug)
        if not room:
            return 0
        delivered = 0
        # S16-4：观察广播耗时
        start = time.perf_counter()
        targets = [
            info
            for uid, info in room.connections.items()
            if uid != exclude_user and info.ws.client_state == WebSocketState.CONNECTED
        ]
        for info in targets:
            try:
                await info.ws.send_json(message)
                delivered += 1
            except Exception:  # noqa: BLE001
                # 单个连接发送失败不影响其他连接
                logger.warning(
                    "collab.send_failed",
                    slug=slug,
                    user_id=info.user_id,
                    error="send_failed",
                )
        # S16-4：记录广播延迟 + 消息计数
        self._observe_broadcast_duration(time.perf_counter() - start)
        self._inc_collab_messages(str(message.get("type", "unknown")), delivered)
        return delivered

    async def _send_to(
        self, slug: str, user_id: str, message: dict[str, Any]
    ) -> bool:
        """向指定用户发送消息"""
        # S16-3：单播消息也注入时间戳，保持与广播一致
        if "timestamp" not in message:
            message = {**message, "timestamp": time.time()}
        # S16-6：持久化事件型消息（lock_denied 通过单播发送）
        self._persist_event(slug, message)
        room = self.rooms.get(slug)
        if not room or user_id not in room.connections:
            return False
        info = room.connections[user_id]
        try:
            await info.ws.send_json(message)
            # S16-4：单播消息计数
            self._inc_collab_messages(str(message.get("type", "unknown")), 1)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _send_presence(self, slug: str) -> int:
        """广播当前在线用户列表"""
        room = self.rooms.get(slug)
        if not room:
            return 0
        return await self.broadcast(
            slug,
            {
                "type": "presence",
                "users": room.online_users,
                "lock_holder": room.lock_holder,
            },
        )

    # ────────── 状态查询（用于测试与 API） ──────────

    def get_room_state(self, slug: str) -> dict[str, Any] | None:
        """获取房间状态快照（不包含 WebSocket 对象）"""
        room = self.rooms.get(slug)
        if not room:
            return None
        return {
            "slug": slug,
            "online_users": room.online_users,
            "online_count": len(room.connections),
            "lock_holder": room.lock_holder,
            "lock_acquired_at": room.lock_acquired_at,
        }

    def list_rooms(self) -> list[dict[str, Any]]:
        """列出所有房间状态"""
        return [self.get_room_state(slug) for slug in sorted(self.rooms.keys())]  # type: ignore[misc]

    # ────────── 编辑事件 / cursor 转发 ──────────

    async def relay_edit_event(
        self, slug: str, user_id: str, payload: dict[str, Any]
    ) -> int:
        """转发编辑事件给房间内其他用户（不包括发送者）"""
        return await self.broadcast(
            slug,
            {"type": "edit_event", "user_id": user_id, "payload": payload},
            exclude_user=user_id,
        )

    async def relay_cursor(
        self, slug: str, user_id: str, payload: dict[str, Any]
    ) -> int:
        """转发 cursor 位置给房间内其他用户"""
        return await self.broadcast(
            slug,
            {"type": "cursor", "user_id": user_id, "payload": payload},
            exclude_user=user_id,
        )


# ────────── 单例 ──────────

_hub: CollabHub | None = None


def get_collab_hub() -> CollabHub:
    """获取全局 CollabHub 单例"""
    global _hub
    if _hub is None:
        _hub = CollabHub()
    return _hub
