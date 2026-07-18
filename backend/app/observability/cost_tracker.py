"""LLM 成本追踪 Dashboard

提供 LLM Token 消耗统计和成本估算 API。
支持多种 LLM 后端定价模型，按 token 数量估算费用。

定价参考（2026 年市场价格，可在 config 中覆盖）：
- OpenAI GPT-4o: $2.50/1M input, $10.00/1M output
- OpenAI GPT-4o-mini: $0.15/1M input, $0.60/1M output
- DeepSeek-V3: $0.27/1M input, $1.10/1M output
- Ollama 本地: $0（免费）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

DATA_DIR = Path(__file__).parent.parent.parent / "data"


@dataclass
class LlmUsageRecord:
    """单次 LLM 调用记录"""

    timestamp: str
    backend: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


@dataclass
class LlmCostSummary:
    """LLM 成本汇总"""

    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    by_backend: dict[str, dict] = field(default_factory=dict)
    by_model: dict[str, dict] = field(default_factory=dict)


# 定价表（USD / 1M tokens）
PRICING: dict[str, dict[str, float]] = {
    # OpenAI
    "openai_compat": {"input": 2.50, "output": 10.00},  # GPT-4o
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    # DeepSeek
    "deepseek": {"input": 0.27, "output": 1.10},
    "deepseek-v3": {"input": 0.27, "output": 1.10},
    "deepseek-r1": {"input": 0.55, "output": 2.19},
    # Anthropic (via compatible API)
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-opus-4": {"input": 15.00, "output": 75.00},
    # Ollama / vLLM (local, free)
    "ollama": {"input": 0.0, "output": 0.0},
    "vllm": {"input": 0.0, "output": 0.0},
}


class LlmCostTracker:
    """LLM 成本追踪器（单例）"""

    _instance: LlmCostTracker | None = None
    _lock: Lock = Lock()

    def __new__(cls) -> LlmCostTracker:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._records: list[LlmUsageRecord] = []
                    cls._instance._data_file = DATA_DIR / "llm_cost_tracker.jsonl"
        return cls._instance

    def record(
        self,
        backend: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> LlmUsageRecord:
        """记录一次 LLM 调用"""
        cost = self._estimate_cost(backend, model, input_tokens, output_tokens)
        record = LlmUsageRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            backend=backend,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
        )
        self._records.append(record)
        self._persist(record)
        return record

    def _estimate_cost(
        self,
        backend: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """估算费用"""
        pricing = PRICING.get(model) or PRICING.get(backend, {})
        input_price = pricing.get("input", 0.0)
        output_price = pricing.get("output", 0.0)
        cost = (input_tokens / 1_000_000) * input_price + (
            output_tokens / 1_000_000
        ) * output_price
        return round(cost, 6)

    def _persist(self, record: LlmUsageRecord) -> None:
        """写入 JSONL 文件"""
        try:
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._data_file, "a") as f:
                f.write(
                    json.dumps(
                        {
                            "ts": record.timestamp,
                            "backend": record.backend,
                            "model": record.model,
                            "in": record.input_tokens,
                            "out": record.output_tokens,
                            "cost": record.estimated_cost_usd,
                        }
                    )
                    + "\n"
                )
        except OSError:
            pass  # 磁盘写入失败不阻塞业务

    def summary(self) -> LlmCostSummary:
        """获取汇总统计"""
        s = LlmCostSummary()
        for r in self._records:
            s.total_calls += 1
            s.total_input_tokens += r.input_tokens
            s.total_output_tokens += r.output_tokens
            s.total_cost_usd += r.estimated_cost_usd

            # 按后端
            if r.backend not in s.by_backend:
                s.by_backend[r.backend] = {"calls": 0, "cost": 0.0}
            s.by_backend[r.backend]["calls"] += 1
            s.by_backend[r.backend]["cost"] += r.estimated_cost_usd

            # 按模型
            if r.model not in s.by_model:
                s.by_model[r.model] = {"calls": 0, "cost": 0.0}
            s.by_model[r.model]["calls"] += 1
            s.by_model[r.model]["cost"] += r.estimated_cost_usd

        s.total_cost_usd = round(s.total_cost_usd, 4)
        for backend_data in s.by_backend.values():
            backend_data["cost"] = round(backend_data["cost"], 4)
        for model_data in s.by_model.values():
            model_data["cost"] = round(model_data["cost"], 4)

        return s

    def clear(self) -> None:
        """清空记录"""
        self._records.clear()
        if self._data_file.exists():
            self._data_file.unlink()


# 全局实例
def get_cost_tracker() -> LlmCostTracker:
    return LlmCostTracker()
