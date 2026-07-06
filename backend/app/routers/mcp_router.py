"""MCP 协议 API（P2-5 + P2-5.2 token auth + P2-5.5 SSE 传输）。

端点：
- POST /mcp        MCP (Model Context Protocol) HTTP 端点（JSON-RPC 2.0）
- POST /mcp/stream MCP SSE 端点（P2-5.5）：流式推送 progress + result
- GET  /mcp/tools  列出 MCP 工具（便捷查询，非 JSON-RPC）

P2-5.2: 三个端点均走 verify_token，与 REST API 一致。
Header: Authorization: Bearer <OPSKG_API_TOKEN>
未配置 OPSKG_API_TOKEN 时放行（开发模式）。

P2-5.5: /mcp/stream 端点：
- 接收单个 JSON-RPC 请求
- 工具执行过程中通过 SSE 推送 notifications/progress 事件
- 最终结果通过 event: result 推送
- 支持 _meta.progressToken（透传给客户端）
- 工具报错通过 event: error 推送
"""

from __future__ import annotations

import asyncio
import json
import queue
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.auth import verify_token
from app.mcp import handle_request as mcp_handle_request
from app.mcp.progress import (
    set_progress_callback,
    reset_progress_context,
    get_progress_token,
)

router = APIRouter()


@router.post("/mcp", dependencies=[Depends(verify_token)])
async def mcp_endpoint(request: Request) -> dict | list:
    """MCP (Model Context Protocol) HTTP 端点

    接收 JSON-RPC 2.0 请求，支持：
    - initialize: 握手
    - tools/list: 列出可用工具
    - tools/call: 调用工具

    认证：Authorization: Bearer <OPSKG_API_TOKEN>

    可用工具：
    - search_knowledge: 搜索知识库
    - generate_runbook: 生成 Runbook
    - list_incidents / get_incident / transition_incident: 事件查询与状态机
    - suggest_rollback: 回滚建议
    - get_topology / infer_topology / merge_topology_aliases / impact_analysis: 拓扑
    - list_documents: 文档列表
    """
    body = await request.json()
    if isinstance(body, list):
        # 批量请求（P2-5.6）
        responses = []
        for req in body:
            resp = mcp_handle_request(req)
            if resp is not None:
                responses.append(resp)
        return responses
    else:
        response = mcp_handle_request(body)
        return response if response is not None else {}


@router.post("/mcp/stream", dependencies=[Depends(verify_token)])
async def mcp_stream_endpoint(request: Request) -> StreamingResponse:
    """P2-5.5 MCP SSE 端点：流式推送 progress + result

    接收单个 JSON-RPC 2.0 请求（不支持批量），返回 text/event-stream。

    SSE 事件格式：
    - event: progress
      data: {"jsonrpc":"2.0","method":"notifications/progress",
             "params":{"progressToken":...,"progress":N,"total":M,"message":"..."}}
    - event: result
      data: <JSON-RPC response>
    - event: error
      data: {"error": "..."}

    客户端可在请求中传 _meta.progressToken，SSE 通知会透传该 token。

    认证：Authorization: Bearer <OPSKG_API_TOKEN>
    """
    body = await request.json()
    # SSE 仅支持单请求（非批量）
    if isinstance(body, list):
        async def err_gen():
            yield _format_sse(
                "error",
                {"error": "SSE 端点不支持批量请求，请改用 POST /mcp"},
            )
        return StreamingResponse(err_gen(), media_type="text/event-stream")

    # 提取 progressToken（来自 _meta.progressToken）
    progress_token = (body.get("_meta") or {}).get("progressToken")
    request_id = body.get("id")

    async def event_generator() -> AsyncGenerator[bytes, None]:
        # 进度事件队列：工具线程 → SSE 主循环
        ev_queue: queue.Queue = queue.Queue()
        loop = asyncio.get_event_loop()

        def progress_cb(message: str, current: int, total: int) -> None:
            """工具内部 emit_progress 触发此回调，推送 SSE progress 事件"""
            notification = {
                "jsonrpc": "2.0",
                "method": "notifications/progress",
                "params": {
                    "progressToken": progress_token,
                    "progress": current,
                    "total": total,
                    "message": message,
                },
            }
            ev_queue.put(("progress", notification))

        def run_tool() -> None:
            """在工作线程中执行工具，结果/异常入队"""
            set_progress_callback(progress_cb, token=progress_token)
            try:
                result = mcp_handle_request(body)
                ev_queue.put(("result", result))
            except Exception as e:
                ev_queue.put(("error", {"error": str(e)}))
            finally:
                reset_progress_context()
                ev_queue.put(("done", None))

        # 启动工具线程（不等待，立即开始流式输出）
        future = loop.run_in_executor(None, run_tool)

        try:
            while True:
                try:
                    event_type, payload = ev_queue.get_nowait()
                except queue.Empty:
                    # 没有事件，短暂等待
                    await asyncio.sleep(0.05)
                    continue

                if event_type == "progress":
                    yield _format_sse("progress", payload)
                elif event_type == "result":
                    yield _format_sse("result", payload)
                elif event_type == "error":
                    yield _format_sse("error", payload)
                elif event_type == "done":
                    break
        finally:
            # 确保工具线程结束（避免悬挂）
            if not future.done():
                future.cancel()
            reset_progress_context()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 不缓冲
        },
    )


def _format_sse(event: str, data: object) -> bytes:
    """格式化 SSE 事件块

    格式：
        event: <event>
        data: <json>

    """
    payload = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


@router.get("/mcp/tools", dependencies=[Depends(verify_token)])
async def mcp_tools_list() -> dict:
    """列出 MCP 工具（便捷查询，非 JSON-RPC）

    认证：Authorization: Bearer <OPSKG_API_TOKEN>
    """
    from app.mcp import list_tools

    return {"tools": list_tools()}
