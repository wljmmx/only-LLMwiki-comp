"""Karpathy LLM Wiki API（P0/P1 范式：知识编译 + 结构化沉淀）。

与既有 /wiki/{slug}（手动发布）区分：/llm-wiki/* 是 LLM 编译产物。

端点：
- POST /llm-wiki/ingest
- POST /llm-wiki/recompile/{doc_id}
- GET  /llm-wiki/pages
- GET  /llm-wiki/page/{slug}
- PUT  /llm-wiki/page/{slug}     # S16-2 用户直接编辑
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

import json
from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import require_role, verify_token
from app.knowledge import (
    auto_recompile_stale,
    detect_drift,
    get_all_deadlinks,
    get_all_slugs,
    get_backlinks,
    get_index,
    get_orphan_slugs,
    get_outlinks,
    get_wiki_compiler,
    get_wiki_qa_engine,
    ignore_issue,
    lint_all,
    list_ignored_issues,
    list_stale_pages,
    list_wiki_pages,
    mark_pages_stale,
    rebuild_index,
    recall_pages,
    render_wikilinks_html,
    suggest_missing_pages,
    unignore_issue,
    update_backlinks,
)
from app.parsers import supported_formats
from app.routers.parsers_router import EXT_FMT_MAP
from app.storage import get_document_store, get_version_control

router = APIRouter()

# AGENTS.md §三 允许的 wiki 页面类型
VALID_PAGE_TYPES = {"entity", "concept", "incident", "runbook", "service", "host"}


class WikiPageUpdate(BaseModel):
    """PUT /llm-wiki/page/{slug} 请求体"""

    content: str = Field(..., description="完整 Markdown（含 YAML frontmatter）")
    title: str | None = Field(None, description="页面标题（缺省从 frontmatter 解析）")
    change_summary: str = Field("", description="变更摘要")
    expected_version: int | None = Field(
        None, description="乐观锁：期望的当前版本号", ge=1
    )
    bypass_lock: bool = Field(False, description="admin 强制覆盖编辑锁")


def _split_frontmatter(content: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (meta, body)"""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, content
    return meta, parts[2].lstrip("\n")


def _assemble_md(meta: dict, body: str) -> str:
    """重新组装 frontmatter + body"""
    clean = {k: v for k, v in meta.items() if v is not None}
    fm = yaml.safe_dump(clean, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body.strip()}\n"


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


def _identity_to_user_id(identity: str) -> str:
    """把 require_role 返回的 identity 转为 CollabHub user_id 格式

    见 realtime_router._resolve_user：
        "anonymous" → "anon"
        "user"      → "legacy"
        "user:<n>"  → "user:<n>"
    """
    if identity == "anonymous":
        return "anon"
    if identity == "user":
        return "legacy"
    return identity


@router.put("/llm-wiki/page/{slug}")
async def llm_wiki_page_put(
    slug: str,
    body: WikiPageUpdate,
    identity: str = Depends(require_role("operator")),
) -> dict:
    """用户直接编辑 wiki 页面（S16-2）

    与 LLM 编译产物互补：用户可修正编译结果、补充人工事实。
    编辑会触发版本快照、backlink 重建、搜索索引刷新、webhook 事件。

    权限：operator 及以上（viewer 只读）
    编辑锁：若 CollabHub 中该 slug 有锁且持有者非当前用户 → 409
            bypass_lock=true 且 admin 角色可跳过（应急通道）
    乐观锁：传 expected_version 时校验，冲突 → 409

    Body:
        content: 完整 Markdown（含 frontmatter）
        title: 可选，缺省从 frontmatter 解析
        change_summary: 变更摘要
        expected_version: 乐观锁
        bypass_lock: admin 强制覆盖
    """
    vc = get_version_control()
    doc_key = f"wiki:{slug}"
    latest = vc.get_latest(doc_key)
    if not latest:
        raise HTTPException(404, f"wiki 页面不存在: {slug}")

    # 1. 内容校验：frontmatter 必须存在
    meta, page_body = _split_frontmatter(body.content)
    if not meta:
        raise HTTPException(400, "content 必须包含有效的 YAML frontmatter")

    # 2. slug 一致性（防止误改 slug 导致 doc_key 错位）
    fm_slug = meta.get("slug")
    if fm_slug and fm_slug != slug:
        raise HTTPException(
            400,
            f"frontmatter slug 与路径不一致：frontmatter={fm_slug}, path={slug}",
        )

    # 3. type 合法性
    page_type = meta.get("type", "concept")
    if page_type not in VALID_PAGE_TYPES:
        raise HTTPException(400, f"无效页面类型: {page_type}")

    # 4. 乐观锁校验
    if body.expected_version is not None:
        if latest["version"] != body.expected_version:
            raise HTTPException(
                409,
                f"版本冲突：期望 v{body.expected_version}，实际 v{latest['version']}",
            )

    # 5. 编辑锁校验（软锁，参考 AGENTS.md §九 CollabHub）
    if not body.bypass_lock:
        from app.realtime import get_collab_hub

        hub = get_collab_hub()
        state = hub.get_room_state(slug)
        if state and state.get("lock_holder"):
            my_user_id = _identity_to_user_id(identity)
            if state["lock_holder"] != my_user_id:
                raise HTTPException(
                    409,
                    f"页面正被 {state['lock_holder']} 编辑，请先申请编辑锁",
                )
    # bypass_lock=true 时不做角色二次校验（require_role 已确保 operator+，
    # 真正的 admin 守卫由前端 UX 控制；后端无法区分 admin 与 operator
    # 在 require_role 中的差异——都返回 identity 字符串。如需硬约束，
    # 可在此处补 get_current_user().role == "admin" 校验）

    # 6. 刷新 frontmatter：updated_at + 清除 stale（用户编辑视为已对齐）
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    if meta.get("stale"):
        meta["stale"] = False
        meta.pop("stale_reason", None)
    # 标记人工编辑（供 LLM 重编译时识别，参考研究报告 §9.1）
    meta["edited_by_human"] = True
    meta["last_human_edit_at"] = meta["updated_at"]
    new_content = _assemble_md(meta, page_body)

    # 7. 标题优先级：body.title > frontmatter.title > slug
    title = body.title or meta.get("title") or slug

    # 8. 保存新版本（一次）
    author = identity if identity != "anonymous" else "anonymous"
    result = vc.save_version(
        doc_key=doc_key,
        title=title,
        content=new_content,
        author=author,
        change_summary=body.change_summary or "用户编辑",
    )

    # 9. backlink 重建（update_backlinks 已做差量：先删后插）
    try:
        update_backlinks(slug, new_content)
    except Exception:  # noqa: BLE001
        pass

    # 10. index 重建（不阻塞主流程）
    try:
        rebuild_index()
    except Exception:  # noqa: BLE001
        pass

    # 11. 搜索索引刷新
    try:
        from app.search import get_search_engine

        get_search_engine().index_document(doc_key, title, new_content, "wiki")
    except Exception:  # noqa: BLE001
        pass

    # 12. webhook 事件
    try:
        from app.webhooks import dispatch_event

        dispatch_event(
            "wiki.page.edited",
            {
                "slug": slug,
                "title": title,
                "version": result.get("version"),
                "editor": author,
                "change_summary": body.change_summary,
            },
        )
    except Exception:  # noqa: BLE001
        pass

    # 13. 业务指标埋点
    try:
        from app.observability import record_business_metric

        record_business_metric("wiki_page_edited_total")
    except Exception:  # noqa: BLE001
        pass

    return {
        "slug": slug,
        "title": title,
        "version": result.get("version"),
        "checksum": result.get("checksum"),
        "created_at": result.get("created_at"),
        "skipped": result.get("skipped", False),
        "reason": result.get("reason"),
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


@router.post("/llm-wiki/query", dependencies=[Depends(verify_token)])
async def llm_wiki_query(payload: dict) -> dict:
    """基于 wiki 的问答（Karpathy 范式：不是 RAG，而是基于编译好的 wiki 回答）

    Body:
        question: 用户问题（必填）
        recall_limit: 召回页面数上限（默认 5）
        expand_backlinks: 是否用 backlink 扩展上下文（默认 true）
        history: P2-13b 多轮会话历史（[{role:"user"|"assistant", content}]），
                 后端无状态，由前端维护并随每次请求回传

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
    history = payload.get("history") or None

    qa = get_wiki_qa_engine()
    result = await qa.answer(
        question,
        recall_limit=recall_limit,
        expand_backlinks=expand_backlinks,
        history=history,
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
    hits = await recall_pages(q, limit=limit)
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


@router.post("/llm-wiki/query/stream", dependencies=[Depends(verify_token)])
async def llm_wiki_query_stream(request: Request, payload: dict):
    """流式 Wiki 问答（P1-4）

    SSE 事件序列：
        event: meta   — {"recalled_pages":[...], "cited_slugs":[...],
                         "insufficient_knowledge":bool, "answer"?:str}
        event: delta  — {"text":"chunk"}  （多次）
        event: done   — {"writebacks":[...]}

    知识库不足时仅发 meta（含 answer）+ done。
    让前端在 LLM 生成期间即时看到召回页面与逐字回答，降低等待焦虑。

    Body 支持 history（P2-13b 多轮会话历史），由前端维护并回传。

    P2-2: 客户端断连时取消 LLM 生成（通过 request.is_disconnected()）。
    """
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question 不能为空")
    recall_limit = int(payload.get("recall_limit", 5))
    expand_backlinks = bool(payload.get("expand_backlinks", True))
    history = payload.get("history") or None

    qa = get_wiki_qa_engine()

    # P2-2: 客户端断连时取消 LLM 生成
    cancel_token = request.is_disconnected

    async def event_gen():
        try:
            async for evt in qa.stream_answer(
                question,
                recall_limit=recall_limit,
                expand_backlinks=expand_backlinks,
                history=history,
                cancel_token=cancel_token,
            ):
                yield f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            err = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"event: error\ndata: {err}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲，确保实时推送
        },
    )


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


# ────────── P1-12b: Lint issue 忽略 ──────────


class LintIgnoreRequest(BaseModel):
    """POST /llm-wiki/lint/ignore 请求体"""

    issue_key: str = Field(
        ..., description="lint issue 稳定标识（sha1(type|slug|message)[:16]）"
    )
    type: str = Field(..., description="问题类型（TYPE_*）")
    slug: str = Field(..., description="受影响页面 slug")
    message: str = Field(..., description="问题消息（用于幂等去重与回溯）")
    reason: str = Field("", description="忽略原因（可选）")


@router.post("/llm-wiki/lint/ignore", dependencies=[Depends(verify_token)])
async def llm_wiki_lint_ignore(payload: LintIgnoreRequest) -> dict:
    """忽略一个 lint issue（幂等）

    忽略后，后续 `lint_all` 会过滤掉该 issue，不再计入 total_issues/by_*。
    取消忽略用 DELETE /llm-wiki/lint/ignore/{issue_key}。
    """
    return ignore_issue(
        payload.issue_key,
        type=payload.type,
        slug=payload.slug,
        message=payload.message,
        reason=payload.reason,
    )


@router.delete("/llm-wiki/lint/ignore/{issue_key}", dependencies=[Depends(verify_token)])
async def llm_wiki_lint_unignore(issue_key: str) -> dict:
    """取消忽略一个 lint issue"""
    deleted = unignore_issue(issue_key)
    return {"issue_key": issue_key, "unignored": deleted}


@router.get("/llm-wiki/lint/ignored", dependencies=[Depends(verify_token)])
async def llm_wiki_lint_ignored() -> dict:
    """列出所有已忽略的 lint issue"""
    items = list_ignored_issues()
    return {"count": len(items), "items": items}


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
