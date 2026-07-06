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


# ────────── 投递记录 ──────────


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
