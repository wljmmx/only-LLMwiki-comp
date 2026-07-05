"""审查队列 API（W8）。

端点：
- GET  /review/queue
- GET  /review/stats
- POST /review/{item_id}/approve
- POST /review/{item_id}/reject
- POST /review/batch-approve
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_token
from app.knowledge import get_review_queue

router = APIRouter()


@router.get("/review/queue")
async def list_review_queue(
    status: str = "pending",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """获取审查队列"""
    queue = get_review_queue()
    if status == "pending":
        items = queue.list_pending(limit, offset)
    else:
        items = []  # 其他状态通过 stats 查询
    stats = queue.get_stats()
    return {"items": items, "stats": stats, "limit": limit, "offset": offset}


@router.get("/review/stats")
async def review_stats() -> dict:
    """审查队列统计"""
    queue = get_review_queue()
    return queue.get_stats()


@router.post("/review/{item_id}/approve", dependencies=[Depends(verify_token)])
async def review_approve(item_id: int, note: str = "") -> dict:
    """批准审查项"""
    queue = get_review_queue()
    ok = queue.approve(item_id, note)
    if not ok:
        raise HTTPException(404, f"审查项不存在: {item_id}")
    return {"id": item_id, "status": "approved"}


@router.post("/review/{item_id}/reject", dependencies=[Depends(verify_token)])
async def review_reject(item_id: int, note: str = "") -> dict:
    """驳回审查项"""
    queue = get_review_queue()
    ok = queue.reject(item_id, note)
    if not ok:
        raise HTTPException(404, f"审查项不存在: {item_id}")
    return {"id": item_id, "status": "rejected"}


@router.post("/review/batch-approve", dependencies=[Depends(verify_token)])
async def review_batch_approve(item_ids: list[int]) -> dict:
    """批量批准"""
    queue = get_review_queue()
    count = queue.batch_approve(item_ids)
    return {"approved": count}
