"""MCP 协议 API（P2-5 + P2-5.2 token auth）。

端点：
- POST /mcp        MCP (Model Context Protocol) HTTP 端点（JSON-RPC 2.0）
- GET  /mcp/tools  列出 MCP 工具（便捷查询，非 JSON-RPC）

P2-5.2: 两个端点均走 verify_token，与 REST API 一致。
Header: Authorization: Bearer <OPSKG_API_TOKEN>
未配置 OPSKG_API_TOKEN 时放行（开发模式）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.auth import verify_token
from app.mcp import handle_request as mcp_handle_request

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


@router.get("/mcp/tools", dependencies=[Depends(verify_token)])
async def mcp_tools_list() -> dict:
    """列出 MCP 工具（便捷查询，非 JSON-RPC）

    认证：Authorization: Bearer <OPSKG_API_TOKEN>
    """
    from app.mcp import list_tools

    return {"tools": list_tools()}
