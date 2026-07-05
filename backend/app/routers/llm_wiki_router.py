"""Karpathy LLM Wiki API（P0/P1 范式：知识编译 + 结构化沉淀）。

与既有 /wiki/{slug}（手动发布）区分：/llm-wiki/* 是 LLM 编译产物。

端点：
- POST /llm-wiki/ingest
- POST /llm-wiki/recompile/{doc_id}
- GET  /llm-wiki/pages
- GET  /llm-wiki/page/{slug}
- GET  /llm-wiki/index
- POST /llm-wiki/index/rebuild
- GET  /llm-wiki/stale
- POST /llm-wiki/drift/check/{doc_id}
- GET  /llm-wiki/orphans
- GET  /llm-wiki/deadlinks
- GET  /llm-wiki/backlinks/{slug}
- POST /llm-wiki/query
- GET  /llm-wiki/recall
- POST /llm-wiki/lint
- GET  /llm-wiki/lint/suggestions
- POST /llm-wiki/recompile-stale
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.auth import verify_token
from app.knowledge import (
    get_wiki_compiler,
    rebuild_index,
    get_index,
    list_wiki_pages,
    get_all_slugs,
    render_wikilinks_html,
    get_backlinks,
    get_outlinks,
    get_orphan_slugs,
    get_all_deadlinks,
    detect_drift,
    mark_pages_stale,
    list_stale_pages,
    recall_pages,
    get_wiki_qa_engine,
    lint_all,
    suggest_missing_pages,
    auto_recompile_stale,
)
from app.parsers import supported_formats
from app.routers.parsers_router import EXT_FMT_MAP
from app.storage import get_document_store, get_version_control

router = APIRouter()


@router.post("/llm-wiki/ingest", dependencies=[Depends(verify_token)])
async def llm_wiki_ingest(file: UploadFile = File(...)) -> dict:
    """上传 raw 文档 → 解析 → 抽取 → LLM 编译为 wiki 页面

    这是 Karpathy 范式的 Ingest 入口。返回编译结果（创建/更新/未变页面数）。

    与 /graph/upload 的区别：
    - /graph/upload：写入 Neo4j 图谱（实体-关系网络）
    - /llm-wiki/ingest：编译为 Markdown Wiki 页面（结构化沉淀）
    两者可串联：先 ingest 到 wiki，再 graph/upload 写图谱。
    """
    ext = (
        (file.filename or "").rsplit(".", 1)[-1].lower()
        if "." in (file.filename or "")
        else ""
    )
    fmt = EXT_FMT_MAP.get(ext, ext)
    if fmt not in supported_formats():
        raise HTTPException(400, f"不支持的格式: {fmt}")

    content = await file.read()
    store = get_document_store()
    meta = store.save(file.filename or "unknown", content, fmt)

    # 漂移检测：若 doc_id 已存在过编译记录且 checksum 变化 → 标记关联页面 stale
    drift = detect_drift(meta["doc_id"])
    if drift.changed:
        mark_pages_stale(drift.affected_slugs, drift.doc_id)

    # 调用 wiki 编译器
    compiler = get_wiki_compiler()
    result = await compiler.compile_raw_to_wiki(meta["doc_id"], force=drift.changed)
    return {
        "doc_id": meta["doc_id"],
        "filename": file.filename,
        "drift": {
            "changed": drift.changed,
            "affected_slugs": drift.affected_slugs,
            "old_checksum": drift.old_checksum[:12],
            "new_checksum": drift.new_checksum[:12],
        },
        "compile": {
            "pages_created": result.pages_created,
            "pages_updated": result.pages_updated,
            "pages_unchanged": result.pages_unchanged,
            "slugs": result.slugs,
            "review_needed": result.review_needed,
            "stale_marked": result.stale_marked,
            "index_rebuilt": result.index_rebuilt,
            "errors": result.errors,
        },
    }


@router.post("/llm-wiki/recompile/{doc_id}", dependencies=[Depends(verify_token)])
async def llm_wiki_recompile(doc_id: str, force: bool = True) -> dict:
    """强制重编译指定 raw 文档为 wiki 页面

    用于：raw 文档更新后手动触发重编译；或 lint 报告 stale 后批量重编译。
    """
    compiler = get_wiki_compiler()
    result = await compiler.compile_raw_to_wiki(doc_id, force=force)
    return {
        "doc_id": doc_id,
        "pages_created": result.pages_created,
        "pages_updated": result.pages_updated,
        "pages_unchanged": result.pages_unchanged,
        "slugs": result.slugs,
        "stale_marked": result.stale_marked,
        "errors": result.errors,
        "index_rebuilt": result.index_rebuilt,
    }


@router.get("/llm-wiki/pages")
async def llm_wiki_pages(limit: int = 200) -> dict:
    """列出所有 wiki 页面（按类型分组）"""
    pages = list_wiki_pages(limit=limit)
    by_type: dict[str, list[dict]] = {}
    for p in pages:
        by_type.setdefault(p["type"], []).append(p)
    return {
        "count": len(pages),
        "by_type": {t: len(v) for t, v in by_type.items()},
        "pages": pages,
    }


@router.get("/llm-wiki/page/{slug}")
async def llm_wiki_page_get(slug: str) -> dict:
    """获取单个 wiki 页面（含 frontmatter + backlinks + outlinks）"""
    vc = get_version_control()
    latest = vc.get_latest(f"wiki:{slug}")
    if not latest:
        raise HTTPException(404, f"wiki 页面不存在: {slug}")
    backlinks = get_backlinks(slug)
    outlinks = get_outlinks(slug)
    # 渲染 wikilink 为 HTML 供前端展示
    all_slugs = get_all_slugs()
    slug_to_url = {s: f"/llm-wiki/page/{s}" for s in all_slugs}
    html = render_wikilinks_html(latest["content"], slug_to_url)
    return {
        "slug": slug,
        "title": latest["title"],
        "version": latest["version"],
        "content": latest["content"],
        "content_html": html,
        "backlinks": [
            {"source": b.source_slug, "display": b.display, "count": b.count}
            for b in backlinks
        ],
        "outlinks": [
            {"target": o.target_slug, "display": o.display, "count": o.count}
            for o in outlinks
        ],
        "updated_at": latest["created_at"],
    }


@router.get("/llm-wiki/index")
async def llm_wiki_index() -> dict:
    """获取 index.md（导航中枢）"""
    idx = get_index()
    if not idx:
        # 自动重建一次
        result = rebuild_index()
        if not result.get("saved"):
            raise HTTPException(404, "wiki 暂无页面，无法生成 index")
        idx = get_index()
    return {
        "content": idx["content"],
        "version": idx["version"],
        "updated_at": idx["created_at"],
    }


@router.post("/llm-wiki/index/rebuild", dependencies=[Depends(verify_token)])
async def llm_wiki_index_rebuild() -> dict:
    """重建 index.md"""
    return rebuild_index()


@router.get("/llm-wiki/stale")
async def llm_wiki_stale() -> dict:
    """列出所有 stale wiki 页面（漂移检测产物）

    用于 Lint / 维护流程：raw 文档变化但 wiki 未重编译的页面。
    """
    pages = list_stale_pages()
    return {
        "count": len(pages),
        "pages": [
            {
                "slug": p.slug,
                "title": p.title,
                "type": p.type,
                "source_doc_id": p.source_doc_id,
                "old_checksum": p.old_checksum[:12],
                "new_checksum": p.new_checksum[:12],
                "last_compiled_at": p.last_compiled_at,
            }
            for p in pages
        ],
    }


@router.post("/llm-wiki/drift/check/{doc_id}", dependencies=[Depends(verify_token)])
async def llm_wiki_drift_check(doc_id: str) -> dict:
    """检测指定 raw 文档的漂移（不自动重编译）"""
    report = detect_drift(doc_id)
    return {
        "doc_id": doc_id,
        "changed": report.changed,
        "affected_slugs": report.affected_slugs,
        "old_checksum": report.old_checksum[:12],
        "new_checksum": report.new_checksum[:12],
    }


@router.get("/llm-wiki/orphans")
async def llm_wiki_orphans() -> dict:
    """列出孤岛 wiki 页面（无入链）"""
    all_slugs = get_all_slugs() - {"index"}
    orphans = get_orphan_slugs(all_slugs)
    return {"count": len(orphans), "orphans": orphans}


@router.get("/llm-wiki/deadlinks")
async def llm_wiki_deadlinks() -> dict:
    """列出所有死链（指向不存在 slug 的 [[wikilink]]）"""
    all_slugs = get_all_slugs()
    dead = get_all_deadlinks(all_slugs)
    return {
        "count": len(dead),
        "deadlinks": [
            {"source": d.source_slug, "target": d.slug, "line": d.line} for d in dead
        ],
    }


@router.get("/llm-wiki/backlinks/{slug}")
async def llm_wiki_backlinks(slug: str) -> dict:
    """查询某 wiki 页面的入链"""
    backs = get_backlinks(slug)
    return {
        "slug": slug,
        "count": len(backs),
        "backlinks": [
            {"source": b.source_slug, "display": b.display, "count": b.count}
            for b in backs
        ],
    }


# ────────── P1-2 Wiki-based Q&A ──────────


@router.post("/llm-wiki/query")
async def llm_wiki_query(payload: dict) -> dict:
    """基于 wiki 的问答（Karpathy 范式：不是 RAG，而是基于编译好的 wiki 回答）

    Body:
        question: 用户问题（必填）
        recall_limit: 召回页面数上限（默认 5）
        expand_backlinks: 是否用 backlink 扩展上下文（默认 true）

    Returns:
        answer: LLM 基于 wiki 的回答
        cited_slugs: 引用的 wiki slug 列表
        recalled_pages: 召回的页面（slug/title/score）
        insufficient_knowledge: 是否知识库不足
    """
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question 不能为空")
    recall_limit = int(payload.get("recall_limit", 5))
    expand_backlinks = bool(payload.get("expand_backlinks", True))

    qa = get_wiki_qa_engine()
    result = await qa.answer(
        question,
        recall_limit=recall_limit,
        expand_backlinks=expand_backlinks,
    )
    return {
        "question": result.question,
        "answer": result.answer,
        "cited_slugs": result.cited_slugs,
        "recalled_pages": [
            {"slug": h.slug, "title": h.title, "type": h.type, "score": h.score}
            for h in result.recalled_pages
        ],
        "insufficient_knowledge": result.insufficient_knowledge,
        "error": result.error,
    }


@router.get("/llm-wiki/recall")
async def llm_wiki_recall(q: str, limit: int = 5) -> dict:
    """召回测试：给定问题，返回召回的 wiki 页面（不调 LLM）

    用于调试召回质量。
    """
    if not q.strip():
        raise HTTPException(400, "q 不能为空")
    hits = recall_pages(q, limit=limit)
    return {
        "query": q,
        "count": len(hits),
        "hits": [
            {
                "slug": h.slug,
                "title": h.title,
                "type": h.type,
                "score": h.score,
                "snippet": h.snippet[:300],
            }
            for h in hits
        ],
    }


# ────────── P1-3 Lint / Health Check ──────────


@router.post("/llm-wiki/lint", dependencies=[Depends(verify_token)])
async def llm_wiki_lint(include_stale: bool = True) -> dict:
    """运行全部 wiki 健康检查

    四类检测（AGENTS.md §七）：
    - contradiction: 同一概念在多页面冲突
    - stale: raw 文档变化但 wiki 未重编译
    - orphan: 无入链的孤岛页面
    - missing_concept: 死链（被引用但页面不存在）
    - missing_type_section: 类型页面缺少必含章节
    - empty_section: 含模板兜底标记，需 LLM 重编译

    Returns:
        pages_checked, total_issues, by_type, by_severity, issues[]
    """
    report = lint_all(include_stale=include_stale)
    return report.to_dict()


@router.get("/llm-wiki/lint/suggestions")
async def llm_wiki_lint_suggestions(limit: int = 20) -> dict:
    """建议需要补全的缺失 wiki 页面（基于死链被引次数）"""
    items = suggest_missing_pages(limit=limit)
    return {"count": len(items), "suggestions": items}


# ────────── P1-4 漂移自动重编译 ──────────


@router.post("/llm-wiki/recompile-stale", dependencies=[Depends(verify_token)])
async def llm_wiki_recompile_stale(push_review: bool = True) -> dict:
    """扫描所有 stale wiki 页面 → 自动重编译 → diff → ReviewQueue

    这是 Karpathy 范式的 Maintain 工作流入口（AGENTS.md §八 8.1）。
    适合定期任务（cron）或 lint 报告 stale 后人工触发。

    Body 参数（query string）：
        push_review: 是否把 diff 推 ReviewQueue（默认 true）
    """
    batch = await auto_recompile_stale(push_review=push_review)
    return {
        "total_jobs": len(batch.jobs),
        "total_recompiled": batch.total_recompiled,
        "total_review_queued": batch.total_review_queued,
        "total_errors": batch.total_errors,
        "jobs": [
            {
                "doc_id": j.doc_id,
                "slugs_affected": j.slugs_affected,
                "pages_created": j.pages_created,
                "pages_updated": j.pages_updated,
                "pages_unchanged": j.pages_unchanged,
                "review_queued": j.review_queued,
                "diff_summary": j.diff_summary,
                "errors": j.errors,
            }
            for j in batch.jobs
        ],
    }
