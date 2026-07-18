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
from pydantic import BaseModel

from app.auth import verify_token
from app.knowledge import get_review_queue
from app.schemas import PaginatedData, PaginatedResponse

router = APIRouter()


def _normalize_item(row: dict) -> dict:
    """将 DB 行映射为前端 ReviewItem 期望的字段名。

    DB 字段 → 前端字段：
      id (int)         → id (str)
      item_type        → type
      name/relation    → title
      status           → status
      source_doc_id    → source_doc_id
      created_at       → created_at
      evidence_span    → content (可选)
      reviewer_note    → reason (可选)
    """
    item_type = row.get("item_type", "")
    if item_type == "entity":
        title = row.get("name", "") or row.get("entity_type", "")
    elif item_type == "relation":
        from_e = row.get("from_entity", "")
        to_e = row.get("to_entity", "")
        rel_type = row.get("relation_type", "")
        title = f"{from_e} —[{rel_type}]→ {to_e}" if from_e else rel_type
    else:
        title = row.get("name", "") or item_type

    return {
        "id": str(row.get("id", "")),
        "type": item_type,
        "title": title,
        "status": row.get("status", "pending"),
        "source_doc_id": row.get("source_doc_id", ""),
        "created_at": row.get("created_at", ""),
        "confidence": row.get("confidence", 0.0),
        "content": row.get("evidence_span", "") or None,
        "reason": row.get("reviewer_note", "") or None,
    }


@router.get("/review/queue")
async def list_review_queue(
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    # Deprecated: 向后兼容旧 limit/offset 参数
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    """获取审查队列（分页）

    status=None 表示全部状态，否则按指定状态过滤。
    使用 page/page_size 分页（推荐），同时兼容旧的 limit/offset 参数。
    """
    queue = get_review_queue()

    # 向后兼容：如果显式传入 limit，使用 limit/offset 模式
    if limit is not None:
        _limit, _offset = limit, offset or 0
        _page, _page_size = 1, _limit
    else:
        _limit = page_size
        _offset = (page - 1) * page_size
        _page, _page_size = page, page_size

    if status is None or status == "":
        items = queue.list_by_status(None, _limit, _offset)
        total = queue.count_by_status(None)
    elif status == "pending":
        items = queue.list_by_status("pending", _limit, _offset)
        total = queue.count_by_status("pending")
    else:
        items = queue.list_by_status(status, _limit, _offset)
        total = queue.count_by_status(status)

    stats = queue.get_stats()

    result = PaginatedResponse[dict](
        data=PaginatedData(
            items=[_normalize_item(item) for item in items],
            total=total,
            page=_page,
            page_size=_page_size,
        ),
        message="",
    ).model_dump()
    # 向后兼容：保留 stats 字段
    result["stats"] = stats
    return result


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


class RejectBody(BaseModel):
    reason: str = ""


@router.post("/review/{item_id}/reject", dependencies=[Depends(verify_token)])
async def review_reject(item_id: int, body: RejectBody | None = None) -> dict:
    """驳回审查项

    前端发送 JSON body ``{"reason": "..."}``，映射到 reviewer_note。
    """
    queue = get_review_queue()
    reason = body.reason if body else ""
    ok = queue.reject(item_id, reason)
    if not ok:
        raise HTTPException(404, f"审查项不存在: {item_id}")
    return {"id": item_id, "status": "rejected"}


class BatchApproveBody(BaseModel):
    ids: list[int] = []


@router.post("/review/batch-approve", dependencies=[Depends(verify_token)])
async def review_batch_approve(body: BatchApproveBody) -> dict:
    """批量批准

    前端发送 ``{"ids": [1, 2, 3]}``，映射到 item_ids。
    """
    queue = get_review_queue()
    count = queue.batch_approve(body.ids)
    return {"approved": count}
