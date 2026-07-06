"""Webhook 事件分发核心实现。

设计要点：
1. 事件匹配：支持精确匹配、`*` 通配、前缀通配 `incident.*`
2. 签名：HMAC-SHA256(payload_raw, secret)，header `X-Webhook-Signature: sha256=<hex>`
3. 投递：异步 httpx.AsyncClient，timeout=10s；2xx 视为成功
4. 重试：指数退避 [30s, 120s, 600s]，超过 max_attempts 标记 failed
5. 不阻塞主请求：dispatch_event 用 asyncio.create_task 后台执行
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from app.observability import span
from app.storage.webhook_store import WebhookStore, get_webhook_store

logger = structlog.get_logger()


# ────────── 事件目录 ──────────

EVENT_CATALOG: dict[str, str] = {
    "document.created": "文档上传成功",
    "document.deleted": "文档被删除",
    "document.parsed": "文档解析完成",
    "wiki.published": "Wiki 文档发布/更新",
    "wiki.deleted": "Wiki 文档删除",
    "event.ingested": "告警事件接入",
    "incident.created": "Incident 自动创建",
    "incident.status_changed": "Incident 状态迁移",
    "runbook.generated": "Runbook 自动生成",
    "review.created": "ReviewQueue 新增审查项",
    "review.resolved": "审查项已处理",
    "webhook.test": "Webhook 测试事件（ping）",
}


# ────────── 重试策略 ──────────

# 每次重试相对上一次的间隔（秒）。第 N 次失败后用 RETRY_INTERVALS[N-1]。
# 默认 3 次重试：30s → 2min → 10min
RETRY_INTERVALS: list[int] = [30, 120, 600]
DEFAULT_MAX_ATTEMPTS = 1 + len(RETRY_INTERVALS)  # 1 次初试 + 3 次重试 = 4
DEFAULT_TIMEOUT = 10.0  # 秒


def _compute_next_retry(attempts_so_far: int) -> str:
    """根据已尝试次数计算下次重试时间（ISO8601 UTC）"""
    idx = attempts_so_far - 1  # attempts=1 失败后取 RETRY_INTERVALS[0]
    if idx < 0 or idx >= len(RETRY_INTERVALS):
        # 超出可重试次数
        return ""
    delay = RETRY_INTERVALS[idx]
    return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()


def _event_matches(subscribed: list[str], event_type: str) -> bool:
    """检查订阅的事件列表是否匹配给定事件

    支持三种形式：
    - `*`                    匹配所有事件
    - `incident.*`           匹配前缀
    - `incident.created`     精确匹配
    """
    if not subscribed:
        return False
    for pat in subscribed:
        if pat == "*":
            return True
        if pat == event_type:
            return True
        if pat.endswith(".*"):
            prefix = pat[:-2]
            if event_type.startswith(prefix + "."):
                return True
    return False


def _sign(payload_raw: bytes, secret: str) -> str:
    """HMAC-SHA256 签名，返回 `sha256=<hex>`"""
    mac = hmac.new(secret.encode("utf-8"), payload_raw, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _record_delivery_metric(status: str, event_type: str) -> None:
    """记录 webhook 投递业务指标（容错，失败不抛）"""
    try:
        from app.observability import record_business_metric

        record_business_metric(
            "webhook_deliveries_total",
            status=status,
            event_type=event_type,
        )
    except Exception:  # noqa: BLE001
        pass


class WebhookManager:
    """Webhook 事件分发管理器"""

    def __init__(
        self,
        store: WebhookStore | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.store = store or get_webhook_store()
        self.max_attempts = max_attempts
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._bg_task: asyncio.Task | None = None
        self._stopping = False

    # ────────── 公共 API ──────────

    def dispatch_event(self, event_type: str, payload: dict[str, Any]) -> int:
        """分发事件到所有匹配订阅（fire-and-forget，不阻塞调用方）

        S15-2: 投递前先过 AlertRouter：
        1. 静默窗口命中 → 直接返回 0
        2. 路由规则收窄订阅范围（无规则时行为不变，向后兼容）

        Returns:
            命中并投递的订阅数量
        """
        try:
            subs = self.store.list_subscriptions(active_only=True)
        except Exception as e:  # noqa: BLE001
            logger.error("webhook.dispatch.list_failed", err=str(e))
            return 0

        matched = [s for s in subs if _event_matches(s.get("events", []), event_type)]
        if not matched:
            return 0

        # S15-2: 告警路由引擎（静默 + 规则）
        try:
            from app.webhooks.alert_router import AlertRouter

            router = AlertRouter(self.store)
            if router.is_silenced(event_type, payload):
                logger.info(
                    "webhook.dispatch.silenced", event_type=event_type
                )
                return 0
            matched = router.route(event_type, payload, matched)
            if not matched:
                return 0
        except Exception as e:  # noqa: BLE001
            # 路由引擎异常不应阻断投递（向后兼容降级）
            logger.warning(
                "webhook.dispatch.router_failed_fallback",
                err=str(e),
                event_type=event_type,
            )

        # 注入事件信封字段
        envelope = {
            "event_type": event_type,
            "event_id": f"evt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        }

        for sub in matched:
            # 创建投递记录
            try:
                deliv = self.store.create_delivery(
                    subscription_id=sub["id"],
                    event_type=event_type,
                    payload=envelope,
                    max_attempts=self.max_attempts,
                )
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "webhook.delivery.create_failed",
                    sub_id=sub["id"],
                    err=str(e),
                )
                continue
            # 后台投递，不阻塞当前请求
            try:
                asyncio.get_event_loop().create_task(
                    self._deliver(sub, deliv, envelope)
                )
            except RuntimeError:
                # 没有 event loop（同步代码上下文）→ 同步执行一次尝试
                logger.warning(
                    "webhook.dispatch.no_loop",
                    sub_id=sub["id"],
                    event_type=event_type,
                )
        return len(matched)

    async def _deliver(
        self,
        sub: dict[str, Any],
        deliv: dict[str, Any],
        envelope: dict[str, Any],
    ) -> None:
        """执行单次投递，失败则记录重试信息"""
        # S15-1d: webhook 投递 span 埋点，覆盖整个投递流程
        with span(
            "webhook.deliver",
            event_type=envelope.get("event_type", ""),
            url=sub.get("url", ""),
            sub_id=sub.get("id", ""),
            attempt=deliv.get("attempts", 0),
        ):
            secret = self.store.get_subscription_secret(sub["id"])
            if not secret:
                # 订阅已禁用或删除
                self.store.update_delivery(deliv["id"], status="skipped")
                return

            url = sub["url"]
            payload_raw = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            signature = _sign(payload_raw, secret)
            headers = {
                "Content-Type": "application/json",
                "X-Webhook-Event": envelope["event_type"],
                "X-Webhook-Event-Id": envelope["event_id"],
                "X-Webhook-Signature": signature,
                "X-Webhook-Timestamp": envelope["timestamp"],
                "User-Agent": "OpsKG-Webhook/1.0",
            }

            attempts = deliv["attempts"]
            try:
                client = await self._get_client()
                resp = await client.post(url, content=payload_raw, headers=headers)
                code = resp.status_code
                body = resp.text
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "webhook.delivery.error",
                    deliv_id=deliv["id"],
                    url=url,
                    err=str(e),
                )
                code = None
                body = str(e)[:1000]
            else:
                if 200 <= code < 300:
                    self.store.update_delivery(
                        deliv["id"],
                        status="success",
                        response_code=code,
                        response_body=body,
                        attempts=attempts + 1,
                        next_retry_at=None,
                    )
                    _record_delivery_metric("success", envelope["event_type"])
                    logger.info(
                        "webhook.delivery.success",
                        deliv_id=deliv["id"],
                        sub_id=sub["id"],
                        code=code,
                    )
                    return

            # 失败处理
            attempts += 1
            if attempts >= self.max_attempts:
                self.store.update_delivery(
                    deliv["id"],
                    status="failed",
                    response_code=code,
                    response_body=body,
                    attempts=attempts,
                    next_retry_at=None,
                )
                _record_delivery_metric("failed", envelope["event_type"])
                logger.warning(
                    "webhook.delivery.failed",
                    deliv_id=deliv["id"],
                    attempts=attempts,
                    code=code,
                )
            else:
                next_retry = _compute_next_retry(attempts)
                self.store.update_delivery(
                    deliv["id"],
                    status="retry",
                    response_code=code,
                    response_body=body,
                    attempts=attempts,
                    next_retry_at=next_retry or None,
                )
                _record_delivery_metric("retry", envelope["event_type"])
                logger.info(
                    "webhook.delivery.will_retry",
                    deliv_id=deliv["id"],
                    attempts=attempts,
                    next_retry=next_retry,
                    code=code,
                )

    async def process_pending_retries(self, batch_size: int = 50) -> int:
        """处理到期的重试任务（由后台 worker 周期调用）

        Returns:
            本次处理的重试数量
        """
        if self._stopping:
            return 0
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            pending = self.store.list_pending_retries(now_iso, limit=batch_size)
        except Exception as e:  # noqa: BLE001
            logger.error("webhook.retry.list_failed", err=str(e))
            return 0

        if not pending:
            return 0

        for deliv in pending:
            sub = self.store.get_subscription(deliv["subscription_id"])
            if not sub or not sub.get("active"):
                self.store.update_delivery(deliv["id"], status="skipped")
                continue
            try:
                await self._deliver(sub, deliv, deliv["payload"])
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "webhook.retry.deliver_failed",
                    deliv_id=deliv["id"],
                    err=str(e),
                )
        return len(pending)

    async def start_retry_worker(self, interval_seconds: int = 15) -> None:
        """启动后台重试 worker（在 FastAPI lifespan 中调用）"""
        if self._bg_task is not None:
            return
        self._stopping = False

        async def _loop() -> None:
            while not self._stopping:
                try:
                    await self.process_pending_retries()
                except Exception as e:  # noqa: BLE001
                    logger.error("webhook.worker.iter_error", err=str(e))
                await asyncio.sleep(interval_seconds)

        self._bg_task = asyncio.create_task(_loop())
        logger.info("webhook.worker.started", interval=interval_seconds)

    async def stop_retry_worker(self) -> None:
        self._stopping = True
        if self._bg_task is not None:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
            self._bg_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("webhook.worker.stopped")

    # ────────── 内部 ──────────

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client


# ────────── 模块级便捷 API ──────────

_manager: WebhookManager | None = None


def get_webhook_manager() -> WebhookManager:
    global _manager
    if _manager is None:
        _manager = WebhookManager()
    return _manager


def dispatch_event(event_type: str, payload: dict[str, Any]) -> int:
    """模块级便捷分发函数。

    在业务代码中只需 `from app.webhooks import dispatch_event`，
    然后 `dispatch_event("incident.status_changed", {...})`。
    不会因 webhook 子系统异常导致业务请求失败。
    """
    try:
        return get_webhook_manager().dispatch_event(event_type, payload)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "webhook.dispatch.error", event_type=event_type, err=str(e)
        )
        return 0
