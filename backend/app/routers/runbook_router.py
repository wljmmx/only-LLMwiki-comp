"""文档生成 API（W7）+ Runbook 自动生成 API（P2-1）。

端点：
- POST /doc/generate                    多智能体文档生成（完整流水线）
- POST /doc/generate-from-knowledge     从知识图谱检索上下文 → 文档生成
- POST /runbook/generate                基于知识库自动生成故障处理 Runbook
- GET  /runbook/preview                 预览 Runbook（GET 版本）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
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
