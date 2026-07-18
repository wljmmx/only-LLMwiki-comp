"""通用健康检查响应模型。

供 /health、/ready 等基础设施端点使用，也可被外部服务消费者复用。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthStatus(BaseModel):
    """健康检查状态（与 app.ha.collect_health 返回结构对齐）"""

    status: str = Field(description="ok | degraded | down")
    instance_id: str = ""
    uptime_seconds: float = 0.0
    dependencies: dict[str, str] = Field(default_factory=dict)


class ReadinessStatus(BaseModel):
    """就绪检查状态（与 app.ha.collect_readiness 返回结构对齐）"""

    ready: bool = False
    instance_id: str = ""
    uptime_seconds: float = 0.0
    checks: dict[str, bool] = Field(default_factory=dict)
