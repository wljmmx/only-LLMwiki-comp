"""服务拓扑 API（P2-4 + P2-4.1 + P2-4.2 + P2-4.3 + P2-4.4 + P2-4.6 + P2-4.7）。

端点：
- POST /topology/rebuild
- POST /topology/infer         (P2-4.1) 共现推断
- POST /topology/merge-aliases (P2-4.2) 节点别名合并
- POST /topology/snapshots     (P2-4.3) 保存快照
- GET  /topology/snapshots     (P2-4.3) 列出快照
- GET  /topology/snapshots/{id} (P2-4.3) 快照详情
- POST /topology/diff          (P2-4.3) 对比两个快照
- GET  /topology
- GET  /topology/export        (P2-4.4) Mermaid/Cytoscape 导出
- GET  /topology/nodes/{node_name}
- GET  /topology/impact/{node_name}    (P2-4.7) 影响分析含冗余度
- GET  /topology/node/{node_id}                 (P2-4.6) 节点详情
- PATCH /topology/node/{node_id}/metadata       (P2-4.6) 更新 metadata
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


# ────────── P2-4.3 拓扑快照与 diff ──────────


@router.post("/topology/snapshots", dependencies=[Depends(verify_token)])
async def topology_save_snapshot(label: str = "manual") -> dict:
    """P2-4.3 保存当前拓扑快照

    Query:
        label: 快照标签（默认 "manual"）
    """
    builder = get_topology_builder()
    return builder.save_snapshot(label)


@router.get("/topology/snapshots")
async def topology_list_snapshots(limit: int = 20) -> dict:
    """P2-4.3 列出历史快照（按时间倒序）

    Query:
        limit: 返回条数上限（默认 20）
    """
    builder = get_topology_builder()
    return {"snapshots": builder.list_snapshots(limit=limit)}


@router.get("/topology/snapshots/{snapshot_id}")
async def topology_get_snapshot(snapshot_id: int) -> dict:
    """P2-4.3 获取单个快照详情（含 nodes/edges）"""
    builder = get_topology_builder()
    snap = builder.get_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail=f"快照不存在: {snapshot_id}")
    return snap


@router.post("/topology/diff", dependencies=[Depends(verify_token)])
async def topology_diff_snapshots(
    snapshot_id_a: int,
    snapshot_id_b: int,
) -> dict:
    """P2-4.3 对比两个拓扑快照的差异

    Query:
        snapshot_id_a: 旧快照 ID（基线）
        snapshot_id_b: 新快照 ID（对比）

    返回 added / removed / changed 的节点与边。
    """
    builder = get_topology_builder()
    result = builder.diff_snapshots(snapshot_id_a, snapshot_id_b)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


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
        import json

        from fastapi.responses import JSONResponse
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


# ────────── P2-4.6 节点 metadata 扩展 ──────────


@router.get("/topology/node/{node_id}")
async def topology_get_node(node_id: str) -> dict:
    """P2-4.6 获取节点详情（含 metadata）

    Path:
        node_id: 节点 ID（如 "Host:web1"），URL 中冒号需编码为 %3A
    """
    builder = get_topology_builder()
    node = builder.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"节点不存在: {node_id}")
    return node


@router.patch("/topology/node/{node_id}/metadata", dependencies=[Depends(verify_token)])
async def topology_update_node_metadata(
    node_id: str,
    metadata: dict,
    merge: bool = True,
) -> dict:
    """P2-4.6 更新节点 metadata（手动补充或修正）

    Path:
        node_id: 节点 ID（如 "Host:web1"），URL 中冒号需编码为 %3A

    Body:
        metadata: 要写入的 metadata 字段（JSON 对象）
        merge: True=合并（旧字段保留，新字段覆盖同名）；False=整体替换

    支持字段：ip / version / owner / env / region / capacity / replicas / 任意自定义字段
    """
    builder = get_topology_builder()
    result = builder.update_node_metadata(node_id, metadata, merge=merge)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
