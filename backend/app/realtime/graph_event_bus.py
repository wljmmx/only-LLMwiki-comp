"""KNOW-11: 图谱变更事件总线

图谱写入操作（upsert/delete/merge）后发布 `graph_changed` 事件，
解耦生产者（graph_store）与消费者（WebSocket 推送、graph→wiki 重编译）。

事件结构：
    {
        "action": "upsert" | "delete" | "merge" | "relation_upsert" | "relation_delete",
        "entity_id": str,           # 实体名称（关系时为 from→to）
        "entity_type": str,         # 实体类型（关系时为 relation_type）
        "changed_fields": dict,     # 变更字段（可选）
        "source_doc_id": str,       # 来源文档（可选）
        "timestamp": float,         # 事件时间戳
    }

单机实现基于 asyncio.Queue；多机部署可升级为 Redis pubsub（与 CollabHub 一致）。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import structlog

logger = structlog.get_logger()

# 事件队列最大容量（防止消费者过慢时内存爆炸）
MAX_QUEUE_SIZE = 1000


@dataclass
class GraphEvent:
    """图谱变更事件"""

    action: str
    """upsert | delete | merge | relation_upsert | relation_delete"""

    entity_id: str
    """实体名称；关系事件为 'from→to'"""

    entity_type: str
    """实体类型；关系事件为 relation_type"""

    changed_fields: dict[str, Any] = field(default_factory=dict)
    """变更字段（可选）"""

    source_doc_id: str = ""
    """来源文档 ID（可选）"""

    timestamp: float = field(default_factory=time.time)
    """事件时间戳"""


class GraphEventBus:
    """图谱变更事件总线（发布/订阅模式）

    单机基于 asyncio.Queue 实现；每个订阅者拥有独立队列，避免相互阻塞。
    订阅者过慢导致队列满时丢弃最旧事件（图谱变更最终一致，前端可全量刷新兜底）。
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[GraphEvent]] = []
        self._lock = asyncio.Lock()

    async def publish(self, event: GraphEvent) -> int:
        """向所有订阅者广播事件（异步）。返回实际投递的订阅者数。"""
        delivered = 0
        async with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                # 队列满：丢弃最旧事件，放入最新（图谱变更最终一致）
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                    delivered += 1
                except Exception:  # noqa: BLE001
                    pass
        logger.debug(
            "graph_event_published",
            action=event.action,
            entity_id=event.entity_id,
            delivered=delivered,
        )
        return delivered

    def publish_sync(
        self,
        action: str,
        entity_id: str,
        entity_type: str = "",
        changed_fields: dict[str, Any] | None = None,
        source_doc_id: str = "",
    ) -> None:
        """同步发布方法（从同步上下文调用，如 graph_store 的写入方法）

        通过 call_soon_threadsafe 将事件投递到事件循环中的订阅者队列。
        无运行中的事件循环时静默跳过（不阻塞调用方）。
        """
        event = GraphEvent(
            action=action,
            entity_id=entity_id,
            entity_type=entity_type,
            changed_fields=changed_fields or {},
            source_doc_id=source_doc_id,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 无运行中的事件循环（如测试/脚本上下文）：静默跳过
            return
        subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._put_nowait, queue, event)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _put_nowait(queue: asyncio.Queue, event: GraphEvent) -> None:
        """线程安全投递事件到队列（在事件循环中执行）"""
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except Exception:  # noqa: BLE001
                pass

    async def publish_simple(
        self,
        action: str,
        entity_id: str,
        entity_type: str = "",
        changed_fields: dict[str, Any] | None = None,
        source_doc_id: str = "",
    ) -> int:
        """便捷发布方法"""
        event = GraphEvent(
            action=action,
            entity_id=entity_id,
            entity_type=entity_type,
            changed_fields=changed_fields or {},
            source_doc_id=source_doc_id,
        )
        return await self.publish(event)

    async def subscribe(self) -> AsyncGenerator[GraphEvent, None]:
        """订阅图谱变更事件（异步生成器）

        订阅者在循环中 await 消费事件；断开连接时自动清理队列。
        """
        queue: asyncio.Queue[GraphEvent] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        async with self._lock:
            self._subscribers.append(queue)
        logger.info("graph_event_subscribed", subscribers=len(self._subscribers))
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            async with self._lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)
            logger.info("graph_event_unsubscribed", subscribers=len(self._subscribers))

    def subscriber_count(self) -> int:
        """当前订阅者数"""
        return len(self._subscribers)


# ────────── 全局单例 ──────────

_bus: GraphEventBus | None = None


def get_graph_event_bus() -> GraphEventBus:
    """获取全局 GraphEventBus 单例"""
    global _bus
    if _bus is None:
        _bus = GraphEventBus()
    return _bus
