"""事件关联 API（P2-2）。

端点：
- POST /events/ingest
- POST /events/correlate
- GET  /events/incidents
- GET  /events/incidents/{incident_id}
- POST /events/incidents/{incident_id}/close
- POST /events/incidents/{incident_id}/runbook
- GET  /events/incidents/{incident_id}/changes
- GET  /events/incidents/{incident_id}/rollback-suggestion
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from app.aiops import get_change_correlator, get_event_correlator
from app.auth import verify_token
from app.search import get_search_engine
from app.storage import get_version_control

router = APIRouter()


@router.post("/events/ingest", dependencies=[Depends(verify_token)])
async def events_ingest(payload: dict) -> dict:
    """接收告警事件流

    Body:
        events: [Event]  事件列表，每个事件包含:
            - id (可选，缺省自动生成)
            - timestamp (可选，缺省用当前时间)
            - host, service, component (可选)
            - severity: info|low|warning|high|critical|fatal
            - message
            - tags, source, attributes (可选)
    """
    events = payload.get("events") or []
    if not isinstance(events, list) or not events:
        raise HTTPException(400, "events 必须是非空数组")
    corr = get_event_correlator()
    return corr.ingest(events)


@router.post("/events/correlate", dependencies=[Depends(verify_token)])
async def events_correlate(payload: dict = None) -> dict:
    """关联最近事件，输出 incident 分组

    Body (可选):
        since_minutes: 关联时间窗口起点（默认 60）
        max_events: 最多处理事件数（默认 500）
    """
    payload = payload or {}
    since = int(payload.get("since_minutes", 60))
    max_ev = int(payload.get("max_events", 500))
    corr = get_event_correlator()
    return corr.correlate(since_minutes=since, max_events=max_ev)


@router.get("/events/incidents")
async def events_list_incidents(
    status: str = "open",
    limit: int = 50,
) -> dict:
    """列出 incident"""
    corr = get_event_correlator()
    items = corr.list_incidents(status, limit)
    return {"incidents": items, "count": len(items)}


@router.get("/events/incidents/{incident_id}")
async def events_get_incident(incident_id: str) -> dict:
    """获取 incident 详情"""
    corr = get_event_correlator()
    inc = corr.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, f"incident 不存在: {incident_id}")
    return inc


@router.post(
    "/events/incidents/{incident_id}/close", dependencies=[Depends(verify_token)]
)
async def events_close_incident(incident_id: str, note: str = "") -> dict:
    """关闭 incident"""
    corr = get_event_correlator()
    ok = corr.close_incident(incident_id, note)
    if not ok:
        raise HTTPException(404, f"incident 不存在或已关闭: {incident_id}")
    return {"incident_id": incident_id, "status": "closed"}


@router.post(
    "/events/incidents/{incident_id}/runbook", dependencies=[Depends(verify_token)]
)
async def events_incident_to_runbook(incident_id: str, publish: bool = False) -> dict:
    """基于 incident 自动生成 Runbook"""
    from app.knowledge import get_runbook_generator

    corr = get_event_correlator()
    inc = corr.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, f"incident 不存在: {incident_id}")

    hint_str = inc.get("runbook_hint") or "{}"
    try:
        hint = json.loads(hint_str)
    except (json.JSONDecodeError, TypeError):
        hint = {}

    symptom = hint.get("symptom") or inc.get("suspected_root_cause") or "未知故障"
    service = hint.get("service", "")
    host = hint.get("host", "")

    gen = get_runbook_generator()
    result = gen.generate(symptom, service, host, max_docs=5)

    # 在 Runbook 顶部附加 incident 元信息
    incident_header = (
        f"> 关联 incident: `{incident_id}` 严重度: {inc['severity']}\n"
        f"> 告警数: {inc.get('alert_count', 0)}  根因推断: {inc.get('suspected_root_cause', '')}\n\n"
    )
    result["runbook_md"] = incident_header + result["runbook_md"]
    result["incident_id"] = incident_id

    if publish:
        slug = f"incident-{incident_id}"
        vc = get_version_control()
        title = f"Incident Runbook: {symptom[:60]}"
        vc.save_version(
            doc_key=f"wiki:{slug}",
            title=title,
            content=result["runbook_md"],
            author="incident-runbook-generator",
            change_summary=f"基于 incident {incident_id} 自动生成",
        )
        get_search_engine().index_document(
            f"wiki:{slug}",
            title,
            result["runbook_md"],
            "incident-runbook",
        )
        result["wiki_slug"] = slug
        result["wiki_published"] = True

    return result


@router.get("/events/incidents/{incident_id}/changes")
async def incident_changes(incident_id: str) -> dict:
    """查询 incident 关联的变更（反向查询）"""
    corr = get_change_correlator()
    items = corr.get_incident_changes(incident_id)
    return {"incident_id": incident_id, "changes": items, "count": len(items)}


@router.get("/events/incidents/{incident_id}/rollback-suggestion")
async def incident_rollback_suggestion(incident_id: str) -> dict:
    """基于 incident 关联变更，给出回滚建议"""
    corr = get_change_correlator()
    return corr.suggest_rollback(incident_id)
