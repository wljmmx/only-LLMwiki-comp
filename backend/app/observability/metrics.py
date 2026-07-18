"""Prometheus 指标定义与采集（Sprint 10+）

指标分类：
1. HTTP 指标（中间件自动采集）
   - opskg_http_requests_total{method, path, status}  Counter
   - opskg_http_request_duration_seconds{method, path} Histogram
   - opskg_http_requests_in_progress  Gauge

2. 业务指标（业务代码主动调用 record_*）
   - opskg_documents_total  Gauge
   - opskg_wiki_pages_total  Gauge
   - opskg_incidents_open  Gauge
   - opskg_webhook_subscriptions_total{active}  Gauge
   - opskg_webhook_deliveries_total{status, event_type}  Counter
   - opskg_llm_calls_total{backend, status}  Counter
   - opskg_llm_call_duration_seconds{backend}  Histogram
   - opskg_search_queries_total  Counter

3. 系统指标（prometheus_client 自带 python_gc_*, process_*）

4. 协作 Hub 指标（S16-4）
   - opskg_collab_rooms_total  Gauge
   - opskg_collab_connections_total  Gauge
   - opskg_collab_messages_total{type}  Counter
   - opskg_collab_broadcast_duration_seconds  Histogram
"""

from __future__ import annotations

import os
import time
from typing import Any

import structlog
from fastapi import Depends, FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    make_asgi_app,
)

from app.auth import verify_token
from app.config import get_settings

logger = structlog.get_logger()

# 默认开启；可通过 OPSKG_METRICS_ENABLED=0 关闭
METRICS_ENABLED = os.getenv("OPSKG_METRICS_ENABLED", "1") != "0"

# 使用独立 registry，避免全局污染
REGISTRY = CollectorRegistry()

# ────────── HTTP 指标 ──────────

http_metrics = {
    "requests_total": Counter(
        "opskg_http_requests_total",
        "HTTP 请求总数",
        ["method", "path_template", "status"],
        registry=REGISTRY,
    ),
    "request_duration": Histogram(
        "opskg_http_request_duration_seconds",
        "HTTP 请求延迟（秒）",
        ["method", "path_template"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        registry=REGISTRY,
    ),
    "in_progress": Gauge(
        "opskg_http_requests_in_progress",
        "当前进行中的 HTTP 请求数",
        registry=REGISTRY,
    ),
}

# ────────── 业务指标 ──────────

business_metrics = {
    "documents_total": Gauge(
        "opskg_documents_total",
        "存储的文档总数",
        registry=REGISTRY,
    ),
    "wiki_pages_total": Gauge(
        "opskg_wiki_pages_total",
        "已发布的 Wiki 页面数",
        registry=REGISTRY,
    ),
    "incidents_open": Gauge(
        "opskg_incidents_open",
        "处于 open 状态的 incident 数",
        registry=REGISTRY,
    ),
    "incidents_total": Gauge(
        "opskg_incidents_total",
        "incident 总数（含已解决）",
        registry=REGISTRY,
    ),
    "webhook_subscriptions": Gauge(
        "opskg_webhook_subscriptions_total",
        "Webhook 订阅数",
        ["active"],
        registry=REGISTRY,
    ),
    "webhook_deliveries_total": Counter(
        "opskg_webhook_deliveries_total",
        "Webhook 投递总数",
        ["status", "event_type"],
        registry=REGISTRY,
    ),
    "llm_calls_total": Counter(
        "opskg_llm_calls_total",
        "LLM 调用次数",
        ["backend", "status"],
        registry=REGISTRY,
    ),
    "llm_call_duration": Histogram(
        "opskg_llm_call_duration_seconds",
        "LLM 调用延迟（秒）",
        ["backend"],
        buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
        registry=REGISTRY,
    ),
    # P2-3: LLM token 用量指标
    "llm_tokens_total": Counter(
        "opskg_llm_tokens_total",
        "LLM token 用量（累计）",
        ["backend", "type"],  # type: prompt | completion
        registry=REGISTRY,
    ),
    "search_queries_total": Counter(
        "opskg_search_queries_total",
        "搜索查询总数",
        registry=REGISTRY,
    ),
    "documents_uploaded_total": Counter(
        "opskg_documents_uploaded_total",
        "已上传文档总数（累计）",
        ["format"],
        registry=REGISTRY,
    ),
    "wiki_published_total": Counter(
        "opskg_wiki_published_total",
        "Wiki 发布次数（累计）",
        registry=REGISTRY,
    ),
    "incidents_created_total": Counter(
        "opskg_incidents_created_total",
        "incident 创建总数（累计）",
        registry=REGISTRY,
    ),
    "errors_total": Counter(
        "opskg_errors_total",
        "应用错误总数",
        ["type"],
        registry=REGISTRY,
    ),
    # ────────── 编译指标（Sprint 10+）──────────
    "compile_total": Counter(
        "opskg_compile_total",
        "编译总次数",
        ["compile_type"],  # full | incremental
        registry=REGISTRY,
    ),
    "compile_duration_seconds": Histogram(
        "opskg_compile_duration_seconds",
        "编译耗时分布（秒）",
        ["compile_type"],
        buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
        registry=REGISTRY,
    ),
    "compile_sections_total": Counter(
        "opskg_compile_sections_total",
        "编译章节总数",
        ["compile_type"],
        registry=REGISTRY,
    ),
    "compile_sections_error_total": Counter(
        "opskg_compile_sections_error_total",
        "编译失败章节数",
        ["compile_type"],
        registry=REGISTRY,
    ),
    "llm_cache_hits_total": Counter(
        "opskg_llm_cache_hits_total",
        "LLM 缓存命中次数",
        ["cache_type"],  # write_body | compile_section
        registry=REGISTRY,
    ),
    "wiki_pages_created_total": Counter(
        "opskg_wiki_pages_created_total",
        "Wiki 页面创建数",
        registry=REGISTRY,
    ),
    "wiki_pages_updated_total": Counter(
        "opskg_wiki_pages_updated_total",
        "Wiki 页面更新数",
        registry=REGISTRY,
    ),
    # S16-4：协作 Hub（CollabHub）指标
    "collab_rooms_total": Gauge(
        "opskg_collab_rooms_total",
        "活跃协作房间数",
        registry=REGISTRY,
    ),
    "collab_connections_total": Gauge(
        "opskg_collab_connections_total",
        "协作 WebSocket 总连接数",
        registry=REGISTRY,
    ),
    "collab_messages_total": Counter(
        "opskg_collab_messages_total",
        "协作消息累计数",
        ["type"],
        registry=REGISTRY,
    ),
    "collab_broadcast_duration_seconds": Histogram(
        "opskg_collab_broadcast_duration_seconds",
        "协作广播延迟（秒）",
        buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
        registry=REGISTRY,
    ),
}


def record_http_request(
    method: str, path_template: str, status: int, duration_seconds: float
) -> None:
    """记录一次 HTTP 请求（中间件自动调用）"""
    if not METRICS_ENABLED:
        return
    try:
        http_metrics["requests_total"].labels(
            method=method, path_template=path_template, status=str(status)
        ).inc()
        http_metrics["request_duration"].labels(
            method=method, path_template=path_template
        ).observe(duration_seconds)
    except Exception as e:  # noqa: BLE001
        logger.error("observability.record_http_failed", err=str(e))


def record_business_metric(name: str, value: float = 1.0, **labels: Any) -> None:
    """更新业务指标

    Args:
        name: 指标名（business_metrics 的 key）
        value: Gauge 取值 / Counter 增量
        labels: 标签键值对
    """
    if not METRICS_ENABLED:
        return
    try:
        metric = business_metrics.get(name)
        if metric is None:
            logger.warning("observability.unknown_metric", name=name)
            return
        if labels:
            metric.labels(**labels).inc(value) if isinstance(
                metric, Counter
            ) else metric.labels(**labels).set(value)
        else:
            metric.inc(value) if isinstance(metric, Counter) else metric.set(value)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "observability.record_business_failed", name=name, err=str(e)
        )


def record_business_histogram(name: str, value: float, **labels: Any) -> None:
    """记录 Histogram 观测值

    Args:
        name: 指标名（business_metrics 的 key）
        value: 观测值（秒）
        labels: 标签键值对
    """
    if not METRICS_ENABLED:
        return
    try:
        metric = business_metrics.get(name)
        if metric is None:
            logger.warning("observability.unknown_histogram", name=name)
            return
        if labels:
            metric.labels(**labels).observe(value)
        else:
            metric.observe(value)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "observability.record_histogram_failed", name=name, err=str(e)
        )


def setup_metrics_middleware(app: FastAPI) -> None:
    """注册 HTTP 指标中间件 + /metrics 端点 + Prometheus ASGI 子应用"""
    if not METRICS_ENABLED:
        logger.info("observability.disabled")
        return

    @app.middleware("http")
    async def _metrics_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        # 跳过 /metrics 自身，避免自引用
        if request.url.path == "/metrics":
            return await call_next(request)
        http_metrics["in_progress"].inc()
        start = time.perf_counter()
        try:
            response = await call_next(request)
            return response
        finally:
            duration = time.perf_counter() - start
            http_metrics["in_progress"].dec()
            # 用 matched route 模板，避免高基数（如 /documents/{doc_id}）
            route = request.scope.get("route")
            path_template = (
                getattr(route, "path", None) or request.url.path or "unknown"
            )
            # 限制 path 长度，防止异常长 URL 爆炸标签
            path_template = path_template[:200]
            # 取 status：response 已返回则用其 code，异常则 500
            status = 500
            try:
                # response 在 finally 中可能未定义（异常路径），用 try 守护
                if "response" in locals() and response is not None:
                    status = response.status_code
            except Exception:  # noqa: BLE001
                pass
            try:
                http_metrics["requests_total"].labels(
                    method=request.method,
                    path_template=path_template,
                    status=str(status),
                ).inc()
                http_metrics["request_duration"].labels(
                    method=request.method, path_template=path_template
                ).observe(duration)
            except Exception as e:  # noqa: BLE001
                logger.error("observability.middleware_record_failed", err=str(e))

    # /metrics 端点：返回 Prometheus 文本格式
    # P0-M9: 认证与 MCP 一致，OPSKG_API_TOKEN 存在时要求 Bearer token
    @app.get("/metrics", tags=["observability"])
    async def metrics_endpoint(
        request: Request,
        _username: str = Depends(verify_token),
    ) -> Response:
        """Prometheus 指标抓取端点

        认证（与 MCP 端点一致）：
        - 未配置 OPSKG_API_TOKEN → 开发模式放行
        - 已配置 OPSKG_API_TOKEN → 需要 Authorization: Bearer <token>
        """
        return Response(
            content=generate_latest(REGISTRY),
            media_type=CONTENT_TYPE_LATEST,
        )

    # 同时挂载 prometheus_client 内置 ASGI app（暴露 process/python 指标）
    # 路径 /metrics 已被上面的端点占用，这里挂到 /metrics/internal 仅供深度排查
    app.mount("/metrics/internal", make_asgi_app(registry=REGISTRY))

    settings = get_settings()
    logger.info("observability.metrics_enabled", endpoint="/metrics", auth="requires-token" if settings.api_token else "open-dev")
