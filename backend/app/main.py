"""FastAPI 应用入口。

仅保留应用级生命周期、健康检查与各业务域 APIRouter 的聚合注册。
所有业务端点实现已迁移至 `app/routers/*_router.py` 子模块。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI

from app.config import get_settings
from app.routers.changes_router import router as changes_router
from app.routers.documents_router import router as documents_router
from app.routers.events_router import router as events_router
from app.routers.extraction_router import router as extraction_router
from app.routers.export_router import router as export_router
from app.routers.graph_router import router as graph_router
from app.routers.llm_wiki_router import router as llm_wiki_router
from app.routers.mcp_router import router as mcp_router
from app.routers.parsers_router import router as parsers_router
from app.routers.review_router import router as review_router
from app.routers.runbook_router import router as runbook_router
from app.routers.search_router import router as search_router
from app.routers.templates_router import router as templates_router
from app.routers.topology_router import router as topology_router
from app.routers.versions_router import router as versions_router
from app.routers.webhook_router import router as webhook_router
from app.routers.wiki_router import router as wiki_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info("backend.starting", env=settings.env, llm_backend=settings.llm_backend)
    # 启动 Webhook 重试后台 worker
    from app.webhooks import get_webhook_manager

    await get_webhook_manager().start_retry_worker(interval_seconds=15)
    # 启动可观测性业务指标采集器
    from app.observability import start_metrics_collector

    collector_task = await start_metrics_collector(interval_seconds=30)
    try:
        yield
    finally:
        collector_task.cancel()
        try:
            await collector_task
        except Exception:  # noqa: BLE001
            pass
        await get_webhook_manager().stop_retry_worker()
        logger.info("backend.stopping")


app = FastAPI(title="OpsKG Backend", version="0.1.0", lifespan=lifespan)

# 注册 Prometheus 指标中间件 + /metrics 端点
from app.observability import setup_metrics_middleware  # noqa: E402

setup_metrics_middleware(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ────────── 业务域 APIRouter 聚合注册 ──────────
app.include_router(documents_router)
app.include_router(parsers_router)
app.include_router(search_router)
app.include_router(review_router)
app.include_router(wiki_router)
app.include_router(llm_wiki_router)
app.include_router(graph_router)
app.include_router(extraction_router)
app.include_router(events_router)
app.include_router(changes_router)
app.include_router(topology_router)
app.include_router(runbook_router)
app.include_router(templates_router)
app.include_router(versions_router)
app.include_router(export_router)
app.include_router(mcp_router)
app.include_router(webhook_router)
