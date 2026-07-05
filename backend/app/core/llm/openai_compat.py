"""OpenAI 兼容客户端（vLLM / DeepSeek / 通义）

复用 openai SDK，适配所有 OpenAI 兼容接口。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import structlog
from openai import AsyncOpenAI

from app.core.llm.base import ChatMessage, LLMResponse

logger = structlog.get_logger()


class OpenAICompatClient:
    backend_name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int,
        default_temperature: float,
        default_max_tokens: int,
        label: str = "openai_compat",
        embedding_model: str | None = None,
    ) -> None:
        self._label = label
        self._model = model
        self._embedding_model = embedding_model
        self._default_temperature = default_temperature
        self._default_max_tokens = default_max_tokens
        self._client = AsyncOpenAI(
            base_url=base_url, api_key=api_key or "EMPTY", timeout=timeout
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature
            if temperature is not None
            else self._default_temperature,
            max_tokens=max_tokens or self._default_max_tokens,
            **kwargs,
        )
        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            text=choice.message.content or "",
            model=resp.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            finish_reason=choice.finish_reason,
            raw=resp.model_dump(),
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature
            if temperature is not None
            else self._default_temperature,
            max_tokens=max_tokens or self._default_max_tokens,
            stream=True,
            **kwargs,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def health(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except Exception as e:
            logger.warning("health_check_failed", label=self._label, error=str(e))
            return False

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> list[list[float]]:
        """调用 OpenAI 兼容的 embeddings API 生成向量

        Args:
            texts: 待向量化的文本列表
            model: embedding 模型名（默认使用 self._embedding_model 或 chat 模型）
        """
        if not texts:
            return []
        emb_model = model or self._embedding_model or "text-embedding-3-small"
        resp = await self._client.embeddings.create(
            model=emb_model,
            input=texts,
            **kwargs,
        )
        # 按 index 排序确保顺序与输入一致
        sorted_data = sorted(resp.data, key=lambda d: d.index)
        return [list(d.embedding) for d in sorted_data]


def build_vllm_client(settings) -> OpenAICompatClient:
    return OpenAICompatClient(
        base_url=f"{settings.vllm_base_url}/v1",
        api_key="EMPTY",
        model=settings.vllm_model,
        timeout=settings.llm_timeout,
        default_temperature=settings.llm_temperature,
        default_max_tokens=settings.llm_max_tokens,
        label="vllm",
        embedding_model=getattr(settings, "embedding_model", None),
    )


def build_openai_compat_client(settings) -> OpenAICompatClient:
    return OpenAICompatClient(
        base_url=settings.openai_compat_base_url,
        api_key=settings.openai_compat_api_key,
        model=settings.openai_compat_model,
        timeout=settings.llm_timeout,
        default_temperature=settings.llm_temperature,
        default_max_tokens=settings.llm_max_tokens,
        label="openai_compat",
        embedding_model=getattr(settings, "embedding_model", None),
    )
