"""MCP (Model Context Protocol) Server — 将 OpsKG 能力暴露为 AI 可调用工具

实现轻量级 MCP 协议（JSON-RPC 2.0），不依赖官方 SDK。
支持两种传输：
- stdio：作为独立脚本运行（python -m app.mcp.server）
- HTTP/SSE：挂载到 FastAPI（POST /mcp）

暴露的工具（tools）：
- search_knowledge: 搜索知识库文档
- generate_runbook: 基于知识库生成 Runbook
- list_incidents: 列出最近 incident
- get_incident: 获取 incident 详情
- suggest_rollback: 基于 incident 给出回滚建议
- get_topology: 获取服务拓扑
- impact_analysis: 影响分析
- list_documents: 列出已上传文档
"""
from __future__ import annotations

import json
import traceback
from typing import Any

import structlog

from app.knowledge import get_runbook_generator
from app.aiops import (
    get_event_correlator, get_change_correlator, get_topology_builder,
)
from app.search import get_search_engine
from app.storage import get_document_store

logger = structlog.get_logger()

# MCP 协议版本
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "opskg-mcp-server"
SERVER_VERSION = "0.1.0"


# ────────── 工具定义 ──────────

TOOLS: list[dict] = [
    {
        "name": "search_knowledge",
        "description": "搜索 OpsKG 知识库中的运维文档。返回匹配的文档列表（含标题、片段、相关度分数）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "limit": {"type": "integer", "description": "返回结果数上限", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "generate_runbook",
        "description": "基于知识库自动生成故障处理 Runbook。聚合相关文档中的命令、配置参数、处置步骤。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symptom": {"type": "string", "description": "故障现象描述"},
                "service": {"type": "string", "description": "受影响服务（可选）"},
                "host": {"type": "string", "description": "受影响主机（可选）"},
                "max_docs": {"type": "integer", "description": "检索文档数上限", "default": 5},
            },
            "required": ["symptom"],
        },
    },
    {
        "name": "list_incidents",
        "description": "列出最近的 incident（关联后的故障分组）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "closed"], "default": "open"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "get_incident",
        "description": "获取 incident 详情，含所有关联告警和变更。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "incident ID"},
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "suggest_rollback",
        "description": "基于 incident 关联的变更，给出回滚建议（疑似根因变更）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "incident ID"},
            },
            "required": ["incident_id"],
        },
    },
    {
        "name": "get_topology",
        "description": "获取服务拓扑（Host/Service/Component 节点和它们之间的依赖关系）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_type": {"type": "string", "enum": ["Host", "Service", "Component"]},
                "relation": {"type": "string", "enum": ["RUNS_ON", "DEPENDS_ON", "USES"]},
            },
        },
    },
    {
        "name": "impact_analysis",
        "description": "影响分析：给定节点故障，分析受影响的上下游服务。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "节点名称（host/service/component）"},
            },
            "required": ["node_name"],
        },
    },
    {
        "name": "list_documents",
        "description": "列出已上传到知识库的文档。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
]


# ────────── 工具实现 ──────────

def _tool_search_knowledge(args: dict) -> str:
    query = args.get("query", "")
    limit = int(args.get("limit", 5))
    if not query:
        return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)
    results = get_search_engine().search(query, limit=limit)
    return json.dumps({
        "query": query,
        "count": len(results),
        "results": [
            {
                "doc_id": r.get("doc_id"),
                "title": r.get("title"),
                "snippet": r.get("snippet", "")[:300],
                "score": r.get("combined_score", 0.0),
            }
            for r in results
        ],
    }, ensure_ascii=False, indent=2)


def _tool_generate_runbook(args: dict) -> str:
    symptom = args.get("symptom", "")
    if not symptom:
        return json.dumps({"error": "symptom 不能为空"}, ensure_ascii=False)
    service = args.get("service", "")
    host = args.get("host", "")
    max_docs = int(args.get("max_docs", 5))
    result = get_runbook_generator().generate(symptom, service, host, max_docs)
    # 只返回 Markdown 和统计，省略完整 sources 以节省 token
    return json.dumps({
        "runbook_md": result["runbook_md"],
        "stats": result["stats"],
        "sources_count": len(result["sources"]["docs"]),
    }, ensure_ascii=False, indent=2)


def _tool_list_incidents(args: dict) -> str:
    status = args.get("status", "open")
    limit = int(args.get("limit", 10))
    items = get_event_correlator().list_incidents(status, limit)
    return json.dumps({
        "count": len(items),
        "incidents": [
            {
                "incident_id": i["incident_id"],
                "started_at": i["started_at"],
                "severity": i["severity"],
                "alert_count": i.get("alert_count", 0),
                "suspected_root_cause": i.get("suspected_root_cause", ""),
                "scope": i.get("scope", {}),
            }
            for i in items
        ],
    }, ensure_ascii=False, indent=2)


def _tool_get_incident(args: dict) -> str:
    inc_id = args.get("incident_id", "")
    if not inc_id:
        return json.dumps({"error": "incident_id 不能为空"}, ensure_ascii=False)
    inc = get_event_correlator().get_incident(inc_id)
    if not inc:
        return json.dumps({"error": f"incident 不存在: {inc_id}"}, ensure_ascii=False)
    # 附加关联变更
    changes = get_change_correlator().get_incident_changes(inc_id)
    inc["linked_changes"] = [
        {
            "change_id": c["id"],
            "change_type": c.get("change_type"),
            "timestamp": c.get("timestamp"),
            "service": c.get("service"),
            "host": c.get("host"),
            "correlation_score": c.get("correlation_score"),
            "description": c.get("description"),
        }
        for c in changes
    ]
    return json.dumps(inc, ensure_ascii=False, indent=2)


def _tool_suggest_rollback(args: dict) -> str:
    inc_id = args.get("incident_id", "")
    if not inc_id:
        return json.dumps({"error": "incident_id 不能为空"}, ensure_ascii=False)
    result = get_change_correlator().suggest_rollback(inc_id)
    return json.dumps(result, ensure_ascii=False, indent=2)


def _tool_get_topology(args: dict) -> str:
    node_type = args.get("node_type")
    relation = args.get("relation")
    result = get_topology_builder().get_topology(node_type=node_type, relation=relation)
    # 精简：只返回前 30 个节点和前 30 个边
    return json.dumps({
        "stats": result["stats"],
        "nodes": result["nodes"][:30],
        "edges": result["edges"][:30],
    }, ensure_ascii=False, indent=2)


def _tool_impact_analysis(args: dict) -> str:
    node_name = args.get("node_name", "")
    if not node_name:
        return json.dumps({"error": "node_name 不能为空"}, ensure_ascii=False)
    result = get_topology_builder().impact_analysis(node_name)
    return json.dumps({
        "node": result.get("node"),
        "impacted_downstream": [
            {"type": n["node_type"], "name": n["name"]}
            for n in result.get("impacted_downstream", [])
        ],
        "potential_root_cause": [
            {"type": n["node_type"], "name": n["name"]}
            for n in result.get("potential_root_cause", [])
        ],
        "summary": result.get("summary", {}),
    }, ensure_ascii=False, indent=2)


def _tool_list_documents(args: dict) -> str:
    limit = int(args.get("limit", 20))
    docs = get_document_store().list(limit=limit)
    return json.dumps({
        "count": len(docs),
        "documents": [
            {
                "doc_id": d["doc_id"],
                "filename": d.get("filename"),
                "title": d.get("title"),
                "format": d.get("format"),
                "status": d.get("status"),
                "size_bytes": d.get("size_bytes"),
            }
            for d in docs
        ],
    }, ensure_ascii=False, indent=2)


TOOL_HANDLERS = {
    "search_knowledge": _tool_search_knowledge,
    "generate_runbook": _tool_generate_runbook,
    "list_incidents": _tool_list_incidents,
    "get_incident": _tool_get_incident,
    "suggest_rollback": _tool_suggest_rollback,
    "get_topology": _tool_get_topology,
    "impact_analysis": _tool_impact_analysis,
    "list_documents": _tool_list_documents,
}


# ────────── JSON-RPC 处理 ──────────

def handle_request(request: dict) -> dict | None:
    """处理单个 JSON-RPC 请求

    Returns:
        响应 dict（成功或错误），如果是 notification 则返回 None
    """
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    # notification（无 id）不返回响应
    is_notification = req_id is None

    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            }
        elif method == "initialized" or method == "notifications/initialized":
            # 客户端确认初始化完成，无需响应
            return None
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            if tool_name not in TOOL_HANDLERS:
                return _error_response(
                    req_id, -32601, f"未知工具: {tool_name}",
                )
            handler = TOOL_HANDLERS[tool_name]
            text_result = handler(tool_args)
            result = {
                "content": [{"type": "text", "text": text_result}],
                "isError": False,
            }
        elif method == "ping":
            result = {}
        elif method == "resources/list":
            # 暂不支持 resources
            result = {"resources": []}
        elif method == "prompts/list":
            result = {"prompts": []}
        else:
            return _error_response(req_id, -32601, f"未知方法: {method}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    except Exception as e:
        logger.error("mcp_handle_error", method=method, error=str(e),
                     traceback=traceback.format_exc())
        if is_notification:
            return None
        return _error_response(req_id, -32603, f"内部错误: {e}")


def _error_response(req_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def list_tools() -> list[dict]:
    """供外部 introspection 使用"""
    return TOOLS
