"""Wiki 发布工作流 API（P1-5）。

手动发布的 Wiki 文档（与 /llm-wiki/* 的 LLM 编译产物区分）。

端点：
- GET    /wiki
- GET    /wiki/{slug}
- POST   /wiki/{slug}
- DELETE /wiki/{slug}
- POST   /wiki/{slug}/recompile/stream   # P2-5.5 SSE 流式按 slug 重编译
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.auth import verify_token
from app.knowledge import get_wiki_compiler
from app.search import get_search_engine
from app.storage import get_version_control
from app.storage.version_control import _get_db as get_vc_db

router = APIRouter()


@router.get("/wiki")
async def wiki_list(limit: int = 50, offset: int = 0) -> dict:
    """列出已发布的 Wiki 文档"""
    vc = get_version_control()
    # Wiki 文档以 wiki: 前缀存储
    conn = vc._get_db() if hasattr(vc, "_get_db") else None
    conn = get_vc_db()
    rows = conn.execute(
        """SELECT DISTINCT doc_key FROM document_versions
           WHERE doc_key LIKE 'wiki:%' ORDER BY doc_key LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    wiki_docs = []
    for r in rows:
        latest = vc.get_latest(r["doc_key"])
        if latest:
            wiki_docs.append(
                {
                    "slug": r["doc_key"].replace("wiki:", "", 1),
                    "title": latest["title"],
                    "version": latest["version"],
                    "updated_at": latest["created_at"],
                }
            )
    return {"documents": wiki_docs, "count": len(wiki_docs)}


@router.get("/wiki/{slug}")
async def wiki_get(slug: str) -> dict:
    """获取 Wiki 文档内容"""
    vc = get_version_control()
    doc_key = f"wiki:{slug}"
    latest = vc.get_latest(doc_key)
    if not latest:
        raise HTTPException(404, f"Wiki 文档不存在: {slug}")
    versions = vc.list_versions(doc_key)
    return {
        "slug": slug,
        "title": latest["title"],
        "content": latest["content"],
        "version": latest["version"],
        "versions": versions,
        "updated_at": latest["created_at"],
    }


@router.post("/wiki/{slug}", dependencies=[Depends(verify_token)])
async def wiki_publish(
    slug: str,
    title: str,
    content: str,
    change_summary: str = "",
) -> dict:
    """发布/更新 Wiki 文档（自动创建新版本）"""
    vc = get_version_control()
    doc_key = f"wiki:{slug}"
    result = vc.save_version(doc_key, title, content, change_summary=change_summary)
    # 同时建立搜索索引
    get_search_engine().index_document(doc_key, title, content, "wiki")

    # 触发 webhook：wiki.published
    from app.webhooks import dispatch_event

    dispatch_event(
        "wiki.published",
        {
            "slug": slug,
            "title": title,
            "version": result.get("version"),
            "change_summary": change_summary,
        },
    )
    # 业务指标埋点
    from app.observability import record_business_metric

    record_business_metric("wiki_published_total")
    return result


@router.delete("/wiki/{slug}", dependencies=[Depends(verify_token)])
async def wiki_delete(slug: str) -> dict:
    """删除 Wiki 文档"""
    vc = get_version_control()
    doc_key = f"wiki:{slug}"
    count = vc.delete_all(doc_key)
    if count == 0:
        raise HTTPException(404, f"Wiki 文档不存在: {slug}")
    get_search_engine().remove_index(doc_key)

    # 触发 webhook：wiki.deleted
    from app.webhooks import dispatch_event

    dispatch_event(
        "wiki.deleted",
        {"slug": slug, "versions_removed": count},
    )
    return {"deleted": True, "slug": slug, "versions_removed": count}


# ────────── P2-5.5: SSE 流式按 slug 重编译 ──────────


def _sse_event(event_type: str, data: dict) -> str:
    """构建 SSE 事件字符串"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _check_cancel(cancel_token) -> bool:
    """检查客户端是否断开连接"""
    if cancel_token is None:
        return False
    try:
        return await cancel_token()
    except Exception:  # noqa: BLE001
        return False


def _extract_source_doc_id(content: str) -> str:
    """从 wiki 页面 Markdown 中提取 source doc_id

    wiki 页面 frontmatter 含 `sources: [{doc_id, title, checksum}]`，
    取第一个 source 的 doc_id 作为重编译入口。
    """
    if not content.startswith("---"):
        return ""
    parts = content.split("---", 2)
    if len(parts) < 3:
        return ""
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return ""
    sources = meta.get("sources") or []
    if not isinstance(sources, list) or not sources:
        return ""
    first = sources[0]
    if not isinstance(first, dict):
        return ""
    return str(first.get("doc_id") or "")


@router.post("/wiki/{slug}/recompile/stream", dependencies=[Depends(verify_token)])
async def wiki_recompile_stream(request: Request, slug: str):
    """P2-5.5: 按 wiki slug 流式重编译 — 实时推送每一步进度

    与 /llm-wiki/recompile/{doc_id}/stream 的区别：以 wiki slug 为入口，
    先解析该页面对应的 source doc_id，再调用编译器。

    SSE 事件序列：
        event: progress — {"step":"start", "slug":"nginx-502-troubleshooting"}
        event: progress — {"step":"compiling", "doc_id":"abc123"}
        [透传 wiki_compiler 的 step_start/step_done/page_start/page_done/progress]
        event: progress — {"step":"done", "slug":"...", "pages_updated":2, ...}
        event: error    — {"step":"...", "message":"...", "retryable":bool}

    客户端断连时取消编译（通过 request.is_disconnected()）。
    """
    from app.knowledge.wiki_compiler import ProgressEventType

    compiler = get_wiki_compiler()
    cancel_token = request.is_disconnected

    async def event_gen():
        total_start = datetime.now(timezone.utc)
        loop = asyncio.get_event_loop()
        # 跨线程安全的事件队列：wiki_compiler 同步 on_progress → SSE async generator
        ev_queue: asyncio.Queue = asyncio.Queue()

        def on_progress(etype: ProgressEventType, data: dict) -> None:
            """wiki_compiler 同步回调 → 线程安全入队"""
            try:
                loop.call_soon_threadsafe(ev_queue.put_nowait, (etype.value, data))
            except Exception:  # noqa: BLE001
                pass  # 回调失败不应中断编译

        async def run_compile():
            """后台运行编译，结果/异常入队"""
            try:
                # 1. 推送 start 事件
                await ev_queue.put(("__start__", {"slug": slug}))

                # 2. 解析 wiki 页面，提取 source doc_id
                vc = get_version_control()
                latest = vc.get_latest(f"wiki:{slug}")
                if not latest:
                    await ev_queue.put(
                        ("__error__", {
                            "message": f"wiki 页面不存在: {slug}",
                            "retryable": False,
                        })
                    )
                    return

                doc_id = _extract_source_doc_id(latest["content"])
                if not doc_id:
                    await ev_queue.put(
                        ("__error__", {
                            "message": f"无法从 wiki 页面 {slug} 提取 source doc_id",
                            "retryable": False,
                        })
                    )
                    return

                # 3. 推送 compiling 事件
                await ev_queue.put(("__compiling__", {"doc_id": doc_id}))

                # 4. 执行编译
                result = await compiler.compile_raw_to_wiki(
                    doc_id,
                    force=True,
                    is_cancelled=cancel_token,  # 客户端断连时取消
                    on_progress=on_progress,  # 进度回调
                )
                await ev_queue.put(("__result__", result))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                await ev_queue.put(
                    ("__error__", {"message": str(e), "retryable": True})
                )

        task = asyncio.create_task(run_compile())

        try:
            while True:
                # 客户端断连取消
                if await _check_cancel(cancel_token):
                    task.cancel()
                    return

                try:
                    event_type, data = await asyncio.wait_for(
                        ev_queue.get(), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    continue

                if event_type == "__start__":
                    yield _sse_event("progress", {"step": "start", "slug": slug})
                elif event_type == "__compiling__":
                    yield _sse_event(
                        "progress", {"step": "compiling", "doc_id": data["doc_id"]}
                    )
                elif event_type == "__result__":
                    result = data
                    total_ms = int(
                        (datetime.now(timezone.utc) - total_start).total_seconds()
                        * 1000
                    )
                    yield _sse_event("progress", {
                        "step": "done",
                        "slug": slug,
                        "total_ms": total_ms,
                        "doc_id": result.doc_id,
                        "pages_created": result.pages_created,
                        "pages_updated": result.pages_updated,
                        "pages_unchanged": result.pages_unchanged,
                        "slugs": result.slugs,
                        "review_needed": result.review_needed,
                        "stale_marked": result.stale_marked,
                        "errors": result.errors,
                        "index_rebuilt": result.index_rebuilt,
                    })
                    break
                elif event_type == "__error__":
                    yield _sse_event("error", {
                        "step": "compile",
                        "message": data.get("message", ""),
                        "retryable": data.get("retryable", True),
                    })
                    break
                else:
                    # 透传 wiki_compiler 的进度事件
                    yield _sse_event(event_type, data)
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
