"""FastAPI 应用入口。

仅保留应用级生命周期、健康检查与各业务域 APIRouter 的聚合注册。
所有业务端点实现已迁移至 `app/routers/*_router.py` 子模块。
"""

from __future__ import annotations

import os
import traceback
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.routers.anomaly_router import router as anomaly_router
from app.routers.auth_router import router as auth_router
from app.routers.backup_router import router as backup_router
from app.routers.changes_router import router as changes_router
from app.routers.documents_router import router as documents_router
from app.routers.events_router import router as events_router
from app.routers.export_router import router as export_router
from app.routers.extraction_router import router as extraction_router
from app.routers.graph_router import router as graph_router
from app.routers.ldap_router import router as ldap_router
from app.routers.llm_wiki_router import router as llm_wiki_router
from app.routers.mcp_router import router as mcp_router
from app.routers.oidc_router import router as oidc_router
from app.routers.okf_router import router as okf_router
from app.routers.parsers_router import router as parsers_router
from app.routers.realtime_router import router as realtime_router
from app.routers.review_router import router as review_router
from app.routers.runbook_router import router as runbook_router
from app.routers.saml_router import router as saml_router
from app.routers.search_router import router as search_router
from app.routers.settings_management_router import router as settings_mgmt_router
from app.routers.setup_router import router as setup_router
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
    # S15-5：启动协作 Hub 心跳清理后台任务
    from app.realtime import get_collab_hub

    collab_hub = get_collab_hub()
    await collab_hub.start_cleanup_loop()
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
        # S15-5：停止协作 Hub 心跳清理
        await collab_hub.stop_cleanup_loop()
        await get_webhook_manager().stop_retry_worker()
        logger.info("backend.stopping", instance_id=instance_id)


app = FastAPI(title="OpsKG Backend", version="0.1.0", lifespan=lifespan)

# P0-7: CORS 中间件
_settings = get_settings()
_cors_origins_raw = _settings.cors_origins.strip()
if _cors_origins_raw:
    _cors_origins = [
        o.strip() for o in _cors_origins_raw.split(",") if o.strip()
    ]
else:
    # 默认允许前端地址
    _cors_origins = [_settings.frontend_base_url]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Trace-ID"],
)

# P0-8: 全局异常处理器 — 统一错误响应，避免泄露内部堆栈
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """捕获所有未处理异常，返回统一错误格式

    - 4xx 异常（HTTPException）保留原 status code
    - 5xx 异常返回 500，不泄露内部细节（生产模式）
    - 所有异常记录结构化日志（含 traceback）
    """
    from fastapi import HTTPException as _HTTPException

    request_id = getattr(request.state, "request_id", None)

    if isinstance(exc, _HTTPException):
        # FastAPI HTTPException — 保留原状态码与详情
        logger.warning(
            "api.http_exception",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=exc.status_code,
            detail=str(exc.detail),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": str(exc.detail),
                "type": "http_exception",
                "status_code": exc.status_code,
                "request_id": request_id,
            },
            headers=getattr(exc, "headers", None),
        )

    # 未知异常 — 500 Internal Server Error
    logger.error(
        "api.unhandled_exception",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        error_type=type(exc).__name__,
        error=str(exc),
        traceback=traceback.format_exc(),
    )
    env = get_settings().env
    detail = str(exc) if env == "dev" else "内部服务器错误"
    return JSONResponse(
        status_code=500,
        content={
            "error": detail,
            "type": "internal_error",
            "status_code": 500,
            "request_id": request_id,
        },
    )


# 注册 Prometheus 指标中间件 + /metrics 端点
from app.observability import setup_metrics_middleware  # noqa: E402

setup_metrics_middleware(app)

# 注册 OpenTelemetry 分布式追踪（默认关闭，OPSKG_TRACING_ENABLED=1 启用）
from app.observability import setup_tracing  # noqa: E402

setup_tracing(app)

# 安全加固：API 限流（slowapi）+ 审计日志
# 顺序：CORS → metrics → tracing → 限流 → 审计
# 限流与审计中间件均在 app 路由之前生效，跳过 /health、/metrics 等基础设施路径
from app.middleware.audit_log import AuditLogMiddleware  # noqa: E402
from app.middleware.rate_limit import configure_rate_limit  # noqa: E402

configure_rate_limit(app)
app.add_middleware(AuditLogMiddleware)


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
app.include_router(ldap_router)
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
app.include_router(anomaly_router)
app.include_router(realtime_router)
app.include_router(setup_router)
app.include_router(settings_mgmt_router)
app.include_router(okf_router)
app.include_router(backup_router)


# ────────── 审计日志查询端点（admin 权限） ──────────
# 使用 dependencies 在装饰器层级强制 admin 权限（dev 模式 anonymous 放行）
from fastapi import Depends  # noqa: E402

from app.auth import require_role as _require_role  # noqa: E402


@app.get(
    "/api/audit/logs",
    tags=["audit"],
    dependencies=[Depends(_require_role("admin"))],
)
async def list_audit_logs(
    user: str | None = Query(None, description="按用户名过滤（精确匹配）"),
    method: str | None = Query(None, description="按 HTTP 方法过滤"),
    path: str | None = Query(None, description="按路径过滤（模糊匹配）"),
    start: str | None = Query(None, description="起始时间（ISO8601，含）"),
    end: str | None = Query(None, description="结束时间（ISO8601，含）"),
    limit: int = Query(100, ge=1, le=1000, description="返回上限"),
    offset: int = Query(0, ge=0, description="分页偏移"),
) -> dict:
    """查询审计日志（admin 权限）

    支持按 user / method / path / timestamp 过滤，按时间倒序返回。
    """
    from app.storage.audit_store import get_audit_store  # noqa: E402

    store = get_audit_store()
    logs = store.list_audit_logs(
        user=user,
        method=method,
        path=path,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    return {
        "logs": logs,
        "count": len(logs),
        "filters": {
            "user": user,
            "method": method,
            "path": path,
            "start": start,
            "end": end,
        },
        "limit": limit,
        "offset": offset,
    }


@app.get(
    "/api/audit/stats",
    tags=["audit"],
    dependencies=[Depends(_require_role("admin"))],
)
async def audit_stats() -> dict:
    """审计日志统计摘要（admin 权限）"""
    from app.storage.audit_store import get_audit_store  # noqa: E402

    return get_audit_store().get_audit_stats()
