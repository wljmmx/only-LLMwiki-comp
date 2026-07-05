"""MCP 协议 API（P2-5）。

端点：
- POST /mcp        MCP (Model Context Protocol) HTTP 端点（JSON-RPC 2.0）
- GET  /mcp/tools  列出 MCP 工具（便捷查询，非 JSON-RPC）
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.mcp import handle_request as mcp_handle_request

router = APIRouter()


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> dict | list:
    """MCP (Model Context Protocol) HTTP 端点

    接收 JSON-RPC 2.0 请求，支持：
    - initialize: 握手
    - tools/list: 列出可用工具
    - tools/call: 调用工具

    可用工具：
    - search_knowledge: 搜索知识库
    - generate_runbook: 生成 Runbook
    - list_incidents / get_incident: 事件查询
    - suggest_rollback: 回滚建议
    - get_topology / impact_analysis: 拓扑查询
    - list_documents: 文档列表
    """
    body = await request.json()
    if isinstance(body, list):
        # 批量请求
        responses = []
        for req in body:
            resp = mcp_handle_request(req)
            if resp is not None:
                responses.append(resp)
        return responses
    else:
        response = mcp_handle_request(body)
        return response if response is not None else {}


@router.get("/mcp/tools")
async def mcp_tools_list() -> dict:
    """列出 MCP 工具（便捷查询，非 JSON-RPC）"""
    from app.mcp import list_tools

    return {"tools": list_tools()}
