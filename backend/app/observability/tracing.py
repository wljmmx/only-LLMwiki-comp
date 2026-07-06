"""OpenTelemetry 分布式追踪（Sprint 10+ P3-3）

提供：
- W3C Trace Context 自动传播（FastAPI 入站 + httpx 出站）
- 自定义业务 span：LLM 调用、文档解析、知识编译等
- 日志关联：`tracing_log_processor` structlog processor 注入 trace_id/span_id
- OTLP 导出：可选发送到 Jaeger / Tempo / OTel Collector

启用（默认关闭）：
    OPSKG_TRACING_ENABLED=1
    OPSKG_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces   # 留空则 Console 输出
    OPSKG_OTLP_SERVICE_NAME=opskg-backend
    OPSKG_TRACING_SAMPLE_RATIO=1.0                              # 采样率 0.0~1.0

设计原则（与 metrics.py 一致）：
- 全部可选：未启用或 opentelemetry 未安装时降级为 no-op，绝不影响业务
- 零侵入：业务代码用 `with span("name", **attrs):` 包裹关键路径
- 容错：所有 otel 调用 try-except 守护
- 可测试：`setup_tracing(app, provider=...)` 支持注入 InMemorySpanExporter
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

import structlog

logger = structlog.get_logger()


def _is_enabled() -> bool:
    """运行时检查是否启用 tracing（便于测试通过 env 切换）。"""
    return os.getenv("OPSKG_TRACING_ENABLED", "0") == "1"


# 模块级状态
_tracer: Any = None  # opentelemetry.trace.Tracer 或 None
_initialized: bool = False


def setup_tracing(app: Any, *, provider: Any = None) -> None:
    """初始化 OpenTelemetry tracing。在 FastAPI app 创建后调用一次。

    参数：
        app: FastAPI 实例（用于 FastAPIInstrumentor.instrument_app）
        provider: 可选 TracerProvider（测试时注入 InMemorySpanExporter）；为 None 则按
                  环境变量构建（OTLP 或 Console）

    幂等：已初始化则直接返回。
    """
    global _tracer, _initialized
    if _initialized:
        return
    enabled = _is_enabled() or provider is not None
    if not enabled:
        logger.info("tracing.disabled")
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )

        if provider is None:
            service_name = os.getenv("OPSKG_OTLP_SERVICE_NAME", "opskg-backend")
            resource = Resource.create(
                {
                    "service.name": service_name,
                    "service.version": "0.1.0",
                    "deployment.environment": os.getenv("OPSKG_ENV", "dev"),
                }
            )
            provider = SDKTracerProvider(resource=resource)

            otlp_endpoint = os.getenv("OPSKG_OTLP_ENDPOINT", "")
            if otlp_endpoint:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
                )
                logger.info(
                    "tracing.otlp_enabled",
                    endpoint=otlp_endpoint,
                    service=service_name,
                )
            else:
                provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
                logger.info("tracing.console_enabled", service=service_name)
        # provider 由调用方注入（测试场景）：不附加额外 processor

        trace.set_tracer_provider(provider)
        _tracer = provider.get_tracer("opskg")

        # 自动 instrumentation：FastAPI（入站 server span）+ httpx（出站 client span）
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        HTTPXClientInstrumentor().instrument()

        _initialized = True
        logger.info("tracing.initialized")
    except Exception as e:  # noqa: BLE001
        logger.warning("tracing.setup_failed", error=str(e))
        _tracer = None


def get_tracer(name: str = "opskg") -> Any:
    """获取 tracer。未启用 / 未初始化时返回 None。"""
    if not _is_enabled() and _tracer is None:
        return None
    return _tracer


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """自定义业务 span 上下文管理器。

    用法::

        from app.observability import span
        with span("llm.call", backend="ollama", model="qwen2.5"):
            result = llm.generate(...)

    未启用 tracing 时为 no-op（yield None），业务代码无需条件判断。
    """
    tracer = get_tracer()
    if tracer is None:
        yield None
        return
    try:
        with tracer.start_as_current_span(name) as s:
            for k, v in attributes.items():
                try:
                    s.set_attribute(k, v)
                except Exception:  # noqa: BLE001
                    pass
            yield s
    except Exception:  # noqa: BLE001
        # tracing 异常绝不影响业务
        yield None


def get_current_trace_id() -> str | None:
    """获取当前活跃 span 的 trace_id（16 进制 32 字符）。

    用于日志关联 / 错误响应注入。无活跃 span 时返回 None。
    """
    if not _is_enabled() and _tracer is None:
        return None
    try:
        from opentelemetry import trace

        span_ctx = trace.get_current_span().get_span_context()
        if span_ctx and span_ctx.is_valid:
            return format(span_ctx.trace_id, "032x")
    except Exception:  # noqa: BLE001
        pass
    return None


def get_current_span_id() -> str | None:
    """获取当前活跃 span 的 span_id（16 进制 16 字符）。无活跃 span 时返回 None。"""
    if not _is_enabled() and _tracer is None:
        return None
    try:
        from opentelemetry import trace

        span_ctx = trace.get_current_span().get_span_context()
        if span_ctx and span_ctx.is_valid:
            return format(span_ctx.span_id, "016x")
    except Exception:  # noqa: BLE001
        pass
    return None


def tracing_log_processor(
    _logger: Any, _method_name: str, event_dict: dict
) -> dict:
    """structlog processor：将当前 span 的 trace_id / span_id 注入日志事件。

    用法::

        import structlog
        from app.observability import tracing_log_processor
        structlog.configure(processors=[
            ...,
            tracing_log_processor,
            structlog.processors.JSONRenderer(),
        ])

    未启用 tracing 时原样返回（不注入）。
    """
    if not _is_enabled() and _tracer is None:
        return event_dict
    trace_id = get_current_trace_id()
    span_id = get_current_span_id()
    if trace_id:
        event_dict["trace_id"] = trace_id
    if span_id:
        event_dict["span_id"] = span_id
    return event_dict


def _reset_for_test() -> None:
    """测试专用：重置模块状态以便重新初始化。生产代码不应调用。"""
    global _tracer, _initialized
    _tracer = None
    _initialized = False
