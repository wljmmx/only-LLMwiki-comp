"""流水线追踪与重处理 API

端点：
- GET  /pipeline/runs                       列出流水线运行记录（支持按 doc_id/status 过滤）
- GET  /pipeline/runs/{run_id}              获取单次运行详情（含阶段状态）
- GET  /pipeline/doc/{doc_id}/latest        获取文档最近的运行
- GET  /pipeline/runs/{run_id}/stages       列出该运行所有阶段产物元数据
- GET  /pipeline/runs/{run_id}/stages/{stage}    查看阶段输入+输出数据
- GET  /pipeline/runs/{run_id}/stages/{stage}/{direction}   查看单一输入或输出
- POST /pipeline/doc/{doc_id}/reprocess     从指定阶段重处理（自动复用上一 run 的产物）
- POST /pipeline/runs/{run_id}/reprocess     从指定阶段重处理特定 run
- DELETE /pipeline/runs/{run_id}             删除一次运行及其产物
- DELETE /pipeline/doc/{doc_id}/artifacts    删除文档所有阶段产物
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import verify_token
from app.storage import get_document_store, get_pipeline_tracker

router = APIRouter()

VALID_STAGES = ["parse", "extract", "compile", "index"]
VALID_DIRECTIONS = ["input", "output"]


class ReprocessRequest(BaseModel):
    """从指定阶段重处理的请求体"""

    start_from_stage: str = Field(
        ...,
        description=f"从此阶段开始重处理，可选: {VALID_STAGES}",
    )
    pipeline_run_id: str | None = Field(
        None,
        description="指定基于哪个 run 的产物继续重处理；None 时自动取文档最近一次 run",
    )
    force: bool = Field(False, description="强制重处理（即使内容未变）")


# ────────── 列表与查询 ──────────


@router.get("/pipeline/runs", dependencies=[Depends(verify_token)])
async def list_pipeline_runs(
    doc_id: str | None = Query(None, description="按文档 ID 过滤"),
    status: str | None = Query(None, description="按状态过滤: pending/running/done/error/cancelled"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """列出流水线运行记录"""
    tracker = get_pipeline_tracker()
    runs = tracker.list_runs(doc_id=doc_id, status=status, limit=limit, offset=offset)
    total = tracker.count_runs(doc_id=doc_id, status=status)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "runs": runs,
    }


@router.get("/pipeline/runs/{run_id}", dependencies=[Depends(verify_token)])
async def get_pipeline_run(run_id: str) -> dict:
    """获取单次运行详情（含 step 状态）"""
    store = get_document_store()
    run = store.get_pipeline_run(run_id)
    if not run:
        raise HTTPException(404, f"运行不存在: {run_id}")
    tracker = get_pipeline_tracker()
    run["artifacts"] = tracker.list_stage_artifacts(run_id)
    return run


@router.get("/pipeline/doc/{doc_id}/latest", dependencies=[Depends(verify_token)])
async def get_latest_pipeline_run(doc_id: str) -> dict:
    """获取文档最近的流水线运行记录"""
    store = get_document_store()
    run = store.get_latest_pipeline_run(doc_id)
    if not run:
        raise HTTPException(404, f"文档 {doc_id} 无运行记录")
    tracker = get_pipeline_tracker()
    run["artifacts"] = tracker.list_stage_artifacts(run["run_id"])
    return run


# ────────── 阶段产物查看 ──────────


@router.get("/pipeline/runs/{run_id}/stages", dependencies=[Depends(verify_token)])
async def list_stage_artifacts(run_id: str) -> dict:
    """列出某次运行的所有阶段产物元数据"""
    store = get_document_store()
    run = store.get_pipeline_run(run_id)
    if not run:
        raise HTTPException(404, f"运行不存在: {run_id}")
    tracker = get_pipeline_tracker()
    return {
        "run_id": run_id,
        "doc_id": run["doc_id"],
        "run_status": run["status"],
        "stages": run.get("steps", []),
        "artifacts": tracker.list_stage_artifacts(run_id),
    }


@router.get(
    "/pipeline/runs/{run_id}/stages/{stage}",
    dependencies=[Depends(verify_token)],
)
async def get_stage_data(run_id: str, stage: str) -> dict:
    """查看指定阶段的输入+输出数据

    用于核实每个阶段的处理结果。返回 input 和 output 两个字段。
    """
    if stage not in VALID_STAGES:
        raise HTTPException(400, f"无效阶段: {stage}，可选: {VALID_STAGES}")

    store = get_document_store()
    run = store.get_pipeline_run(run_id)
    if not run:
        raise HTTPException(404, f"运行不存在: {run_id}")

    tracker = get_pipeline_tracker()
    input_data = tracker.get_artifact(run_id, stage, "input")
    output_data = tracker.get_artifact(run_id, stage, "output")
    input_meta = tracker.get_artifact_meta(run_id, stage, "input")
    output_meta = tracker.get_artifact_meta(run_id, stage, "output")

    if not input_data and not output_data:
        raise HTTPException(
            404,
            f"运行 {run_id} 的阶段 {stage} 无产物数据",
        )

    return {
        "run_id": run_id,
        "doc_id": run["doc_id"],
        "stage": stage,
        "input": input_data,
        "output": output_data,
        "input_meta": input_meta,
        "output_meta": output_meta,
    }


@router.get(
    "/pipeline/runs/{run_id}/stages/{stage}/{direction}",
    dependencies=[Depends(verify_token)],
)
async def get_stage_artifact(
    run_id: str, stage: str, direction: str,
) -> dict:
    """查看单一输入或输出产物"""
    if stage not in VALID_STAGES:
        raise HTTPException(400, f"无效阶段: {stage}，可选: {VALID_STAGES}")
    if direction not in VALID_DIRECTIONS:
        raise HTTPException(
            400, f"无效方向: {direction}，可选: {VALID_DIRECTIONS}",
        )

    tracker = get_pipeline_tracker()
    payload = tracker.get_artifact(run_id, stage, direction)
    if payload is None:
        raise HTTPException(
            404,
            f"运行 {run_id} 阶段 {stage} 无 {direction} 产物",
        )
    meta = tracker.get_artifact_meta(run_id, stage, direction)
    return {
        "run_id": run_id,
        "stage": stage,
        "direction": direction,
        "payload": payload,
        "meta": meta,
    }


# ────────── 从任意阶段重处理 ──────────


@router.post(
    "/pipeline/doc/{doc_id}/reprocess",
    dependencies=[Depends(verify_token)],
)
async def reprocess_doc(doc_id: str, body: ReprocessRequest) -> dict:
    """从指定阶段重处理文档

    自动复用上一 run 中保存的更早阶段产物作为本 run 的输入：
    - start_from_stage=parse:    从头开始（与普通 recompile 等价）
    - start_from_stage=extract:  复用 parse 阶段的输出（跳过 parse）
    - start_from_stage=compile:  复用 parse + extract 输出
    - start_from_stage=index:    复用 parse + extract + compile 输出，只重建索引
    """
    if body.start_from_stage not in VALID_STAGES:
        raise HTTPException(
            400,
            f"无效 start_from_stage: {body.start_from_stage}，可选: {VALID_STAGES}",
        )

    # 校验文档存在
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")

    # 解析 run_id：未指定时自动取最近一次 run
    run_id = body.pipeline_run_id
    if run_id is None and body.start_from_stage != "parse":
        latest = store.get_latest_pipeline_run(doc_id)
        if not latest:
            raise HTTPException(
                400,
                f"文档 {doc_id} 无历史运行记录，无法从 {body.start_from_stage} 阶段重处理。"
                "请先执行一次完整编译（start_from_stage=parse）。",
            )
        run_id = latest["run_id"]

    # 调用 compiler
    from app.knowledge import get_wiki_compiler

    compiler = get_wiki_compiler()
    result = await compiler.compile_raw_to_wiki(
        doc_id,
        force=body.force,
        pipeline_run_id=run_id if body.start_from_stage != "parse" else None,
        start_from_stage=body.start_from_stage,
    )

    return {
        "doc_id": doc_id,
        "run_id": run_id,
        "start_from_stage": body.start_from_stage,
        "pages_created": result.pages_created,
        "pages_updated": result.pages_updated,
        "pages_unchanged": result.pages_unchanged,
        "slugs": result.slugs,
        "review_needed": result.review_needed,
        "stale_marked": result.stale_marked,
        "errors": result.errors,
        "index_rebuilt": result.index_rebuilt,
    }


@router.post(
    "/pipeline/runs/{run_id}/reprocess",
    dependencies=[Depends(verify_token)],
)
async def reprocess_run(run_id: str, body: ReprocessRequest) -> dict:
    """从指定阶段重处理特定 run（必须提供 start_from_stage）"""
    if body.start_from_stage not in VALID_STAGES:
        raise HTTPException(
            400,
            f"无效 start_from_stage: {body.start_from_stage}，可选: {VALID_STAGES}",
        )

    store = get_document_store()
    run = store.get_pipeline_run(run_id)
    if not run:
        raise HTTPException(404, f"运行不存在: {run_id}")

    doc_id = run["doc_id"]

    from app.knowledge import get_wiki_compiler

    compiler = get_wiki_compiler()
    result = await compiler.compile_raw_to_wiki(
        doc_id,
        force=body.force,
        pipeline_run_id=run_id if body.start_from_stage != "parse" else None,
        start_from_stage=body.start_from_stage,
    )

    return {
        "run_id": run_id,
        "doc_id": doc_id,
        "start_from_stage": body.start_from_stage,
        "pages_created": result.pages_created,
        "pages_updated": result.pages_updated,
        "pages_unchanged": result.pages_unchanged,
        "slugs": result.slugs,
        "errors": result.errors,
        "index_rebuilt": result.index_rebuilt,
    }


# ────────── 删除（维护）──────────


@router.delete("/pipeline/runs/{run_id}", dependencies=[Depends(verify_token)])
async def delete_pipeline_run(run_id: str) -> dict:
    """删除一次运行的阶段产物（保留 pipeline_runs 状态记录用于审计）"""
    tracker = get_pipeline_tracker()
    deleted = tracker.delete_run(run_id)
    return {"run_id": run_id, "artifacts_deleted": deleted}


@router.delete(
    "/pipeline/doc/{doc_id}/artifacts",
    dependencies=[Depends(verify_token)],
)
async def delete_doc_artifacts(doc_id: str) -> dict:
    """删除文档的所有阶段产物"""
    tracker = get_pipeline_tracker()
    deleted = tracker.delete_doc_artifacts(doc_id)
    return {"doc_id": doc_id, "artifacts_deleted": deleted}
