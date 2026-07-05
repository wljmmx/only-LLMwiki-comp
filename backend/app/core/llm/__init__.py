import structlog

from app.core.llm.base import ChatMessage, LLMClient, LLMResponse
from app.core.llm.router import get_llm_client

logger = structlog.get_logger()


async def embed_texts(
    texts: list[str],
    *,
    batch_size: int | None = None,
) -> list[list[float]] | None:
    """便捷封装：调用 LLM 生成 embedding

    失败时返回 None（不抛异常），调用方按 None 降级到纯关键词检索。

    Args:
        texts: 待向量化的文本列表
        batch_size: 分批大小，默认从 settings 读取
    """
    if not texts:
        return []
    try:
        from app.config import get_settings

        settings = get_settings()
        if not settings.embedding_model:
            # 未配置 embedding 模型 → 跳过
            return None
        bsz = batch_size or settings.embedding_batch_size
        client = get_llm_client()
        results: list[list[float]] = []
        for i in range(0, len(texts), bsz):
            batch = texts[i : i + bsz]
            embs = await client.embed(batch, model=settings.embedding_model)
            results.extend(embs)
        return results
    except Exception as e:
        logger.warning("embed_texts_failed", error=str(e))
        return None


async def embed_query(text: str) -> list[float] | None:
    """单条 query 向量化（便捷方法）"""
    embs = await embed_texts([text])
    if not embs:
        return None
    return embs[0]


__all__ = [
    "ChatMessage",
    "LLMClient",
    "LLMResponse",
    "get_llm_client",
    "embed_texts",
    "embed_query",
]
