"""FastAPI 应用入口。

仅保留应用级生命周期、健康检查与各业务域 APIRouter 的聚合注册。
所有业务端点实现已迁移至 `app/routers/*_router.py` 子模块。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI

from app.config import get_settings
from app.routers.auth_router import router as auth_router
from app.routers.changes_router import router as changes_router
from app.routers.documents_router import router as documents_router
from app.routers.events_router import router as events_router
from app.routers.export_router import router as export_router
from app.routers.extraction_router import router as extraction_router
from app.routers.graph_router import router as graph_router
from app.routers.llm_wiki_router import router as llm_wiki_router
from app.routers.mcp_router import router as mcp_router
from app.routers.oidc_router import router as oidc_router
from app.routers.parsers_router import router as parsers_router
from app.routers.review_router import router as review_router
from app.routers.runbook_router import router as runbook_router
from app.routers.saml_router import router as saml_router
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
    # HA：启动时记录实例 ID 与部署模式
    from app.ha import get_instance_id, get_startup_time

    instance_id = get_instance_id()
    logger.info(
        "backend.starting",
        env=settings.env,
        llm_backend=settings.llm_backend,
        instance_id=instance_id,
        deployment_mode=settings.deployment_mode,
        started_at=get_startup_time(),
    )
    # 启动 Webhook 重试后台 worker
    from app.webhooks import get_webhook_manager

    await get_webhook_manager().start_retry_worker(interval_seconds=15)
    # 启动可观测性业务指标采集器
    from app.observability import start_metrics_collector

    collector_task = await start_metrics_collector(interval_seconds=30)
    # P3-1：引导默认 admin 用户（首次启动）
    try:
        from app.auth.models import get_auth_store

        admin_user = os.getenv("OPSKG_BOOTSTRAP_ADMIN_USER", "admin")
        admin_pass = os.getenv("OPSKG_BOOTSTRAP_ADMIN_PASSWORD", "admin")
        store = get_auth_store()
        store.ensure_bootstrap_admin(admin_user, admin_pass)
        logger.info("auth.bootstrap_ok", admin_user=admin_user)
    except Exception as e:  # noqa: BLE001
        logger.warning("auth.bootstrap_failed", error=str(e))
    try:
        yield
    finally:
        collector_task.cancel()
        try:
            await collector_task
        except Exception:  # noqa: BLE001
            pass
        await get_webhook_manager().stop_retry_worker()
        logger.info("backend.stopping", instance_id=instance_id)


app = FastAPI(title="OpsKG Backend", version="0.1.0", lifespan=lifespan)

# 注册 Prometheus 指标中间件 + /metrics 端点
from app.observability import setup_metrics_middleware  # noqa: E402

setup_metrics_middleware(app)

# 注册 OpenTelemetry 分布式追踪（默认关闭，OPSKG_TRACING_ENABLED=1 启用）
from app.observability import setup_tracing  # noqa: E402

setup_tracing(app)


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness 探针：返回实例元数据与依赖状态。

    - `status` ∈ `ok | degraded | down`
    - 任一依赖不可达 → degraded；全部不可达 → down
    - HTTP 200 表示进程存活，K8s 不会重启
    """
    from app.ha import collect_health

    return collect_health()


@app.get("/ready")
async def ready() -> dict[str, object]:
    """Readiness 探针：所有 DB 必须可达 + 关键 worker 必须 running。

    返回 ready=False 时返回 HTTP 503，K8s 会从 Service endpoints 摘除流量。
    """
    from fastapi.responses import JSONResponse

    from app.ha import collect_readiness

    result = collect_readiness()
    if not result["ready"]:
        return JSONResponse(status_code=503, content=result)
    return result


@app.get("/tracing/status")
async def tracing_status() -> dict[str, object]:
    """追踪状态：是否启用 + 当前 trace_id/span_id（便于调试）。

    - `enabled`: OPSKG_TRACING_ENABLED=1 且 opentelemetry 已安装
    - `trace_id` / `span_id`: 当前请求的 span 上下文（启用时才有值）
    """
    from app.observability import (
        get_current_span_id,
        get_current_trace_id,
        get_tracer,
    )

    tracer = get_tracer()
    return {
        "enabled": tracer is not None,
        "trace_id": get_current_trace_id(),
        "span_id": get_current_span_id(),
    }


# ────────── 业务域 APIRouter 聚合注册 ──────────
app.include_router(auth_router)
app.include_router(oidc_router)
app.include_router(saml_router)
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
