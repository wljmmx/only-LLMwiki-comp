"""实时协作 API（S15-5）

WebSocket 端点：
    WS /realtime/collab/{slug}?token=<token>
    WS /realtime/graph?token=<token>     KNOW-12: 图谱变更实时推送

    鉴权：query 参数 token，复用 verify_token_string（与 HTTP Bearer 同语义）
    开发模式（未配置 OPSKG_API_TOKEN）允许匿名连接

HTTP 状态查询端点：
    GET /realtime/rooms                  列出所有房间状态
    GET /realtime/rooms/{slug}           获取指定房间状态

HTTP 历史事件端点（S16-6）：
    GET /realtime/events/{slug}          查询历史事件（分页 + 增量同步）
    GET /realtime/events/{slug}/count    统计事件总数
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.auth import verify_token_string
from app.auth.models import get_auth_store
from app.realtime import CollabRoomFull, get_collab_hub
from app.realtime.collab_event_store import get_collab_event_store

router = APIRouter()


# ────────── HTTP 状态查询 ──────────


@router.get("/realtime/status")
async def realtime_status() -> dict:
    """全局实时协作状态摘要（E2E 兼容端点）

    返回在线房间数和总连接数，供前端 / E2E 测试探测协作服务是否可用。
    """
    hub = get_collab_hub()
    rooms = hub.list_rooms()
    total_conns = sum(r.get("online_count", 0) for r in rooms)
    return {
        "status": "ok",
        "rooms": len(rooms),
        "connections": total_conns,
    }


@router.get("/realtime/rooms")
async def list_rooms() -> dict:
    """列出所有协作房间状态（监控 / 调试用）"""
    hub = get_collab_hub()
    rooms = hub.list_rooms()
    return {"rooms": rooms, "count": len(rooms)}


@router.get("/realtime/rooms/{slug}")
async def get_room(slug: str) -> dict:
    """获取指定 slug 房间的状态"""
    hub = get_collab_hub()
    state = hub.get_room_state(slug)
    if not state:
        raise HTTPException(404, f"房间不存在或无人在线: {slug}")
    return state


@router.get("/realtime/room/{slug}/state")
async def get_room_state_compat(slug: str) -> dict:
    """兼容 E2E 测试的房间状态端点（单数路径别名）"""
    return await get_room(slug)


# ────────── HTTP 历史事件查询（S16-6） ──────────


@router.get("/realtime/events/{slug}")
async def list_collab_events(
    slug: str,
    limit: int = Query(default=100, ge=1, le=500, description="返回条数上限（1-500）"),
    before_id: int | None = Query(
        default=None, ge=1, description="分页游标：仅返回 id < before_id 的事件"
    ),
    since_timestamp: float | None = Query(
        default=None,
        ge=0,
        description="增量同步：仅返回 timestamp > since_timestamp 的事件（秒级）",
    ),
) -> dict:
    """查询某 slug 的协作历史事件（S16-6 协作历史回放）

    两种查询模式（互斥）：
    1. **分页模式**（默认）：按 id 倒序返回最新事件，配合 before_id 游标实现"加载更多"
    2. **增量模式**：传入 since_timestamp，按时间升序返回该时间点之后的事件

    返回结构：
        {
            "slug": "...",
            "events": [...],      # 事件列表
            "has_more": bool,      # 是否还有更多历史
            "count": int,          # 本次返回条数
            "total": int           # 该 slug 事件总数（用于显示）
        }
    """
    store = get_collab_event_store()

    if since_timestamp is not None:
        result = store.list_events_since(slug, since_timestamp, limit=limit)
    else:
        result = store.list_events(slug, limit=limit, before_id=before_id)

    total = store.count_events(slug)

    return {
        "slug": slug,
        "events": result["events"],
        "has_more": result["has_more"],
        "count": result["count"],
        "total": total,
    }


@router.get("/realtime/events/{slug}/count")
async def count_collab_events(slug: str) -> dict:
    """统计某 slug 的协作事件总数（S16-6，轻量查询）"""
    store = get_collab_event_store()
    return {"slug": slug, "count": store.count_events(slug)}


# ────────── WebSocket 协作端点 ──────────


def _resolve_user(token: str | None) -> dict[str, Any] | None:
    """根据 token 解析用户信息

    返回 None 表示认证失败；返回 dict 包含 user_id/username/display_name/role。
    开发模式（anonymous）返回一个虚拟用户。
    """
    identity = verify_token_string(token)
    if identity is None:
        return None

    # 开发模式
    if identity == "anonymous":
        return {
            "user_id": "anon",
            "username": "anonymous",
            "display_name": "匿名用户",
            "role": "admin",  # dev 模式放行所有权限
        }

    # legacy 共享 token
    if identity == "user":
        return {
            "user_id": "legacy",
            "username": "legacy",
            "display_name": "Legacy 共享 Token",
            "role": "admin",
        }

    # session token：identity 格式 "user:<username>"
    if identity.startswith("user:"):
        username = identity[5:]
        try:
            store = get_auth_store()
            user = store.get_user(username)
            if user:
                return {
                    "user_id": f"user:{username}",
                    "username": username,
                    "display_name": user.get("display_name") or username,
                    "role": user.get("role", "viewer"),
                }
        except Exception:  # noqa: BLE001
            pass
        # 兜底：identity 已校验通过，但用户对象不可达
        return {
            "user_id": f"user:{username}",
            "username": username,
            "display_name": username,
            "role": "viewer",
        }

    return None


@router.websocket("/realtime/collab/{slug}")
async def collab_ws(
    websocket: WebSocket,
    slug: str,
    token: str | None = Query(default=None),
) -> None:
    """Wiki 页面协作 WebSocket 端点

    协议见 app/realtime/collab_hub.py 模块文档。
    """
    user = _resolve_user(token)
    if user is None:
        await websocket.close(code=4401, reason="认证失败")
        return

    await websocket.accept()
    hub = get_collab_hub()
    user_id = user["user_id"]

    # 加入房间（S16-4：房间/连接数达上限时拒绝）
    try:
        await hub.connect(
            slug=slug,
            user_id=user_id,
            username=user["username"],
            display_name=user["display_name"],
            role=user["role"],
            ws=websocket,
        )
    except CollabRoomFull as e:
        # 推送 error 给客户端，便于前端展示"房间已满"提示
        try:
            await websocket.send_json({"type": "error", "reason": e.reason, "message": e.message})
        except Exception:  # noqa: BLE001
            pass
        await websocket.close(code=4029, reason=e.reason)
        return

    try:
        while True:
            message = await websocket.receive_json()
            msg_type = message.get("type")
            payload = message.get("payload", {})

            if msg_type == "heartbeat":
                hub.touch_heartbeat(slug, user_id)
                # 单独回执，便于客户端测量 RTT
                await websocket.send_json({"type": "heartbeat_ack"})

            elif msg_type == "acquire_lock":
                ok = await hub.acquire_lock(slug, user_id)
                if ok:
                    await websocket.send_json(
                        {"type": "lock_acquired_ack", "user_id": user_id}
                    )

            elif msg_type == "release_lock":
                await hub.release_lock(slug, user_id)

            elif msg_type == "edit_event":
                await hub.relay_edit_event(slug, user_id, payload)

            elif msg_type == "cursor":
                await hub.relay_cursor(slug, user_id, payload)

            else:
                await websocket.send_json(
                    {"type": "error", "message": f"未知消息类型: {msg_type}"}
                )

    except WebSocketDisconnect:
        # 客户端主动断开
        pass
    except Exception:  # noqa: BLE001
        # 其他异常：确保连接被清理
        pass
    finally:
        await hub.disconnect(slug, user_id)
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


# ────────── WebSocket 图谱变更推送（KNOW-12）──────────


@router.websocket("/realtime/graph")
async def graph_ws(
    websocket: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    """KNOW-12: 图谱变更实时推送 WebSocket 端点

    订阅 graph_event_bus，将图谱变更事件（upsert/delete/relation_upsert）
    实时推送给前端 GraphView，实现增量更新而非全量刷新。

    协议：
        服务端 → 客户端：
            {"type": "graph_changed", "action": "upsert|delete|...", "entity_id": "...",
             "entity_type": "...", "source_doc_id": "...", "timestamp": 123.456}
            {"type": "connected", "subscribers": N}  # 连接成功
            {"type": "heartbeat_ack"}                 # 心跳回执

        客户端 → 服务端：
            {"type": "heartbeat"}  # 心跳保活
    """
    user = _resolve_user(token)
    if user is None:
        await websocket.close(code=4401, reason="认证失败")
        return

    await websocket.accept()

    from app.realtime.graph_event_bus import get_graph_event_bus

    bus = get_graph_event_bus()

    # 发送连接成功消息
    try:
        await websocket.send_json({
            "type": "connected",
            "subscribers": bus.subscriber_count(),
        })
    except Exception:  # noqa: BLE001
        return

    # 订阅事件总线 + 同时监听客户端消息（心跳）
    import asyncio

    async def _consume_events() -> None:
        async for event in bus.subscribe():
            try:
                await websocket.send_json({
                    "type": "graph_changed",
                    "action": event.action,
                    "entity_id": event.entity_id,
                    "entity_type": event.entity_type,
                    "changed_fields": event.changed_fields,
                    "source_doc_id": event.source_doc_id,
                    "timestamp": event.timestamp,
                })
            except Exception:  # noqa: BLE001
                break

    async def _receive_messages() -> None:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "heartbeat":
                try:
                    await websocket.send_json({"type": "heartbeat_ack"})
                except Exception:  # noqa: BLE001
                    break

    event_task = asyncio.create_task(_consume_events())
    recv_task = asyncio.create_task(_receive_messages())

    try:
        # 任一任务结束（断连/异常）即退出
        done, pending = await asyncio.wait(
            {event_task, recv_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        for task in (event_task, recv_task):
            if not task.done():
                task.cancel()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
