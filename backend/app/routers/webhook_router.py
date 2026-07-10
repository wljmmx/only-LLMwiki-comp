"""Webhook 订阅管理 API（Sprint 10+）。

端点：
- GET    /webhooks                         列出订阅
- POST   /webhooks                         创建订阅（返回明文 secret，仅此一次）
- GET    /webhooks/events                  列出可用事件类型目录
- GET    /webhooks/{sub_id}                获取订阅详情
- PUT    /webhooks/{sub_id}                更新订阅（url/events/description/active）
- DELETE /webhooks/{sub_id}                删除订阅
- POST   /webhooks/{sub_id}/rotate-secret  重置签名密钥
- POST   /webhooks/{sub_id}/test           发送 webhook.test 测试事件
- GET    /webhooks/{sub_id}/deliveries     列出该订阅的投递记录
- GET    /webhooks/deliveries              列出全部投递记录（可按 status 过滤）
- POST   /webhooks/deliveries/retry        手动触发一次到期重试扫描

S15-2 告警路由规则引擎：
- GET    /webhooks/rules                   列出所有路由规则
- POST   /webhooks/rules                   创建路由规则
- GET    /webhooks/rules/{rule_id}         获取规则详情
- PUT    /webhooks/rules/{rule_id}         更新规则
- DELETE /webhooks/rules/{rule_id}         删除规则
- POST   /webhooks/rules/test              测试事件路由结果（dry-run）
- GET    /webhooks/silence                 列出所有静默窗口
- POST   /webhooks/silence                 创建静默窗口
- GET    /webhooks/silence/{window_id}     获取静默窗口详情
- PUT    /webhooks/silence/{window_id}     更新静默窗口
- DELETE /webhooks/silence/{window_id}     删除静默窗口
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import verify_token
from app.storage import get_webhook_store
from app.webhooks import EVENT_CATALOG, dispatch_event, get_webhook_manager

router = APIRouter()


# ────────── 事件目录 ──────────


@router.get("/webhooks/events")
async def list_events() -> dict:
    """列出可用事件类型目录（供前端展示与创建订阅时选择）"""
    return {
        "events": [
            {"type": k, "description": v}
            for k, v in sorted(EVENT_CATALOG.items())
        ],
        "count": len(EVENT_CATALOG),
    }


# ────────── 订阅 CRUD ──────────


@router.get("/webhooks")
async def list_subscriptions(active_only: bool = False) -> dict:
    """列出所有订阅（不返回 secret）"""
    store = get_webhook_store()
    subs = store.list_subscriptions(active_only=active_only)
    return {"subscriptions": subs, "count": len(subs)}


@router.post("/webhooks", dependencies=[Depends(verify_token)])
async def create_subscription(payload: dict) -> dict:
    """创建订阅

    Body:
        url: 必填，回调地址（http/https）
        events: 必填，订阅事件类型列表，支持 `*` / `incident.*` / 精确类型
        description: 可选，描述
        active: 可选，默认 true
        secret: 可选，不传则自动生成

    Returns:
        订阅记录（含明文 secret，仅此一次返回）
    """
    url = str(payload.get("url", "")).strip()
    events = payload.get("events")
    if not url:
        raise HTTPException(400, "url 必填")
    if not events or not isinstance(events, list):
        raise HTTPException(400, "events 必填且为数组")
    # 校验事件类型合法（允许通配符）
    valid_types = set(EVENT_CATALOG.keys())
    for ev in events:
        if ev == "*" or ev.endswith(".*"):
            continue
        if ev not in valid_types:
            raise HTTPException(
                400,
                f"非法事件类型: {ev}。合法类型见 /webhooks/events",
            )

    store = get_webhook_store()
    try:
        sub = store.create_subscription(
            url=url,
            events=events,
            secret=payload.get("secret"),
            description=str(payload.get("description", "")),
            active=bool(payload.get("active", True)),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return sub


# ────────── 告警路由规则 CRUD（S15-2） ──────────
# 注意：这些静态路径必须在 /webhooks/{sub_id} 之前注册，否则会被 {sub_id} 捕获


@router.get("/webhooks/rules")
async def list_alert_rules(enabled_only: bool = False) -> dict:
    """列出所有告警路由规则（按 priority 升序）"""
    store = get_webhook_store()
    rules = store.list_alert_rules(enabled_only=enabled_only)
    return {"rules": rules, "count": len(rules)}


@router.post("/webhooks/rules", dependencies=[Depends(verify_token)])
async def create_alert_rule(payload: dict) -> dict:
    """创建告警路由规则

    Body:
        name: 必填，规则名称
        event_type_pattern: 必填，事件匹配模式（精确 / `incident.*` / `*`）
        description: 可选，描述
        severity: 可选，critical/warning/info/空=不限
        payload_matchers: 可选，[{"field":"host","op":"eq","value":"prod-01"}]
        target_subscription_ids: 可选，["wh_xxx"]，空=匹配所有订阅
        enabled: 可选，默认 true
        priority: 可选，默认 100（数字越小优先级越高）
    """
    name = str(payload.get("name", "")).strip()
    pattern = str(payload.get("event_type_pattern", "")).strip()
    if not name:
        raise HTTPException(400, "name 必填")
    if not pattern:
        raise HTTPException(400, "event_type_pattern 必填")
    matchers = payload.get("payload_matchers")
    if matchers is not None and not isinstance(matchers, list):
        raise HTTPException(400, "payload_matchers 必须为数组")
    targets = payload.get("target_subscription_ids")
    if targets is not None and not isinstance(targets, list):
        raise HTTPException(400, "target_subscription_ids 必须为数组")
    store = get_webhook_store()
    try:
        rule = store.create_alert_rule(
            name=name,
            event_type_pattern=pattern,
            description=str(payload.get("description", "")),
            severity=str(payload.get("severity", "")),
            payload_matchers=matchers or [],
            target_subscription_ids=targets or [],
            enabled=bool(payload.get("enabled", True)),
            priority=int(payload.get("priority", 100)),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return rule


@router.post("/webhooks/rules/test", dependencies=[Depends(verify_token)])
async def test_alert_route(payload: dict) -> dict:
    """测试事件路由结果（dry-run，不实际投递）

    Body:
        event_type: 必填，事件类型
        payload: 必填，事件 payload（dict）

    Returns:
        silenced: 是否被静默
        silenced_by: 命中的静默窗口
        matched_subscription_count: 候选订阅数
        routed_subscription_count: 经路由后的最终订阅数
        routed_subscription_ids: 最终订阅 ID 列表
        matched_rules: 命中的路由规则
    """
    event_type = str(payload.get("event_type", "")).strip()
    if not event_type:
        raise HTTPException(400, "event_type 必填")
    evt_payload = payload.get("payload")
    if not isinstance(evt_payload, dict):
        raise HTTPException(400, "payload 必须为对象")
    store = get_webhook_store()
    from app.webhooks.alert_router import AlertRouter

    router = AlertRouter(store)
    subs = store.list_subscriptions(active_only=True)
    from app.webhooks.manager import _event_matches

    matched = [s for s in subs if _event_matches(s.get("events", []), event_type)]
    return router.evaluate(event_type, evt_payload, matched)


@router.get("/webhooks/rules/{rule_id}")
async def get_alert_rule(rule_id: str) -> dict:
    """获取路由规则详情"""
    store = get_webhook_store()
    rule = store.get_alert_rule(rule_id)
    if not rule:
        raise HTTPException(404, f"规则不存在: {rule_id}")
    return rule


@router.put("/webhooks/rules/{rule_id}", dependencies=[Depends(verify_token)])
async def update_alert_rule(rule_id: str, payload: dict) -> dict:
    """更新路由规则（任意字段可选）"""
    store = get_webhook_store()
    matchers = payload.get("payload_matchers")
    if matchers is not None and not isinstance(matchers, list):
        raise HTTPException(400, "payload_matchers 必须为数组")
    targets = payload.get("target_subscription_ids")
    if targets is not None and not isinstance(targets, list):
        raise HTTPException(400, "target_subscription_ids 必须为数组")
    try:
        rule = store.update_alert_rule(
            rule_id,
            name=payload.get("name"),
            description=payload.get("description"),
            event_type_pattern=payload.get("event_type_pattern"),
            severity=payload.get("severity"),
            payload_matchers=matchers,
            target_subscription_ids=targets,
            enabled=payload.get("enabled"),
            priority=int(payload["priority"]) if payload.get("priority") is not None else None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not rule:
        raise HTTPException(404, f"规则不存在: {rule_id}")
    return rule


@router.delete("/webhooks/rules/{rule_id}", dependencies=[Depends(verify_token)])
async def delete_alert_rule(rule_id: str) -> dict:
    """删除路由规则"""
    store = get_webhook_store()
    ok = store.delete_alert_rule(rule_id)
    if not ok:
        raise HTTPException(404, f"规则不存在: {rule_id}")
    return {"deleted": True, "id": rule_id}


# ────────── 静默窗口 CRUD（S15-2） ──────────


@router.get("/webhooks/silence")
async def list_silence_windows(enabled_only: bool = False) -> dict:
    """列出所有静默窗口"""
    store = get_webhook_store()
    windows = store.list_silence_windows(enabled_only=enabled_only)
    return {"silence_windows": windows, "count": len(windows)}


@router.post("/webhooks/silence", dependencies=[Depends(verify_token)])
async def create_silence_window(payload: dict) -> dict:
    """创建静默窗口

    Body:
        name: 必填，窗口名称
        event_type_pattern: 必填，事件匹配模式
        start_time: 必填，起始时间（ISO8601 UTC）
        end_time: 必填，结束时间（ISO8601 UTC）
        reason: 可选，静默原因
        payload_matchers: 可选，payload 匹配条件
        enabled: 可选，默认 true
    """
    name = str(payload.get("name", "")).strip()
    pattern = str(payload.get("event_type_pattern", "")).strip()
    start_time = str(payload.get("start_time", "")).strip()
    end_time = str(payload.get("end_time", "")).strip()
    if not name:
        raise HTTPException(400, "name 必填")
    if not pattern:
        raise HTTPException(400, "event_type_pattern 必填")
    if not start_time or not end_time:
        raise HTTPException(400, "start_time 和 end_time 必填")
    matchers = payload.get("payload_matchers")
    if matchers is not None and not isinstance(matchers, list):
        raise HTTPException(400, "payload_matchers 必须为数组")
    store = get_webhook_store()
    try:
        window = store.create_silence_window(
            name=name,
            event_type_pattern=pattern,
            start_time=start_time,
            end_time=end_time,
            reason=str(payload.get("reason", "")),
            payload_matchers=matchers or [],
            enabled=bool(payload.get("enabled", True)),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return window


@router.get("/webhooks/silence/{window_id}")
async def get_silence_window(window_id: str) -> dict:
    """获取静默窗口详情"""
    store = get_webhook_store()
    window = store.get_silence_window(window_id)
    if not window:
        raise HTTPException(404, f"静默窗口不存在: {window_id}")
    return window


@router.put("/webhooks/silence/{window_id}", dependencies=[Depends(verify_token)])
async def update_silence_window(window_id: str, payload: dict) -> dict:
    """更新静默窗口（任意字段可选）"""
    store = get_webhook_store()
    matchers = payload.get("payload_matchers")
    if matchers is not None and not isinstance(matchers, list):
        raise HTTPException(400, "payload_matchers 必须为数组")
    try:
        window = store.update_silence_window(
            window_id,
            name=payload.get("name"),
            event_type_pattern=payload.get("event_type_pattern"),
            reason=payload.get("reason"),
            start_time=payload.get("start_time"),
            end_time=payload.get("end_time"),
            payload_matchers=matchers,
            enabled=payload.get("enabled"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not window:
        raise HTTPException(404, f"静默窗口不存在: {window_id}")
    return window


@router.delete("/webhooks/silence/{window_id}", dependencies=[Depends(verify_token)])
async def delete_silence_window(window_id: str) -> dict:
    """删除静默窗口"""
    store = get_webhook_store()
    ok = store.delete_silence_window(window_id)
    if not ok:
        raise HTTPException(404, f"静默窗口不存在: {window_id}")
    return {"deleted": True, "id": window_id}


# ────────── 投递记录（静态路径须在 {sub_id} 之前注册） ──────────


@router.get("/webhooks/deliveries")
async def list_all_deliveries(
    status: str | None = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
) -> dict:
    """列出全部投递记录（可按 status 过滤）"""
    store = get_webhook_store()
    items = store.list_deliveries(
        status=status, limit=limit, offset=offset
    )
    return {
        "deliveries": items,
        "count": len(items),
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/webhooks/deliveries/retry", dependencies=[Depends(verify_token)]
)
async def retry_pending_deliveries() -> dict:
    """手动触发一次到期重试扫描（不必等后台 worker）"""
    mgr = get_webhook_manager()
    n = await mgr.process_pending_retries()
    return {"processed": n}


# ────────── 订阅详情 CRUD（{sub_id} 动态路径） ──────────


@router.get("/webhooks/{sub_id}")
async def get_subscription(sub_id: str) -> dict:
    """获取订阅详情（不返回 secret）"""
    store = get_webhook_store()
    sub = store.get_subscription(sub_id)
    if not sub:
        raise HTTPException(404, f"订阅不存在: {sub_id}")
    return sub


@router.put("/webhooks/{sub_id}", dependencies=[Depends(verify_token)])
async def update_subscription(sub_id: str, payload: dict) -> dict:
    """更新订阅

    Body 任意字段可选：url / events / description / active
    """
    store = get_webhook_store()
    events = payload.get("events")
    if events is not None:
        if not isinstance(events, list):
            raise HTTPException(400, "events 必须为数组")
        valid_types = set(EVENT_CATALOG.keys())
        for ev in events:
            if ev == "*" or ev.endswith(".*"):
                continue
            if ev not in valid_types:
                raise HTTPException(400, f"非法事件类型: {ev}")
    try:
        sub = store.update_subscription(
            sub_id,
            url=payload.get("url"),
            events=events,
            description=payload.get("description"),
            active=payload.get("active"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not sub:
        raise HTTPException(404, f"订阅不存在: {sub_id}")
    return sub


@router.delete("/webhooks/{sub_id}", dependencies=[Depends(verify_token)])
async def delete_subscription(sub_id: str) -> dict:
    """删除订阅"""
    store = get_webhook_store()
    ok = store.delete_subscription(sub_id)
    if not ok:
        raise HTTPException(404, f"订阅不存在: {sub_id}")
    return {"deleted": True, "id": sub_id}


@router.post(
    "/webhooks/{sub_id}/rotate-secret", dependencies=[Depends(verify_token)]
)
async def rotate_secret(sub_id: str) -> dict:
    """重置签名密钥（旧 secret 立即失效）"""
    store = get_webhook_store()
    new_secret = store.rotate_secret(sub_id)
    if not new_secret:
        raise HTTPException(404, f"订阅不存在: {sub_id}")
    return {"id": sub_id, "secret": new_secret}


@router.post("/webhooks/{sub_id}/test", dependencies=[Depends(verify_token)])
async def test_subscription(sub_id: str) -> dict:
    """发送 webhook.test 测试事件到该订阅

    Returns:
        matched: 是否找到该订阅并触发
        delivery_id: 投递记录 ID（如创建）
    """
    store = get_webhook_store()
    sub = store.get_subscription(sub_id)
    if not sub:
        raise HTTPException(404, f"订阅不存在: {sub_id}")
    if not sub.get("active"):
        raise HTTPException(400, "订阅未启用，请先 active=true")

    matched = dispatch_event(
        "webhook.test",
        {
            "subscription_id": sub_id,
            "url": sub["url"],
            "message": "Webhook 测试事件",
            "sent_by": "webhook_router.test",
        },
    )
    return {
        "subscription_id": sub_id,
        "matched": matched,
        "message": (
            "测试事件已分发" if matched else "该订阅未订阅 webhook.test 事件"
        ),
    }


# ────────── 单订阅投递记录 ──────────


@router.get("/webhooks/{sub_id}/deliveries")
async def list_subscription_deliveries(
    sub_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """列出某订阅的投递记录"""
    store = get_webhook_store()
    sub = store.get_subscription(sub_id)
    if not sub:
        raise HTTPException(404, f"订阅不存在: {sub_id}")
    items = store.list_deliveries(
        subscription_id=sub_id, status=status, limit=limit, offset=offset
    )
    return {
        "subscription_id": sub_id,
        "deliveries": items,
        "count": len(items),
        "limit": limit,
        "offset": offset,
    }
