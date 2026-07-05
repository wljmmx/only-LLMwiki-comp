"""服务拓扑 API（P2-4）。

端点：
- POST /topology/rebuild
- GET  /topology
- GET  /topology/nodes/{node_name}
- GET  /topology/impact/{node_name}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.aiops import get_topology_builder
from app.auth import verify_token

router = APIRouter()


@router.post("/topology/rebuild", dependencies=[Depends(verify_token)])
async def topology_rebuild(max_docs: int = 100) -> dict:
    """全量重建服务拓扑（扫描所有已上传文档）"""
    builder = get_topology_builder()
    return builder.rebuild(max_docs)


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
