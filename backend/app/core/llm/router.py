"""LLM 后端路由器：根据 LLM_BACKEND 实例化对应客户端"""
from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.core.llm.base import LLMClient


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    backend = settings.llm_backend
    if backend == "ollama":
        from app.core.llm.ollama import OllamaClient
        return OllamaClient(settings)
    if backend in ("vllm", "openai_compat"):
        from app.core.llm.openai_compat import build_vllm_client, build_openai_compat_client
        if backend == "vllm":
            return build_vllm_client(settings)
        return build_openai_compat_client(settings)
    raise ValueError(f"未知 LLM 后端: {backend}")