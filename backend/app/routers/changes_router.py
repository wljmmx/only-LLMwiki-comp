"""变更关联 API（P2-3）。

端点：
- POST /changes/ingest
- POST /changes/correlate
- GET  /changes
- GET  /changes/{change_id}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.aiops import get_change_correlator
from app.auth import verify_token

router = APIRouter()


@router.post("/changes/ingest", dependencies=[Depends(verify_token)])
async def changes_ingest(payload: dict) -> dict:
    """接收变更事件流

    Body:
        changes: [Change]  变更事件列表
            - id (可选)
            - change_type: deployment|config_change|migration|scaling|restart|rollback|patch|other
            - timestamp (可选)
            - host, service, component (可选)
            - severity: normal|warning|high
            - author, ticket_id, description
            - attributes (dict)
            - status: completed|failed|in_progress
            - rollback_of (可选，标记为某 change 的回滚)
    """
    changes = payload.get("changes") or []
    if not isinstance(changes, list) or not changes:
        raise HTTPException(400, "changes 必须是非空数组")
    corr = get_change_correlator()
    return corr.ingest(changes)


@router.post("/changes/correlate", dependencies=[Depends(verify_token)])
async def changes_correlate(payload: dict = None) -> dict:
    """关联最近变更与 incident

    Body (可选):
        since_hours: 变更时间范围（默认 24）
        time_window_minutes: 变更-incident 时间窗口（默认 30）
    """
    payload = payload or {}
    since = int(payload.get("since_hours", 24))
    win = payload.get("time_window_minutes")
    win = int(win) if win is not None else None
    corr = get_change_correlator()
    return corr.correlate(since_hours=since, time_window_minutes=win)


@router.get("/changes")
async def changes_list(
    service: str = "",
    limit: int = 50,
) -> dict:
    """列出变更"""
    corr = get_change_correlator()
    items = corr.list_changes(service, limit)
    return {"changes": items, "count": len(items)}


@router.get("/changes/{change_id}")
async def changes_get(change_id: str) -> dict:
    """获取变更详情（含关联 incident）"""
    corr = get_change_correlator()
    ch = corr.get_change(change_id)
    if not ch:
        raise HTTPException(404, f"变更不存在: {change_id}")
    return ch
