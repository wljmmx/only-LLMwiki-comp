"""文档管理 API（P0-1）。

端点：
- GET    /documents
- GET    /documents/stats
- GET    /documents/search
- GET    /documents/{doc_id}
- GET    /documents/{doc_id}/content
- DELETE /documents/{doc_id}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.aiops import get_topology_builder
from app.auth import verify_token
from app.search import get_search_engine
from app.storage import get_document_store

router = APIRouter()


@router.get("/documents")
async def list_documents(
    limit: int = 50,
    offset: int = 0,
    format: str | None = None,
    status: str | None = None,
) -> dict:
    """列出所有存储的文档"""
    store = get_document_store()
    docs = store.list(limit, offset, format, status)
    stats = store.get_stats()
    return {"documents": docs, "stats": stats, "limit": limit, "offset": offset}


@router.get("/documents/stats")
async def document_stats() -> dict:
    """文档统计"""
    store = get_document_store()
    return store.get_stats()


@router.get("/documents/search")
async def search_documents(q: str, limit: int = 20) -> dict:
    """搜索文档（按文件名/标题）"""
    store = get_document_store()
    results = store.search(q, limit)
    return {"query": q, "results": results, "count": len(results)}


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str) -> dict:
    """获取文档元数据"""
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    return doc


@router.get("/documents/{doc_id}/content", dependencies=[Depends(verify_token)])
async def get_document_content(doc_id: str) -> dict:
    """获取文档原始内容（文本）

    返回 JSON ``{"content": str, "format": str}`` 供前端展示。
    """
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    content = store.read_content(doc_id)
    if content is None:
        raise HTTPException(404, "文件内容不存在（可能已被删除）")
    # 尝试 UTF-8 解码；二进制格式（xlsx/docx/pdf）返回占位提示
    try:
        text = content.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        text = f"[二进制内容，共 {len(content)} 字节，无法预览]"
    return {"content": text, "format": doc.get("format", "")}


@router.delete("/documents/{doc_id}", dependencies=[Depends(verify_token)])
async def delete_document(doc_id: str) -> dict:
    """删除文档（文件+元数据+索引+拓扑引用）"""
    store = get_document_store()
    ok = store.delete(doc_id)
    if not ok:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    get_search_engine().remove_index(doc_id)
    # P2-4.5 清理拓扑中该文档的引用
    topo_cleanup = get_topology_builder().remove_doc(doc_id)

    # 触发 webhook：document.deleted
    from app.webhooks import dispatch_event

    dispatch_event(
        "document.deleted",
        {"doc_id": doc_id, "topology_cleanup": topo_cleanup},
    )
    return {"deleted": True, "doc_id": doc_id, "topology_cleanup": topo_cleanup}
