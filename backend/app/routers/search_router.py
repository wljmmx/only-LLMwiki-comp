"""搜索 API（P1-1 + P2-1.1）。

端点：
- GET  /search
- GET  /search/stats
- POST /search/reindex/{doc_id}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_token
from app.core.llm import embed_query, embed_texts
from app.parsers import get_parser
from app.search import get_search_engine
from app.storage import get_document_store

router = APIRouter()


@router.get("/search")
async def search(
    q: str,
    limit: int = 20,
    use_vector: bool = False,
    fusion: str = "rrf",
) -> dict:
    """混合检索（关键字 + 向量，RRF 融合）

    - q: 搜索关键词
    - use_vector: 是否启用向量检索（需配置 EMBEDDING_MODEL，默认关闭）
    - fusion: 融合策略
        - "rrf"（默认）：Reciprocal Rank Fusion
        - "weighted"：加权线性（兼容旧版）
    """
    engine = get_search_engine()
    query_embedding = None
    vector_actually_used = False
    if use_vector:
        # 真正调用 LLM embedding API
        query_embedding = await embed_query(q)
        vector_actually_used = query_embedding is not None

    results = engine.search(
        q,
        limit,
        query_embedding,
        fusion=fusion,  # type: ignore[arg-type]
    )
    # 业务指标埋点
    from app.observability import record_business_metric

    record_business_metric("search_queries_total")
    return {
        "query": q,
        "results": results,
        "count": len(results),
        "vector_enabled": use_vector,
        "vector_actually_used": vector_actually_used,
        "fusion": fusion,
    }


@router.get("/search/stats")
async def search_stats() -> dict:
    """搜索索引统计"""
    engine = get_search_engine()
    return engine.get_stats()


@router.post("/search/reindex/{doc_id}", dependencies=[Depends(verify_token)])
async def reindex_document(doc_id: str) -> dict:
    """重新索引指定文档（同时生成 embedding 写入向量索引）"""
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    try:
        fmt = doc["format"]
        parser = get_parser(fmt)
        parsed = parser.parse(doc["stored_path"], doc_id)
        content_text = " ".join(e.content for e in parsed.elements if e.content)
        # 尝试生成 embedding（失败则降级为纯关键词）
        embedding = await embed_texts([parsed.title + "\n" + content_text[:2000]])
        emb = embedding[0] if embedding else None
        get_search_engine().index_document(doc_id, parsed.title, content_text, fmt, emb)
        return {
            "reindexed": True,
            "doc_id": doc_id,
            "title": parsed.title,
            "has_embedding": emb is not None,
        }
    except Exception as e:
        raise HTTPException(500, f"重新索引失败: {e}")
