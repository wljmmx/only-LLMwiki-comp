"""业务指标周期采集（Sprint 10+）

每 30s 从各存储层拉取数据更新 Gauge：
- opskg_documents_total ← DocumentStore.get_stats()
- opskg_wiki_pages_total ← VersionControl wiki 计数
- opskg_incidents_open / opskg_incidents_total ← EventCorrelator
- opskg_webhook_subscriptions_total{active} ← WebhookStore

由 FastAPI lifespan 启停后台任务。
"""

from __future__ import annotations

import asyncio

import structlog

logger = structlog.get_logger()

COLLECT_INTERVAL_SECONDS = 60  # P2: 降低采集频率，减少系统开销


async def collect_business_metrics() -> None:
    """采集一次业务指标，更新所有 Gauge"""
    from app.observability import record_business_metric

    try:
        from app.storage import get_document_store

        stats = get_document_store().get_stats()
        record_business_metric("documents_total", float(stats.get("total", 0)))
    except Exception as e:  # noqa: BLE001
        logger.error("observability.collect_documents_failed", err=str(e))

    try:
        from app.storage.version_control import _get_db as get_vc_db

        conn = get_vc_db()
        row = conn.execute(
            "SELECT COUNT(DISTINCT doc_key) as n FROM document_versions WHERE doc_key LIKE 'wiki:%'"
        ).fetchone()
        n = row["n"] if row else 0
        record_business_metric("wiki_pages_total", float(n))
    except Exception as e:  # noqa: BLE001
        logger.error("observability.collect_wiki_failed", err=str(e))

    try:
        from app.aiops.event_correlator import _get_db as get_ev_db

        conn = get_ev_db()
        # open 状态
        row = conn.execute(
            "SELECT COUNT(*) as n FROM incidents WHERE status IN ('open', 'ack', 'investigating', 'mitigated')"
        ).fetchone()
        open_n = row["n"] if row else 0
        record_business_metric("incidents_open", float(open_n))
        # 总数
        row = conn.execute("SELECT COUNT(*) as n FROM incidents").fetchone()
        total_n = row["n"] if row else 0
        record_business_metric("incidents_total", float(total_n))
    except Exception as e:  # noqa: BLE001
        logger.error("observability.collect_incidents_failed", err=str(e))

    try:
        from app.storage import get_webhook_store

        store = get_webhook_store()
        subs = store.list_subscriptions()
        active_n = sum(1 for s in subs if s.get("active"))
        inactive_n = len(subs) - active_n
        record_business_metric("webhook_subscriptions", float(active_n), active="true")
        record_business_metric(
            "webhook_subscriptions", float(inactive_n), active="false"
        )
    except Exception as e:  # noqa: BLE001
        logger.error("observability.collect_webhooks_failed", err=str(e))

    # S16-4：协作 Hub 房间/连接 Gauge 采集
    # 注意：collab_hub 内部 connect/disconnect 已主动更新 Gauge，
    # 这里作为周期性兜底，避免 metric 与状态脱节
    try:
        from app.realtime import get_collab_hub

        hub = get_collab_hub()
        rooms = hub.list_rooms()
        record_business_metric("collab_rooms_total", float(len(rooms)))
        record_business_metric(
            "collab_connections_total",
            float(sum(r.get("online_count", 0) for r in rooms)),
        )
    except Exception as e:  # noqa: BLE001
        logger.error("observability.collect_collab_failed", err=str(e))


async def start_metrics_collector(
    interval_seconds: int = COLLECT_INTERVAL_SECONDS,
) -> asyncio.Task:
    """启动后台业务指标采集器（在 FastAPI lifespan 中调用）"""

    async def _loop() -> None:
        # 启动时立即采集一次
        await collect_business_metrics()
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                await collect_business_metrics()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error("observability.collector_iter_error", err=str(e))

    task = asyncio.create_task(_loop())
    logger.info("observability.collector_started", interval=interval_seconds)
    return task
