"""LLM 后端路由器：根据 LLM_BACKEND 实例化对应客户端

P2-1: 用 ResilientLLMClient 包装原始客户端，提供重试+限流+降级
"""

from __future__ import annotations

from functools import lru_cache

import structlog

from app.config import get_settings
from app.core.llm.base import LLMClient

logger = structlog.get_logger()


def _build_raw_client(backend: str, settings) -> LLMClient:
    """构建原始 LLM 客户端（不含弹性包装）"""
    if backend == "ollama":
        from app.core.llm.ollama import OllamaClient

        return OllamaClient(settings)
    if backend in ("vllm", "openai_compat"):
        from app.core.llm.openai_compat import (
            build_openai_compat_client,
            build_vllm_client,
        )

        if backend == "vllm":
            return build_vllm_client(settings)
        return build_openai_compat_client(settings)
    raise ValueError(f"未知 LLM 后端: {backend}")


@lru_cache
def get_llm_client() -> LLMClient:
    """获取 LLM 客户端（P2-1: 含重试+限流+降级包装）"""
    settings = get_settings()
    primary = _build_raw_client(settings.llm_backend, settings)

    # P2-1: 构建 fallback 后端链
    fallbacks: list[LLMClient] = []
    fallback_raw = settings.llm_fallback_backends.strip()
    if fallback_raw:
        for fb_backend in fallback_raw.split(","):
            fb_backend = fb_backend.strip()
            if not fb_backend or fb_backend == settings.llm_backend:
                continue  # 跳过空值和与主后端重复的
            try:
                fallbacks.append(_build_raw_client(fb_backend, settings))
                logger.info("llm.fallback_configured", fallback_backend=fb_backend)
            except Exception as e:
                logger.warning(
                    "llm.fallback_build_failed", backend=fb_backend, error=str(e)
                )

    # P2-1: 用 ResilientLLMClient 包装
    from app.core.llm.resilient import ResilientLLMClient

    return ResilientLLMClient(
        primary,
        fallbacks=fallbacks or None,
        max_retries=settings.llm_max_retries,
        retry_base_delay=settings.llm_retry_base_delay,
        retry_max_delay=settings.llm_retry_max_delay,
        concurrency_limit=settings.llm_concurrency_limit,
        rate_limit=settings.llm_rate_limit,
    )
