"""LLM 成本追踪 API 端点"""

from __future__ import annotations

from fastapi import APIRouter

from app.observability.cost_tracker import PRICING, get_cost_tracker

router = APIRouter(prefix="/cost", tags=["cost-tracking"])


@router.get("/summary")
async def cost_summary() -> dict:
    """获取 LLM 成本汇总"""
    tracker = get_cost_tracker()
    summary = tracker.summary()
    return {
        "total_calls": summary.total_calls,
        "total_input_tokens": summary.total_input_tokens,
        "total_output_tokens": summary.total_output_tokens,
        "total_cost_usd": summary.total_cost_usd,
        "by_backend": summary.by_backend,
        "by_model": summary.by_model,
    }


@router.get("/pricing")
async def cost_pricing() -> dict:
    """获取 LLM 定价参考"""
    return {"pricing": PRICING}


@router.post("/clear")
async def cost_clear() -> dict:
    """清空成本记录"""
    get_cost_tracker().clear()
    return {"ok": True}
