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
- transition_incident: 迁移 incident 状态机（P2-2.2）
- suggest_rollback: 基于 incident 给出回滚建议
- get_topology: 获取服务拓扑
- infer_topology: 基于共现强度推断缺失的拓扑边（P2-4.1）
- merge_topology_aliases: 合并别名节点（P2-4.2）
- save_topology_snapshot: 保存拓扑快照（P2-4.3）
- diff_topology_snapshots: 对比两个拓扑快照（P2-4.3）
- impact_analysis: 影响分析
- list_documents: 列出已上传文档

暴露的资源（resources，P2-5.1）：
- wiki://index              — Wiki 索引页
- wiki://{slug}             — Wiki 单页（动态列出）
- topology://graph          — 服务拓扑全图
- documents://list          — 已上传文档列表
- incidents://open          — open 状态 incident 列表
- incidents://{id}          — 单个 incident 详情

暴露的 Prompt 模板（prompts，P2-5.1）：
- summarize_incident            — 总结 incident
- generate_runbook_from_symptom — 基于症状生成 Runbook
- wiki_qa                       — wiki 问答
- root_cause_analysis           — 根因分析
- review_change_impact          — 变更影响评审
"""

from __future__ import annotations

import json
import traceback
from typing import Any

import structlog

from app.aiops import (
    get_change_correlator,
    get_event_correlator,
    get_topology_builder,
)
from app.knowledge import get_runbook_generator

# P2-5.8 入参 JSON Schema 运行时校验
from app.mcp.schema_validator import fill_defaults, validate_args
from app.search import get_search_engine
from app.storage import get_document_store

logger = structlog.get_logger()

# MCP 协议版本
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "opskg-mcp-server"
SERVER_VERSION = "0.2.0"  # P2-5.1：升级，新增 resources/prompts 支持


# ────────── 工具定义 ──────────

TOOLS: list[dict] = [
    {
        "name": "search_knowledge",
        "description": "搜索 OpsKG 知识库中的运维文档。返回匹配的文档列表（含标题、片段、相关度分数）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "limit": {
                    "type": "integer",
                    "description": "返回结果数上限",
                    "default": 5,
                },
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
                "max_docs": {
                    "type": "integer",
                    "description": "检索文档数上限",
                    "default": 5,
                },
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
                "status": {
                    "type": "string",
                    "enum": [
                        "open",
                        "ack",
                        "investigating",
                        "mitigated",
                        "resolved",
                        "closed",
                        "all",
                    ],
                    "default": "open",
                    "description": "状态过滤；all 表示不过滤",
                },
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
        "name": "transition_incident",
        "description": (
            "迁移 incident 状态机。合法状态：open → ack → investigating → "
            "mitigated → resolved。允许前向跳跃与 reopen（resolved → open）。"
            "closed 为历史别名，等价于 resolved。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "incident_id": {
                    "type": "string",
                    "description": "incident ID",
                },
                "target_state": {
                    "type": "string",
                    "enum": [
                        "open",
                        "ack",
                        "investigating",
                        "mitigated",
                        "resolved",
                        "closed",
                    ],
                    "description": "目标状态",
                },
                "note": {
                    "type": "string",
                    "description": "迁移备注（写入 transition_history）",
                },
                "by": {
                    "type": "string",
                    "description": "操作人（默认 mcp）",
                },
            },
            "required": ["incident_id", "target_state"],
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
                "node_type": {
                    "type": "string",
                    "enum": ["Host", "Service", "Component"],
                },
                "relation": {
                    "type": "string",
                    "enum": ["RUNS_ON", "DEPENDS_ON", "USES"],
                },
            },
        },
    },
    {
        "name": "infer_topology",
        "description": (
            "P2-4.1 基于节点 source_docs 共现强度推断缺失的拓扑边。"
            "对共现文档数 >= min_cooccurrence 且 overlap coefficient >= min_confidence "
            "的节点对，若不存在显式边，则按节点类型推断 relation（Service→Host=RUNS_ON, "
            "Service→Component=USES, 同类型=DEPENDS_ON），插入 inferred=1 的边并返回。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_cooccurrence": {
                    "type": "integer",
                    "description": "共现文档数下限（默认 2）",
                    "default": 2,
                },
                "min_confidence": {
                    "type": "number",
                    "description": "overlap coefficient 下限（默认 0.3）",
                    "default": 0.3,
                },
            },
        },
    },
    {
        "name": "merge_topology_aliases",
        "description": (
            "P2-4.2 合并拓扑别名节点。检测同类型节点中 FQDN 与短名配对"
            "（如 db1.example.com ↔ db1），保留短名为 canonical，合并 source_docs/occurrences，"
            "重定向边到 canonical 节点，删除别名节点。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "save_topology_snapshot",
        "description": (
            "P2-4.3 保存当前拓扑快照。将当前 topology_nodes / topology_edges "
            "序列化为 JSON 存入 topology_snapshots 表，便于后续 diff 对比。"
            "建议在重大变更（rebuild / merge / infer）前后各保存一次。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "快照标签（如 'pre-rebuild'、'v1.0'），默认 'manual'",
                    "default": "manual",
                },
            },
        },
    },
    {
        "name": "diff_topology_snapshots",
        "description": (
            "P2-4.3 对比两个拓扑快照的差异。输出 added / removed / changed "
            "的节点和边（节点按 node_id 索引，边按 source+target+relation 索引）。"
            "建议先调用 save_topology_snapshot 保存快照。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "snapshot_id_a": {
                    "type": "integer",
                    "description": "旧快照 ID（基线）",
                },
                "snapshot_id_b": {
                    "type": "integer",
                    "description": "新快照 ID（对比）",
                },
            },
            "required": ["snapshot_id_a", "snapshot_id_b"],
        },
    },
    {
        "name": "impact_analysis",
        "description": "影响分析：给定节点故障，分析受影响的上下游服务。P2-4.7 增强冗余度分析：基于节点 metadata.replicas/capacity 计算 per-downstream 的 severity（critical=spof / degraded / minor）、blast_radius_score 与 single_points_of_failure。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_name": {
                    "type": "string",
                    "description": "节点名称（host/service/component）",
                },
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

# ────────── P2-5.7 工具 annotations ──────────
# MCP 2025-06-18 规范：annotations 提示工具的副作用特征
# - readOnlyHint: 工具不修改状态（纯查询）
# - destructiveHint: 工具可能破坏性修改状态（删除/覆盖）
# - idempotentHint: 重复调用效果相同（幂等）
_TOOL_ANNOTATIONS: dict[str, dict[str, bool]] = {
    "search_knowledge":            {"readOnlyHint": True},
    "generate_runbook":            {"readOnlyHint": True},   # 生成内容不修改状态
    "list_incidents":              {"readOnlyHint": True},
    "get_incident":                {"readOnlyHint": True},
    "transition_incident":         {"destructiveHint": True, "idempotentHint": True},  # 改状态但幂等
    "suggest_rollback":            {"readOnlyHint": True},
    "get_topology":                {"readOnlyHint": True},
    "infer_topology":              {"idempotentHint": True},  # INSERT OR IGNORE 幂等
    "merge_topology_aliases":      {"destructiveHint": True, "idempotentHint": True},  # 删节点但幂等
    "save_topology_snapshot":      {"idempotentHint": False},  # 每次产生新快照（非幂等）
    "diff_topology_snapshots":     {"readOnlyHint": True},     # 纯对比，不改状态
    "impact_analysis":             {"readOnlyHint": True},
    "list_documents":              {"readOnlyHint": True},
}

# 应用 annotations 到 TOOLS
for _tool in TOOLS:
    _tool["annotations"] = _TOOL_ANNOTATIONS.get(_tool["name"], {})


# ────────── 工具实现 ──────────


def _tool_search_knowledge(args: dict) -> str:
    query = args.get("query", "")
    limit = int(args.get("limit", 5))
    if not query:
        return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)
    results = get_search_engine().search(query, limit=limit)
    return json.dumps(
        {
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
        },
        ensure_ascii=False,
        indent=2,
    )


def _tool_generate_runbook(args: dict) -> str:
    symptom = args.get("symptom", "")
    if not symptom:
        return json.dumps({"error": "symptom 不能为空"}, ensure_ascii=False)
    service = args.get("service", "")
    host = args.get("host", "")
    max_docs = int(args.get("max_docs", 5))

    # P2-5.5 推送进度通知（SSE 端点会捕获；普通 JSON-RPC 路径为 no-op）
    from app.mcp.progress import emit_progress
    emit_progress("开始生成 Runbook", 0, 5)
    emit_progress("构造检索查询", 1, 5)

    result = get_runbook_generator().generate(symptom, service, host, max_docs)
    emit_progress(
        f"检索完成，命中 {result['stats']['docs_searched']} 篇文档",
        3,
        5,
    )
    emit_progress("生成 Runbook Markdown", 4, 5)
    emit_progress("完成", 5, 5)

    # 只返回 Markdown 和统计，省略完整 sources 以节省 token
    return json.dumps(
        {
            "runbook_md": result["runbook_md"],
            "stats": result["stats"],
            "sources_count": len(result["sources"]["docs"]),
        },
        ensure_ascii=False,
        indent=2,
    )


def _tool_list_incidents(args: dict) -> str:
    status = args.get("status", "open")
    limit = int(args.get("limit", 10))
    items = get_event_correlator().list_incidents(status, limit)
    return json.dumps(
        {
            "count": len(items),
            "incidents": [
                {
                    "incident_id": i["incident_id"],
                    "started_at": i["started_at"],
                    "severity": i["severity"],
                    "status": i.get("status", "open"),
                    "alert_count": i.get("alert_count", 0),
                    "suspected_root_cause": i.get("suspected_root_cause", ""),
                    "scope": i.get("scope", {}),
                    "assignee": i.get("assignee", ""),
                }
                for i in items
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _tool_transition_incident(args: dict) -> str:
    inc_id = args.get("incident_id", "")
    target = args.get("target_state", "")
    if not inc_id:
        return json.dumps({"error": "incident_id 不能为空"}, ensure_ascii=False)
    if not target:
        return json.dumps({"error": "target_state 不能为空"}, ensure_ascii=False)
    note = str(args.get("note", ""))
    by = str(args.get("by", "")) or "mcp"
    try:
        updated = get_event_correlator().transition_incident(
            inc_id, target, note=note, by=by
        )
    except KeyError:
        return json.dumps(
            {"error": f"incident 不存在: {inc_id}"}, ensure_ascii=False
        )
    except ValueError as e:
        # InvalidTransitionError 是 ValueError 子类
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    return json.dumps(
        {
            "incident_id": inc_id,
            "status": updated.get("status"),
            "acknowledged_at": updated.get("acknowledged_at"),
            "resolved_at": updated.get("resolved_at"),
            "ended_at": updated.get("ended_at"),
            "transition_history": updated.get("transition_history", []),
        },
        ensure_ascii=False,
        indent=2,
    )


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
    return json.dumps(
        {
            "stats": result["stats"],
            "nodes": result["nodes"][:30],
            "edges": result["edges"][:30],
        },
        ensure_ascii=False,
        indent=2,
    )


def _tool_infer_topology(args: dict) -> str:
    min_cooccur = int(args.get("min_cooccurrence", 2))
    min_conf = float(args.get("min_confidence", 0.3))
    result = get_topology_builder().infer_cooccurrence_edges(
        min_cooccurrence=min_cooccur,
        min_confidence=min_conf,
    )
    return json.dumps(
        {
            "considered_pairs": result["considered_pairs"],
            "inferred_edges": result["inferred_edges"],
            "skipped_existing": result["skipped_existing"],
            "edges": result["edges"][:50],  # 精简输出
        },
        ensure_ascii=False,
        indent=2,
    )


def _tool_merge_topology_aliases(args: dict) -> str:
    result = get_topology_builder().merge_aliases()
    return json.dumps(
        {
            "merged_pairs": result["merged_pairs"],
            "removed_nodes": result["removed_nodes"],
            "redirected_edges": result["redirected_edges"],
            "details": result["details"][:50],  # 精简输出
        },
        ensure_ascii=False,
        indent=2,
    )


def _tool_impact_analysis(args: dict) -> str:
    node_name = args.get("node_name", "")
    if not node_name:
        return json.dumps({"error": "node_name 不能为空"}, ensure_ascii=False)
    result = get_topology_builder().impact_analysis(node_name)
    # P2-4.7 增加 redundancy 字段输出
    redundancy = result.get("redundancy", {})
    return json.dumps(
        {
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
            # P2-4.7 冗余度影响分析
            "redundancy": {
                "node_replicas": redundancy.get("node_replicas", 1),
                "node_is_spof": redundancy.get("node_is_spof", False),
                "downstream_impacts": redundancy.get("downstream_impacts", []),
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def _tool_save_topology_snapshot(args: dict) -> str:
    label = str(args.get("label", "manual")) or "manual"
    builder = get_topology_builder()
    result = builder.save_snapshot(label)
    # 同时返回当前快照列表，便于查看 ID
    snapshots = builder.list_snapshots(limit=5)
    return json.dumps(
        {
            "saved": result,
            "recent_snapshots": snapshots,
        },
        ensure_ascii=False,
        indent=2,
    )


def _tool_diff_topology_snapshots(args: dict) -> str:
    try:
        snap_a = int(args["snapshot_id_a"])
        snap_b = int(args["snapshot_id_b"])
    except (KeyError, TypeError, ValueError):
        return json.dumps(
            {"error": "snapshot_id_a 和 snapshot_id_b 必须为整数"},
            ensure_ascii=False,
        )
    builder = get_topology_builder()
    result = builder.diff_snapshots(snap_a, snap_b)
    if "error" in result:
        return json.dumps(result, ensure_ascii=False)
    # 精简输出：added/removed/changed 列表只保留关键字段
    def _slim_nodes(items: list[dict]) -> list[dict]:
        return [
            {
                "node_id": n.get("node_id"),
                "name": n.get("name"),
                "node_type": n.get("node_type"),
                "occurrences": n.get("occurrences", 0),
            }
            for n in items
        ]

    def _slim_edges(items: list[dict]) -> list[dict]:
        return [
            {
                "source": e.get("source"),
                "target": e.get("target"),
                "relation": e.get("relation"),
                "inferred": e.get("inferred", 0),
                "confidence": e.get("confidence", 1.0),
            }
            for e in items
        ]

    slimmed = {
        "snapshot_a": result["snapshot_a"],
        "snapshot_b": result["snapshot_b"],
        "nodes": {
            "added": _slim_nodes(result["nodes"]["added"]),
            "removed": _slim_nodes(result["nodes"]["removed"]),
            "changed": result["nodes"]["changed"][:50],  # changed 含 before/after，原样返回
        },
        "edges": {
            "added": _slim_edges(result["edges"]["added"]),
            "removed": _slim_edges(result["edges"]["removed"]),
            "changed": result["edges"]["changed"][:50],
        },
        "summary": result["summary"],
    }
    return json.dumps(slimmed, ensure_ascii=False, indent=2)


def _tool_list_documents(args: dict) -> str:
    limit = int(args.get("limit", 20))
    docs = get_document_store().list(limit=limit)
    return json.dumps(
        {
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
        },
        ensure_ascii=False,
        indent=2,
    )


TOOL_HANDLERS = {
    "search_knowledge": _tool_search_knowledge,
    "generate_runbook": _tool_generate_runbook,
    "list_incidents": _tool_list_incidents,
    "get_incident": _tool_get_incident,
    "transition_incident": _tool_transition_incident,
    "suggest_rollback": _tool_suggest_rollback,
    "get_topology": _tool_get_topology,
    "infer_topology": _tool_infer_topology,
    "merge_topology_aliases": _tool_merge_topology_aliases,
    "save_topology_snapshot": _tool_save_topology_snapshot,
    "diff_topology_snapshots": _tool_diff_topology_snapshots,
    "impact_analysis": _tool_impact_analysis,
    "list_documents": _tool_list_documents,
}


# ────────── 资源（P2-5.1）──────────
#
# MCP resources 暴露 OpsKG 内部知识为 AI 客户端可读的 URI：
# - wiki://index              — Wiki 索引页
# - wiki://{slug}             — Wiki 单页（动态列出，从 wiki_index 拉取）
# - topology://graph          — 服务拓扑全图（JSON）
# - documents://list          — 已上传文档列表（JSON）
# - incidents://open          — 当前 open 状态 incident 列表（JSON）
# - incidents://{id}          — 单个 incident 详情（含状态机历史）


def _list_static_resources() -> list[dict]:
    """系统级静态资源"""
    return [
        {
            "uri": "wiki://index",
            "name": "Wiki Index",
            "description": "Wiki 索引页，列出所有 wiki 页面与孤岛候选",
            "mimeType": "text/markdown",
        },
        {
            "uri": "topology://graph",
            "name": "Service Topology",
            "description": "服务拓扑全图（Host/Service/Component 节点与依赖边）",
            "mimeType": "application/json",
        },
        {
            "uri": "documents://list",
            "name": "Document List",
            "description": "已上传到知识库的文档元信息列表",
            "mimeType": "application/json",
        },
        {
            "uri": "incidents://open",
            "name": "Open Incidents",
            "description": "当前所有 open 状态的 incident",
            "mimeType": "application/json",
        },
    ]


def _list_wiki_resources(limit: int = 200) -> list[dict]:
    """动态列出 wiki 页面作为资源"""
    try:
        from app.knowledge.wiki_index import list_wiki_pages

        pages = list_wiki_pages(limit=limit)
        return [
            {
                "uri": f"wiki://{p['slug']}",
                "name": p.get("title") or p["slug"],
                "description": f"Wiki 页面（type={p.get('type', 'concept')}）",
                "mimeType": "text/markdown",
            }
            for p in pages
            if p.get("slug") != "index"
        ]
    except Exception as e:
        logger.warning("mcp_list_wiki_resources_failed", error=str(e))
        return []


def list_resources() -> list[dict]:
    """列出所有可用资源（静态 + 动态 wiki 页面）"""
    return _list_static_resources() + _list_wiki_resources()


def _read_resource(uri: str) -> dict:
    """读取资源内容，返回 MCP resources/read 响应结构"""
    # wiki://index
    if uri == "wiki://index":
        from app.knowledge.wiki_index import get_index

        idx = get_index()
        text = idx["content"] if idx else "# Wiki Index (空)"
        return {
            "contents": [
                {"uri": uri, "mimeType": "text/markdown", "text": text}
            ]
        }

    # wiki://{slug}
    if uri.startswith("wiki://"):
        slug = uri[len("wiki://") :]
        if not slug:
            raise ValueError("wiki:// URI 缺少 slug")
        from app.knowledge.wiki_index import _key_from_slug
        from app.storage.version_control import get_version_control

        vc = get_version_control()
        latest = vc.get_latest(_key_from_slug(slug))
        if not latest:
            raise ValueError(f"未找到 wiki 页面: {slug}")
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/markdown",
                    "text": latest["content"],
                }
            ]
        }

    # topology://graph
    if uri == "topology://graph":
        result = get_topology_builder().get_topology()
        text = json.dumps(
            {
                "stats": result["stats"],
                "nodes": result["nodes"][:100],
                "edges": result["edges"][:100],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": text,
                }
            ]
        }

    # documents://list
    if uri == "documents://list":
        docs = get_document_store().list(limit=200)
        text = json.dumps(
            [
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
            ensure_ascii=False,
            indent=2,
        )
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": text,
                }
            ]
        }

    # incidents://open 或 incidents://{id}
    if uri.startswith("incidents://"):
        target = uri[len("incidents://") :]
        if target in ("", "open"):
            items = get_event_correlator().list_incidents("open", 100)
            text = json.dumps(items, ensure_ascii=False, indent=2, default=str)
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": text,
                    }
                ]
            }
        # 单个 incident
        inc = get_event_correlator().get_incident(target)
        if not inc:
            raise ValueError(f"未找到 incident: {target}")
        text = json.dumps(inc, ensure_ascii=False, indent=2, default=str)
        return {
            "contents": [
                {"uri": uri, "mimeType": "application/json", "text": text}
            ]
        }

    raise ValueError(f"未知资源 URI: {uri}")


# ────────── Prompt 模板（P2-5.1）──────────
#
# MCP prompts 暴露 OpsKG 内部能力为预定义的 prompt 模板，AI 客户端
# 调用 prompts/get 后拿到已渲染的 messages，直接喂给本地 LLM 即可。


PROMPTS: list[dict] = [
    {
        "name": "summarize_incident",
        "description": "总结 incident 的根因、影响范围、处置过程与改进建议",
        "arguments": [
            {
                "name": "incident_id",
                "description": "incident ID",
                "required": True,
            },
        ],
    },
    {
        "name": "generate_runbook_from_symptom",
        "description": "基于故障现象从知识库召回相关文档并生成 Runbook",
        "arguments": [
            {
                "name": "symptom",
                "description": "故障现象描述",
                "required": True,
            },
            {"name": "service", "description": "受影响服务（可选）"},
            {"name": "host", "description": "受影响主机（可选）"},
        ],
    },
    {
        "name": "wiki_qa",
        "description": "基于 wiki 知识库回答问题（Karpathy LLM Wiki 范式）",
        "arguments": [
            {"name": "question", "description": "用户问题", "required": True},
        ],
    },
    {
        "name": "root_cause_analysis",
        "description": "基于 incident 关联事件与拓扑影响，做根因分析",
        "arguments": [
            {
                "name": "incident_id",
                "description": "incident ID",
                "required": True,
            },
        ],
    },
    {
        "name": "review_change_impact",
        "description": "评审变更可能造成的影响，给出风险等级与回滚建议",
        "arguments": [
            {"name": "change_id", "description": "变更 ID", "required": True},
        ],
    },
]


def _get_prompt(name: str, arguments: dict) -> dict:
    """渲染 prompt 模板，返回 MCP prompts/get 响应结构"""
    args = arguments or {}

    if name == "summarize_incident":
        inc_id = args.get("incident_id", "")
        if not inc_id:
            raise ValueError("incident_id 不能为空")
        inc = get_event_correlator().get_incident(inc_id)
        if not inc:
            raise ValueError(f"incident 不存在: {inc_id}")
        changes = get_change_correlator().get_incident_changes(inc_id)
        prompt_text = (
            "请总结以下 incident 的根因、影响范围、处置过程与改进建议：\n\n"
            f"## Incident\n```json\n{json.dumps(inc, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
            f"## 关联变更\n```json\n{json.dumps(changes, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
            "## 输出要求\n"
            "1. 根因分析（一段话）\n"
            "2. 影响范围（列表）\n"
            "3. 处置时间线（按 transition_history）\n"
            "4. 改进建议（3-5 条）\n"
        )
        return {
            "description": f"总结 incident {inc_id}",
            "messages": [
                {
                    "role": "user",
                    "content": {"type": "text", "text": prompt_text},
                }
            ],
        }

    if name == "generate_runbook_from_symptom":
        symptom = args.get("symptom", "")
        if not symptom:
            raise ValueError("symptom 不能为空")
        service = args.get("service", "")
        host = args.get("host", "")
        gen = get_runbook_generator()
        result = gen.generate(symptom, service, host, max_docs=5)
        prompt_text = (
            "基于以下召回的 Runbook 草稿，请润色并补全：\n\n"
            f"## 故障现象\n{symptom}\n服务: {service} 主机: {host}\n\n"
            f"## 召回的 Runbook 草稿\n{result.get('runbook_md', '')}\n\n"
            "## 输出要求\n请输出最终版 Runbook，包含：概述、排查步骤、处置方案、关键配置参数、来源"
        )
        return {
            "description": f"生成 Runbook: {symptom[:60]}",
            "messages": [
                {
                    "role": "user",
                    "content": {"type": "text", "text": prompt_text},
                }
            ],
        }

    if name == "wiki_qa":
        import asyncio

        question = args.get("question", "")
        if not question:
            raise ValueError("question 不能为空")
        # recall_pages 是 async，MCP handler 是 sync，用临时事件循环驱动
        hits: list = []
        try:
            from app.knowledge.wiki_query import recall_pages

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 已在事件循环中（罕见），降级为空召回
                    hits = []
                else:
                    hits = loop.run_until_complete(
                        recall_pages(question, limit=5)
                    )
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    hits = loop.run_until_complete(
                        recall_pages(question, limit=5)
                    )
                finally:
                    loop.close()
        except Exception as e:
            logger.warning("mcp_wiki_qa_recall_failed", error=str(e))
            hits = []
        hits_text = (
            "\n".join(
                f"- [[{h.slug}]] {h.title} (score={h.score:.4f})\n  {h.snippet[:200]}"
                for h in hits
            )
            or "(无召回)"
        )
        prompt_text = (
            "你是 OpsKG Wiki 管理员。基于已编译的 wiki 页面回答问题。\n\n"
            f"## 用户问题\n{question}\n\n"
            f"## 召回的 wiki 页面\n{hits_text}\n\n"
            "## 输出要求\n"
            "1. 直接回答问题\n"
            "2. 引用相关页面时用 [[slug]] 标注\n"
            "3. 若 wiki 不足，明确指出缺口"
        )
        return {
            "description": f"Wiki Q&A: {question[:60]}",
            "messages": [
                {
                    "role": "user",
                    "content": {"type": "text", "text": prompt_text},
                }
            ],
        }

    if name == "root_cause_analysis":
        inc_id = args.get("incident_id", "")
        if not inc_id:
            raise ValueError("incident_id 不能为空")
        inc = get_event_correlator().get_incident(inc_id)
        if not inc:
            raise ValueError(f"incident 不存在: {inc_id}")
        scope = inc.get("scope") or {}
        hosts = scope.get("hosts", [])
        services = scope.get("services", [])
        topo_analyses = []
        for n in (hosts + services)[:5]:
            try:
                ana = get_topology_builder().impact_analysis(n)
                topo_analyses.append({"node": n, "analysis": ana})
            except Exception as e:
                logger.warning(
                    "mcp_rca_topo_failed", node=n, error=str(e)
                )
        prompt_text = (
            "请基于 incident 与拓扑影响分析，推断根因：\n\n"
            f"## Incident\n```json\n{json.dumps(inc, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
            f"## 拓扑影响分析\n```json\n{json.dumps(topo_analyses, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
            "## 输出要求\n"
            "1. 候选根因列表（按可能性排序）\n"
            "2. 每个候选的依据\n"
            "3. 推荐的进一步排查步骤"
        )
        return {
            "description": f"根因分析: {inc_id}",
            "messages": [
                {
                    "role": "user",
                    "content": {"type": "text", "text": prompt_text},
                }
            ],
        }

    if name == "review_change_impact":
        change_id = args.get("change_id", "")
        if not change_id:
            raise ValueError("change_id 不能为空")
        ch_corr = get_change_correlator()
        change = ch_corr.get_change(change_id)
        if not change:
            # 兜底：从 list 里找
            for c in ch_corr.list_changes(limit=1000):
                if c.get("id") == change_id:
                    change = c
                    break
        if not change:
            raise ValueError(f"变更不存在: {change_id}")
        prompt_text = (
            "请评审以下变更可能造成的影响：\n\n"
            f"## 变更\n```json\n{json.dumps(change, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
            "## 输出要求\n"
            "1. 风险等级（low/medium/high/critical）\n"
            "2. 影响范围\n"
            "3. 回滚建议（具体命令或步骤）\n"
            "4. 监控指标建议"
        )
        return {
            "description": f"变更影响评审: {change_id}",
            "messages": [
                {
                    "role": "user",
                    "content": {"type": "text", "text": prompt_text},
                }
            ],
        }

    raise ValueError(f"未知 prompt: {name}")


# ────────── JSON-RPC 处理 ──────────


def _get_tool_schema(tool_name: str) -> dict | None:
    """P2-5.8 从 TOOLS 列表中查询工具的 inputSchema

    Args:
        tool_name: 工具名

    Returns:
        工具的 inputSchema dict，未找到返回 None
    """
    for tool in TOOLS:
        if tool.get("name") == tool_name:
            return tool.get("inputSchema")
    return None


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
                    "resources": {"listChanged": False},
                    "prompts": {"listChanged": False},
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
                    req_id,
                    -32601,
                    f"未知工具: {tool_name}",
                )
            # P2-5.8 入参 JSON Schema 运行时校验
            tool_schema = _get_tool_schema(tool_name)
            if tool_schema:
                validation_errors = validate_args(tool_args, tool_schema)
                if validation_errors:
                    return _error_response(
                        req_id,
                        -32602,
                        "Invalid Params: " + "; ".join(validation_errors),
                    )
                # 填充 default 字段
                tool_args = fill_defaults(tool_args, tool_schema)
            handler = TOOL_HANDLERS[tool_name]
            text_result = handler(tool_args)
            result = {
                "content": [{"type": "text", "text": text_result}],
                "isError": False,
            }
        elif method == "ping":
            result = {}
        elif method == "resources/list":
            # P2-5.1：列出资源（静态 + 动态 wiki 页面）
            result = {"resources": list_resources()}
        elif method == "resources/read":
            # P2-5.1：读取资源内容
            uri = params.get("uri", "")
            if not uri:
                return _error_response(req_id, -32602, "uri 不能为空")
            try:
                result = _read_resource(uri)
            except ValueError as e:
                return _error_response(req_id, -32602, str(e))
        elif method == "prompts/list":
            # P2-5.1：列出 prompt 模板
            result = {"prompts": PROMPTS}
        elif method == "prompts/get":
            # P2-5.1：渲染 prompt 模板
            prompt_name = params.get("name", "")
            prompt_args = params.get("arguments", {})
            try:
                result = _get_prompt(prompt_name, prompt_args)
            except ValueError as e:
                return _error_response(req_id, -32602, str(e))
        else:
            return _error_response(req_id, -32601, f"未知方法: {method}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    except Exception as e:
        logger.error(
            "mcp_handle_error",
            method=method,
            error=str(e),
            traceback=traceback.format_exc(),
        )
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


def list_prompts() -> list[dict]:
    """供外部 introspection 使用（P2-5.1）"""
    return PROMPTS
