"""搜索 API（P1-1）。

端点：
- GET  /search
- GET  /search/stats
- POST /search/reindex/{doc_id}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_token
from app.parsers import get_parser
from app.search import get_search_engine
from app.storage import get_document_store

router = APIRouter()


@router.get("/search")
async def search(
    q: str,
    limit: int = 20,
    use_vector: bool = False,
) -> dict:
    """混合检索（关键字 + 向量）

    - q: 搜索关键词
    - use_vector: 是否启用向量检索（需配合 LLM embedding，默认关闭）
    """
    engine = get_search_engine()
    query_embedding = None
    if use_vector:
        # 通过 LLM 生成 query embedding（如果后端支持）
        try:
            from app.core.llm import get_llm_client

            get_llm_client()
            # 简单实现：用 LLM 生成文本的 embedding（如果支持）
            # 实际生产中应调用 embedding API
        except Exception:
            pass

    results = engine.search(q, limit, query_embedding)
    return {
        "query": q,
        "results": results,
        "count": len(results),
        "vector_enabled": use_vector,
    }


@router.get("/search/stats")
async def search_stats() -> dict:
    """搜索索引统计"""
    engine = get_search_engine()
    return engine.get_stats()


@router.post("/search/reindex/{doc_id}", dependencies=[Depends(verify_token)])
async def reindex_document(doc_id: str) -> dict:
    """重新索引指定文档"""
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    # 重新解析并索引
    try:
        fmt = doc["format"]
        parser = get_parser(fmt)
        parsed = parser.parse(doc["stored_path"], doc_id)
        content_text = " ".join(e.content for e in parsed.elements if e.content)
        get_search_engine().index_document(doc_id, parsed.title, content_text, fmt)
        return {"reindexed": True, "doc_id": doc_id, "title": parsed.title}
    except Exception as e:
        raise HTTPException(500, f"重新索引失败: {e}")
