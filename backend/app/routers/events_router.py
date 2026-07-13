"""事件关联 API（P2-2 + P2-2.2 incident 状态机）。

端点：
- POST /events/ingest
- POST /events/ingest/alertmanager            （P2-2.5: Alertmanager v4 webhook 接入）
- POST /events/correlate
- GET  /events/incidents
- GET  /events/incidents/{incident_id}
- POST /events/incidents/{incident_id}/close        （legacy，等价于 /resolve）
- POST /events/incidents/{incident_id}/ack
- POST /events/incidents/{incident_id}/investigate
- POST /events/incidents/{incident_id}/mitigate
- POST /events/incidents/{incident_id}/resolve
- POST /events/incidents/{incident_id}/transition    （通用迁移端点）
- GET  /events/incidents/states                      （查询状态机定义）
- POST /events/incidents/{incident_id}/runbook
- GET  /events/incidents/{incident_id}/changes
- GET  /events/incidents/{incident_id}/rollback-suggestion
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.aiops import (
    INCIDENT_STATES,
    INCIDENT_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    alertmanager_to_events,
    get_change_correlator,
    get_event_correlator,
    is_valid_state,
    should_resolve_incident,
)
from app.aiops.event_correlator import _get_db as get_events_db
from app.auth import verify_token
from app.config import get_settings
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
    result = corr.ingest(events)

    # 触发 webhook：event.ingested
    from app.webhooks import dispatch_event

    dispatch_event(
        "event.ingested",
        {
            "ingested": result.get("ingested", 0),
            "skipped_duplicates": result.get("skipped_duplicates", 0),
            "event_count": len(events),
        },
    )
    return result


@router.post("/events/ingest/alertmanager")
async def events_ingest_alertmanager(
    request: Request,
    payload: dict,
    token: str | None = Query(
        None,
        description="Alertmanager 入站 token（也可用 Authorization: Bearer 头）",
    ),
) -> dict:
    """接收 Prometheus Alertmanager v4 webhook 告警（P2-2.5）

    Alertmanager 标准 webhook receiver 配置：
        receivers:
          - name: opskg
            webhook_configs:
              - url: http://opskg:8000/events/ingest/alertmanager?token=xxx
                send_resolved: true

    认证（三选一，优先级递减）：
    1. alertmanager_ingest_token（专用，独立于 api_token）
    2. api_token（共享 legacy token）
    3. 二者皆空 → 开发模式放行

    支持的 token 传递方式：
    - Authorization: Bearer <token>  （推荐）
    - ?token=<token>                  （Alertmanager webhook_configs.url 携带）

    Body: Alertmanager v4 标准 payload（含 alerts 数组）
          或单 alert 对象（容错）

    Returns:
        {
          "ingested": N,
          "skipped_duplicates": M,
          "resolved": K,                # 触发 incident 自动迁移的数量
          "source": "alertmanager",
          "alerts_total": len(alerts)
        }
    """
    # ── 认证 ──
    import secrets as _secrets

    settings = get_settings()
    expected_am = settings.alertmanager_ingest_token
    expected_api = settings.api_token
    expected = expected_am or expected_api
    if expected:
        # 优先从 Authorization header 取
        auth_header = request.headers.get("authorization", "")
        bearer_token = ""
        if auth_header.lower().startswith("bearer "):
            bearer_token = auth_header[7:].strip()
        candidate = bearer_token or token
        # 直接与 alertmanager_ingest_token 或 api_token 常量时间比较
        # （verify_token_string 仅查 api_token，不支持 alertmanager 专用 token）
        ok = bool(candidate) and (
            (expected_am and _secrets.compare_digest(candidate, expected_am))
            or (expected_api and _secrets.compare_digest(candidate, expected_api))
        )
        if not ok:
            raise HTTPException(
                401,
                "Alertmanager webhook 认证失败",
                headers={"WWW-Authenticate": "Bearer"},
            )
    # else: 开发模式放行

    # ── payload 转换 ──
    try:
        events = alertmanager_to_events(payload)
    except ValueError as e:
        raise HTTPException(400, f"Alertmanager payload 解析失败：{e}") from e

    if not events:
        return {
            "ingested": 0,
            "skipped_duplicates": 0,
            "resolved": 0,
            "source": "alertmanager",
            "alerts_total": 0,
        }

    # ── 接入关联引擎 ──
    corr = get_event_correlator()
    result = corr.ingest(events)

    # ── resolved 自动迁移 ──
    # Alertmanager send_resolved: true 时，恢复的告警会重发同 fingerprint 的 alert
    # 对应已存在的 incident 应自动迁移到 resolved 状态
    resolved_count = 0
    for ev in events:
        if not should_resolve_incident(ev):
            continue
        attrs = ev.get("attributes") or {}
        fingerprint = attrs.get("fingerprint", "")
        alertname = attrs.get("alertname", "")
        if not fingerprint:
            continue
        # 按 fingerprint 反查 incident：扫描 alert_attributes 中匹配的 incident
        # 保守策略：仅迁移 status=open/ack/investigating/mitigated 的同 alertname incident
        try:
            conn = get_events_db()
            rows = conn.execute(
                """SELECT i.incident_id, i.status FROM incidents i
                   WHERE i.status IN ('open', 'ack', 'investigating', 'mitigated')
                   AND EXISTS (
                       SELECT 1 FROM events e
                       WHERE e.incident_id = i.incident_id
                       AND e.component = ?
                   )""",
                (alertname,),
            ).fetchall()
            for inc_id, _status in rows:
                try:
                    corr.transition_incident(
                        inc_id,
                        "resolved",
                        note=f"Alertmanager 自动恢复（fingerprint={fingerprint}）",
                        by="alertmanager",
                    )
                    resolved_count += 1
                except (KeyError, InvalidTransitionError):
                    # 状态已变化或 incident 不存在 — 跳过
                    continue
        except Exception:  # noqa: BLE001
            # 自动迁移失败不应阻塞 ingest 主流程
            pass

    # ── 触发 webhook ──
    from app.webhooks import dispatch_event

    dispatch_event(
        "event.ingested",
        {
            "ingested": result.get("ingested", 0),
            "skipped_duplicates": result.get("skipped_duplicates", 0),
            "event_count": len(events),
            "source": "alertmanager",
            "resolved": resolved_count,
        },
    )

    return {
        "ingested": result.get("ingested", 0),
        "skipped_duplicates": result.get("skipped_duplicates", 0),
        "resolved": resolved_count,
        "source": "alertmanager",
        "alerts_total": len(events),
    }


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
    result = corr.correlate(since_minutes=since, max_events=max_ev)

    # 触发 webhook：incident.created（每个新建 incident 一条）
    from app.webhooks import dispatch_event

    for inc in result.get("incidents", []):
        dispatch_event(
            "incident.created",
            {
                "incident_id": inc.get("incident_id"),
                "severity": inc.get("severity"),
                "alert_count": inc.get("alert_count", 0),
                "suspected_root_cause": inc.get("suspected_root_cause"),
                "hosts": inc.get("hosts", []),
                "services": inc.get("services", []),
            },
        )
    # 业务指标埋点
    from app.observability import record_business_metric

    incidents_created = len(result.get("incidents", []))
    if incidents_created > 0:
        record_business_metric(
            "incidents_created_total", float(incidents_created)
        )
    return result


@router.get("/events/incidents/states")
async def events_list_incident_states() -> dict:
    """查询 incident 状态机定义（P2-2.2）

    供前端渲染状态切换按钮、状态徽标颜色映射使用。
    """
    return {
        "states": INCIDENT_STATES,
        "transitions": {
            s: sorted(targets) for s, targets in INCIDENT_TRANSITIONS.items()
        },
        "terminal_states": sorted(TERMINAL_STATES),
        "legacy_aliases": {"closed": "resolved"},
    }


@router.get("/events/incidents")
async def events_list_incidents(
    status: str = "open",
    limit: int = 50,
) -> dict:
    """列出 incident

    Args:
        status: 状态过滤；支持 open/ack/investigating/mitigated/resolved/closed，
                传 "all" 或空串则不过滤
        limit: 返回数量上限
    """
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


# ────────── P2-2.2 状态机端点 ──────────


def _do_transition(
    incident_id: str, target_state: str, payload: dict | None
) -> dict:
    """通用迁移逻辑，被各具体状态端点复用"""
    payload = payload or {}
    note = str(payload.get("note", ""))
    by = str(payload.get("by", "")) or "api"
    assignee = payload.get("assignee")
    corr = get_event_correlator()
    try:
        updated = corr.transition_incident(
            incident_id, target_state, note=note, by=by
        )
    except KeyError:
        raise HTTPException(404, f"incident 不存在: {incident_id}")
    except InvalidTransitionError as e:
        raise HTTPException(409, str(e))

    # 可选：同时更新 assignee
    if assignee:
        from app.aiops.event_correlator import _get_db

        conn = _get_db()
        conn.execute(
            "UPDATE incidents SET assignee = ? WHERE incident_id = ?",
            (str(assignee), incident_id),
        )
        conn.commit()
        updated = corr.get_incident(incident_id)  # type: ignore[assignment]

    # 触发 webhook：incident.status_changed
    from app.webhooks import dispatch_event

    prev_status = (
        updated.get("previous_status") if isinstance(updated, dict) else None
    )
    dispatch_event(
        "incident.status_changed",
        {
            "incident_id": incident_id,
            "from": prev_status,
            "to": target_state,
            "by": by,
            "note": note,
        },
    )

    return {  # type: ignore[return-value]
        "incident_id": incident_id,
        "status": updated.get("status") if isinstance(updated, dict) else target_state,
        "transition_history": (
            updated.get("transition_history") if isinstance(updated, dict) else []
        ),
    }


@router.post(
    "/events/incidents/{incident_id}/transition",
    dependencies=[Depends(verify_token)],
)
async def events_transition_incident(
    incident_id: str, payload: dict = None
) -> dict:
    """通用状态迁移端点（P2-2.2）

    Body:
        target_state: 目标状态（必填，open/ack/investigating/mitigated/resolved）
        note: 迁移备注（可选）
        by: 操作人（可选，默认 "api"）
        assignee: 指派处理人（可选，同时更新 assignee 字段）
    """
    payload = payload or {}
    target = str(payload.get("target_state", "")).strip()
    if not target:
        raise HTTPException(400, "target_state 不能为空")
    if not is_valid_state(target) and target != "closed":
        raise HTTPException(
            400,
            f"非法目标状态: {target}。合法状态: {INCIDENT_STATES} (closed 为 legacy)",
        )
    return _do_transition(incident_id, target, payload)


@router.post(
    "/events/incidents/{incident_id}/ack",
    dependencies=[Depends(verify_token)],
)
async def events_ack_incident(
    incident_id: str, payload: dict = None
) -> dict:
    """Acknowledge incident：open → ack"""
    return _do_transition(incident_id, "ack", payload)


@router.post(
    "/events/incidents/{incident_id}/investigate",
    dependencies=[Depends(verify_token)],
)
async def events_investigate_incident(
    incident_id: str, payload: dict = None
) -> dict:
    """开始调查：→ investigating"""
    return _do_transition(incident_id, "investigating", payload)


@router.post(
    "/events/incidents/{incident_id}/mitigate",
    dependencies=[Depends(verify_token)],
)
async def events_mitigate_incident(
    incident_id: str, payload: dict = None
) -> dict:
    """已缓解：→ mitigated"""
    return _do_transition(incident_id, "mitigated", payload)


@router.post(
    "/events/incidents/{incident_id}/resolve",
    dependencies=[Depends(verify_token)],
)
async def events_resolve_incident(
    incident_id: str, payload: dict = None
) -> dict:
    """已解决：→ resolved（终态）"""
    return _do_transition(incident_id, "resolved", payload)


@router.post(
    "/events/incidents/{incident_id}/close", dependencies=[Depends(verify_token)]
)
async def events_close_incident(incident_id: str, note: str = "") -> dict:
    """关闭 incident（legacy 端点，等价于 /resolve）

    向后兼容：保留 note 作为 query 参数。
    """
    corr = get_event_correlator()
    try:
        corr.transition_incident(
            incident_id, "resolved", note=note, by="legacy-close"
        )
    except KeyError:
        raise HTTPException(404, f"incident 不存在: {incident_id}")
    except InvalidTransitionError as e:
        raise HTTPException(409, str(e))
    return {"incident_id": incident_id, "status": "resolved"}


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

    # 触发 webhook：runbook.generated
    from app.webhooks import dispatch_event

    dispatch_event(
        "runbook.generated",
        {
            "incident_id": incident_id,
            "symptom": symptom,
            "service": service,
            "host": host,
            "published": bool(publish),
            "wiki_slug": result.get("wiki_slug"),
            "matched_docs": result.get("matched_docs"),
        },
    )
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
