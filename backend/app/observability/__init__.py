"""可观测性子系统（Sprint 10+）

提供：
- Prometheus 指标：HTTP 请求计数/延迟、业务计数（文档/Wiki/incident/webhook）
- `/metrics` 端点：标准的 Prometheus 文本格式输出
- 中间件：自动记录每个 HTTP 请求的 method/path/status/duration
- 业务指标便捷 API：业务代码调用 record_* 即可更新 Gauge
- 可选 OpenTelemetry 分布式追踪（通过环境变量启用）

设计原则：
- 全部可选：observability 子系统异常绝不影响业务
- 零侵入：业务代码只需在关键路径调用 record_*，HTTP 中间件自动收集
- 兼容标准：Prometheus 文本格式，可直接被 Prometheus / Grafana / VictoriaMetrics 抓取
"""

from app.observability.collector import (
    collect_business_metrics,
    start_metrics_collector,
)
from app.observability.metrics import (
    METRICS_ENABLED,
    REGISTRY,
    business_metrics,
    http_metrics,
    record_business_metric,
    record_http_request,
    setup_metrics_middleware,
)
from app.observability.tracing import (
    get_current_span_id,
    get_current_trace_id,
    get_tracer,
    setup_tracing,
    span,
    tracing_log_processor,
)

__all__ = [
    "METRICS_ENABLED",
    "REGISTRY",
    "business_metrics",
    "collect_business_metrics",
    "get_current_span_id",
    "get_current_trace_id",
    "get_tracer",
    "http_metrics",
    "record_business_metric",
    "record_http_request",
    "setup_metrics_middleware",
    "setup_tracing",
    "span",
    "start_metrics_collector",
    "tracing_log_processor",
]
