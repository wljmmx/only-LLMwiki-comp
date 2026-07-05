"""服务拓扑 API（P2-4 + P2-4.1 + P2-4.2 + P2-4.4）。

端点：
- POST /topology/rebuild
- POST /topology/infer         (P2-4.1) 共现推断
- POST /topology/merge-aliases (P2-4.2) 节点别名合并
- GET  /topology
- GET  /topology/export        (P2-4.4) Mermaid/Cytoscape 导出
- GET  /topology/nodes/{node_name}
- GET  /topology/impact/{node_name}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.aiops import get_topology_builder
from app.auth import verify_token

router = APIRouter()


@router.post("/topology/rebuild", dependencies=[Depends(verify_token)])
async def topology_rebuild(max_docs: int = 100) -> dict:
    """全量重建服务拓扑（扫描所有已上传文档）"""
    builder = get_topology_builder()
    return builder.rebuild(max_docs)


@router.post("/topology/infer", dependencies=[Depends(verify_token)])
async def topology_infer(
    min_cooccurrence: int = 2,
    min_confidence: float = 0.3,
) -> dict:
    """P2-4.1 基于节点 source_docs 共现强度推断缺失的拓扑边

    Query:
        min_cooccurrence: 共现文档数下限（默认 2）
        min_confidence: overlap coefficient 下限（默认 0.3）
    """
    builder = get_topology_builder()
    return builder.infer_cooccurrence_edges(
        min_cooccurrence=min_cooccurrence,
        min_confidence=min_confidence,
    )


@router.post("/topology/merge-aliases", dependencies=[Depends(verify_token)])
async def topology_merge_aliases() -> dict:
    """P2-4.2 合并别名节点（db1.example.com ↔ db1）

    检测同类型节点中 FQDN 与短名配对，保留短名为 canonical，
    合并 source_docs/occurrences，重定向边，删除别名节点。
    """
    builder = get_topology_builder()
    return builder.merge_aliases()


@router.get("/topology")
async def topology_get(
    node_type: str | None = None,
    relation: str | None = None,
) -> dict:
    """获取服务拓扑数据

    Query:
        node_type: Host|Service|Component
        relation: RUNS_ON|DEPENDS_ON|USES
    """
    builder = get_topology_builder()
    return builder.get_topology(node_type=node_type, relation=relation)


@router.get("/topology/export")
async def topology_export(fmt: str = "mermaid"):
    """P2-4.4 导出拓扑为 Mermaid 或 Cytoscape 格式

    Query:
        fmt: mermaid | cytoscape（默认 mermaid）
    """
    builder = get_topology_builder()
    try:
        content = builder.export(fmt=fmt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if fmt == "cytoscape":
        from fastapi.responses import JSONResponse
        import json
        return JSONResponse(content=json.loads(content))
    # mermaid 返回纯文本
    return PlainTextResponse(content=content, media_type="text/plain")


@router.get("/topology/nodes/{node_name}")
async def topology_node_neighbors(
    node_name: str,
    depth: int = 1,
) -> dict:
    """获取节点的邻居（上下游依赖）"""
    builder = get_topology_builder()
    return builder.get_neighbors(node_name, depth=depth)


@router.get("/topology/impact/{node_name}")
async def topology_impact(node_name: str) -> dict:
    """影响分析：给定节点故障，分析受影响的上下游"""
    builder = get_topology_builder()
    return builder.impact_analysis(node_name)
