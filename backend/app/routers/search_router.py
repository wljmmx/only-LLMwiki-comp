"""搜索 API（P1-1 + P2-1.1 + P2-1.6）。

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


def _build_empty_suggestions(query: str, results: list[dict]) -> dict:
    """构建空结果兜底建议（P2-1.6）

    当搜索返回 0 命中时，提供诊断与建议帮助用户调整查询或上传文档。

    Args:
        query: 原始搜索词
        results: 搜索结果列表（调用本函数时为空）

    Returns:
        {
            "similar_queries": [...],   # 相近查询建议（最多 3 个）
            "diagnosis": str,           # 空结果原因诊断
            "upload_hint": str,         # 上传建议文案
            "did_you_mean": str | None, # 移除零命中 term 后的查询建议
        }
    """
    from app.config import get_settings
    from app.search.tokenizer import tokenize

    engine = get_search_engine()
    mode = get_settings().search_tokenizer
    tokens = tokenize(query, mode=mode)

    diagnosis_parts: list[str] = []
    similar_queries: list[str] = []
    did_you_mean: str | None = None

    if not tokens:
        # 无有效 token：判断是否中文未分词
        has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in query)
        if has_cjk:
            diagnosis_parts.append("中文未分词，建议调整关键词或检查分词配置")
        else:
            diagnosis_parts.append("query 无有效关键词")
    else:
        # 诊断：query 过长
        if len(tokens) > 6:
            diagnosis_parts.append("query 过长，建议精简关键词")

        # 逐 token 检查 FTS 命中情况，区分命中 term 与零命中 term
        hit_tokens: list[str] = []
        zero_hit_tokens: list[str] = []
        for tok in tokens:
            try:
                hits = engine.search(tok, limit=1)
            except Exception:  # noqa: BLE001
                hits = []
            if hits:
                hit_tokens.append(tok)
            else:
                zero_hit_tokens.append(tok)

        # did_you_mean：若有零命中 term 且仍有命中 term，建议移除零命中 term 重试
        if zero_hit_tokens and hit_tokens:
            did_you_mean = " ".join(hit_tokens)

        # similar_queries：用命中 term 构建相近查询（单 term 作为备选，最多 3 个）
        for tok in hit_tokens:
            if tok not in similar_queries:
                similar_queries.append(tok)
            if len(similar_queries) >= 3:
                break

        # 诊断：部分关键词未命中 / 无相关文档
        if zero_hit_tokens and hit_tokens:
            shown = "、".join(zero_hit_tokens[:3])
            diagnosis_parts.append(f"部分关键词未命中：{shown}")
        elif not hit_tokens:
            diagnosis_parts.append("无相关文档")

    if not diagnosis_parts:
        diagnosis_parts.append("无相关文档")

    return {
        "similar_queries": similar_queries[:3],
        "diagnosis": "；".join(diagnosis_parts),
        "upload_hint": "知识库无相关文档，建议上传相关文档以扩充知识库",
        "did_you_mean": did_you_mean,
    }


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
    response: dict = {
        "query": q,
        "results": results,
        "count": len(results),
        "vector_enabled": use_vector,
        "vector_actually_used": vector_actually_used,
        "fusion": fusion,
    }
    # P2-1.6：空结果兜底提示（仅 0 命中时附加 suggestions 字段）
    if not results:
        response["suggestions"] = _build_empty_suggestions(q, results)
    return response


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
