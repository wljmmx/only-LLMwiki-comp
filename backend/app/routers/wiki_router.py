"""Wiki 发布工作流 API（P1-5）。

手动发布的 Wiki 文档（与 /llm-wiki/* 的 LLM 编译产物区分）。

端点：
- GET    /wiki
- GET    /wiki/{slug}
- POST   /wiki/{slug}
- DELETE /wiki/{slug}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_token
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
