"""Webhook 事件分发子系统（Sprint 10+）

提供：
- WebhookManager.dispatch_event(event_type, payload): 异步分发事件到所有匹配订阅
- HMAC-SHA256 签名（X-Webhook-Signature: sha256=<hex>）
- 指数退避重试（默认 3 次：30s / 2min / 10min）
- 后台重试任务（可选启动，由 lifespan 触发）

事件类型清单见 EVENT_CATALOG。
"""

from app.webhooks.manager import (
    EVENT_CATALOG,
    WebhookManager,
    dispatch_event,
    get_webhook_manager,
)

__all__ = [
    "EVENT_CATALOG",
    "WebhookManager",
    "dispatch_event",
    "get_webhook_manager",
]
