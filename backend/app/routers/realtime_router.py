"""实时协作 API（S15-5）

WebSocket 端点：
    WS /realtime/collab/{slug}?token=<token>

    鉴权：query 参数 token，复用 verify_token_string（与 HTTP Bearer 同语义）
    开发模式（未配置 OPSKG_API_TOKEN）允许匿名连接

HTTP 状态查询端点：
    GET /realtime/rooms                  列出所有房间状态
    GET /realtime/rooms/{slug}           获取指定房间状态
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.auth import verify_token_string
from app.auth.models import get_auth_store
from app.realtime import CollabRoomFull, get_collab_hub

router = APIRouter()


# ────────── HTTP 状态查询 ──────────


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
