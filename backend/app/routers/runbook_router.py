"""文档生成 API（W7）+ Runbook 自动生成 API（P2-1 + P3-5）。

端点：
- POST /doc/generate                    多智能体文档生成（完整流水线）
- POST /doc/generate-from-knowledge     从知识图谱检索上下文 → 文档生成
- POST /runbook/generate                基于知识库自动生成故障处理 Runbook（模板）
- POST /runbook/generate-llm            P3-5: 规则召回 + LLM 编译成 wiki 风格 Runbook
- GET  /runbook/preview                 预览 Runbook（GET 版本）
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import verify_token
from app.knowledge import get_graph_store, get_pipeline, get_runbook_generator
from app.search import get_search_engine
from app.storage import get_version_control

router = APIRouter()


class DocGenRequest(BaseModel):
    request: str
    context: str = ""
    max_iterations: int | None = None


def _sse_event(event_type: str, data: dict) -> str:
    """格式化 SSE 事件帧"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ────────── W7 文档生成 API ──────────

@router.post("/doc/generate", dependencies=[Depends(verify_token)])
async def generate_document(req: DocGenRequest) -> dict:
    """多智能体文档生成（完整流水线）"""
    pipeline = get_pipeline()
    state = await pipeline.generate(
        request=req.request,
        context=req.context,
        max_iterations=req.max_iterations,
    )
    return {
        "document": state.get("final_document", ""),
        "outline": state.get("outline", []),
        "sections": [
            {"title": s.get("title", ""), "content": s.get("content", "")[:500]}
            for s in state.get("sections", [])
        ],
        "iterations": state.get("iteration", 0),
        "token_usage": state.get("token_usage", 0),
        "error": state.get("error", ""),
    }


@router.post("/doc/generate/stream", dependencies=[Depends(verify_token)])
async def generate_document_stream(req: DocGenRequest, request: Request):
    """P2-5.5: SSE 流式文档生成 — 实时推送 6 阶段进度

    SSE 事件序列（来自 doc_generator.PipelineStage）：
        event: stage_start  — {"stage":"intent", "message":"分析需求意图...", "iteration":0}
        event: stage_done   — {"stage":"intent", "token_usage":120, "error":null}
        event: stage_start  — {"stage":"outline", ...}
        event: stage_done   — {"stage":"outline", "sections_total":5, ...}
        event: stage_start  — {"stage":"generate", "sections_total":5, ...}
        event: section_start — {"stage":"generate", "section_index":0, "section_total":5, "section_title":"..."}
        event: section_done  — {"stage":"generate", "section_index":1, "section_total":5, ...}
        ...
        event: stage_done   — {"stage":"generate", "sections_completed":5, ...}
        event: stage_start  — {"stage":"review", "iteration":1, ...}
        event: stage_done   — {"stage":"review", "decision":"accept|reject", ...}
        [如 reject → stage_start("modify") → stage_done("modify") → stage_start("review") ...]
        event: stage_start  — {"stage":"proofread", ...}
        event: stage_done   — {"stage":"proofread", ...}
        event: done         — {"document":"...", "outline":[...], "iterations":1, "token_usage":1234, ...}
        event: error        — {"stage":"...", "message":"...", "retryable":true}

    P2-4: 客户端断连时取消生成（通过 request.is_disconnected()）。
    """
    pipeline = get_pipeline()
    cancel_token = request.is_disconnected

    async def event_gen():
        total_start = datetime.now(timezone.utc)
        loop = asyncio.get_event_loop()
        # 跨线程安全的事件队列
        ev_queue: asyncio.Queue = asyncio.Queue()

        def on_progress(stage: str, data: dict) -> None:
            """doc_generator 同步回调 → 线程安全入队"""
            try:
                loop.call_soon_threadsafe(ev_queue.put_nowait, (stage, data))
            except Exception:  # noqa: BLE001
                pass

        async def run_generate():
            try:
                result = await pipeline.generate(
                    request=req.request,
                    context=req.context,
                    max_iterations=req.max_iterations,
                    on_progress=on_progress,
                )
                await ev_queue.put(("__result__", result))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                await ev_queue.put(("__error__", str(e)))

        task = asyncio.create_task(run_generate())

        try:
            while True:
                # 客户端断连取消
                if await _check_cancel(cancel_token):
                    task.cancel()
                    return

                try:
                    stage, data = await asyncio.wait_for(ev_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                if stage == "__result__":
                    state = data
                    total_ms = int(
                        (datetime.now(timezone.utc) - total_start).total_seconds() * 1000
                    )
                    yield _sse_event("done", {
                        "total_ms": total_ms,
                        "document": state.get("final_document", ""),
                        "outline": state.get("outline", []),
                        "sections": [
                            {"title": s.get("title", ""), "content": s.get("content", "")[:500]}
                            for s in state.get("sections", [])
                        ],
                        "iterations": state.get("iteration", 0),
                        "token_usage": state.get("token_usage", 0),
                        "error": state.get("error", ""),
                    })
                    break
                elif stage == "__error__":
                    yield _sse_event("error", {
                        "stage": "generate",
                        "message": data,
                        "retryable": True,
                    })
                    break
                else:
                    # 透传 doc_generator 的进度事件
                    yield _sse_event(stage, data)
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _check_cancel(cancel_token) -> bool:
    """检查客户端是否断连"""
    try:
        return bool(await cancel_token())
    except Exception:  # noqa: BLE001
        return False


@router.post("/doc/generate-from-knowledge", dependencies=[Depends(verify_token)])
async def generate_from_knowledge(req: DocGenRequest) -> dict:
    """从知识图谱检索上下文 → 文档生成"""
    pipeline = get_pipeline()

    # 尝试从图谱检索相关上下文
    context = req.context
    if not context:
        try:
            store = get_graph_store()
            # 搜索与请求相关的实体
            search_results = store.search_entities(req.request[:50], limit=10)
            if search_results:
                context_parts = []
                for r in search_results:
                    entity = store.query_entity(r["name"])
                    if entity:
                        context_parts.append(f"实体: {r['name']} (类型: {r['type']})")
                        for k, v in entity.items():
                            if k not in ("name", "entity_type", "updated_at"):
                                context_parts.append(f"  {k}: {v}")
                context = "\n".join(context_parts)
        except Exception:
            pass  # Neo4j 不可用时跳过

    state = await pipeline.generate(
        request=req.request,
        context=context,
        max_iterations=req.max_iterations,
    )
    return {
        "document": state.get("final_document", ""),
        "outline": state.get("outline", []),
        "context_used": bool(context),
        "iterations": state.get("iteration", 0),
        "token_usage": state.get("token_usage", 0),
        "error": state.get("error", ""),
    }


# ────────── P2-1 Runbook 自动生成 API ──────────


@router.post("/runbook/generate", dependencies=[Depends(verify_token)])
async def runbook_generate(payload: dict) -> dict:
    """基于知识库自动生成故障处理 Runbook

    Body:
        symptom: 故障现象描述（必填）
        service: 受影响服务（可选）
        host: 受影响主机（可选）
        max_docs: 检索文档数上限（默认 5）
        publish: 是否同时发布为 Wiki（默认 false）
        wiki_slug: Wiki slug（默认 auto:runbook-<timestamp>）
    """
    symptom = (payload.get("symptom") or "").strip()
    if not symptom:
        raise HTTPException(400, "symptom 不能为空")
    service = payload.get("service", "") or ""
    host = payload.get("host", "") or ""
    max_docs = int(payload.get("max_docs", 5))
    publish = bool(payload.get("publish", False))

    gen = get_runbook_generator()
    try:
        result = gen.generate(symptom, service, host, max_docs)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # 可选：发布为 Wiki
    if publish:
        import time

        slug = payload.get("wiki_slug") or f"runbook-{int(time.time())}"
        vc = get_version_control()
        title = f"Runbook: {symptom[:60]}"
        vc.save_version(
            doc_key=f"wiki:{slug}",
            title=title,
            content=result["runbook_md"],
            author="runbook-generator",
            change_summary=f"自动生成: {symptom}",
        )
        get_search_engine().index_document(
            f"wiki:{slug}",
            title,
            result["runbook_md"],
            "runbook",
        )
        result["wiki_slug"] = slug
        result["wiki_published"] = True

    return result


@router.get("/runbook/preview")
async def runbook_preview(
    symptom: str,
    service: str = "",
    host: str = "",
    max_docs: int = 5,
) -> dict:
    """预览 Runbook（GET 版本，便于浏览器/curl 快速调用）

    返回 Markdown 原文 + 来源统计，不写入 Wiki。
    """
    if not symptom.strip():
        raise HTTPException(400, "symptom 不能为空")
    gen = get_runbook_generator()
    return gen.generate(symptom.strip(), service, host, max_docs)


@router.post("/runbook/generate-llm", dependencies=[Depends(verify_token)])
async def runbook_generate_llm(payload: dict) -> dict:
    """P3-5: 规则召回 + LLM 编译成 wiki 风格 Runbook

    与 /runbook/generate 相同的召回流程，但正文由 LLM 编译为连贯的 wiki 风格。
    LLM 不可用时退化为模板生成（与 /runbook/generate 一致）。

    Body:
        symptom: 故障现象描述（必填）
        service: 受影响服务（可选）
        host: 受影响主机（可选）
        max_docs: 检索文档数上限（默认 5）
        publish: 是否同时发布为 Wiki（默认 false）
        wiki_slug: Wiki slug（默认 auto:runbook-<timestamp>）

    Returns:
        与 /runbook/generate 相同的 dict，额外含 "llm_compiled": bool
    """
    symptom = (payload.get("symptom") or "").strip()
    if not symptom:
        raise HTTPException(400, "symptom 不能为空")
    service = payload.get("service", "") or ""
    host = payload.get("host", "") or ""
    max_docs = int(payload.get("max_docs", 5))
    publish = bool(payload.get("publish", False))

    gen = get_runbook_generator()
    try:
        result = await gen.generate_async(symptom, service, host, max_docs)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # 可选：发布为 Wiki
    if publish:
        import time

        slug = payload.get("wiki_slug") or f"runbook-{int(time.time())}"
        vc = get_version_control()
        title = f"Runbook: {symptom[:60]}"
        vc.save_version(
            doc_key=f"wiki:{slug}",
            title=title,
            content=result["runbook_md"],
            author="runbook-generator-llm",
            change_summary=f"P3-5 LLM 编译: {symptom}",
        )
        get_search_engine().index_document(
            f"wiki:{slug}",
            title,
            result["runbook_md"],
            "runbook",
        )
        result["wiki_slug"] = slug
        result["wiki_published"] = True

    return result
