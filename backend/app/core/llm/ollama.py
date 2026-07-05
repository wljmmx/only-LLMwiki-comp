"""Ollama 后端（本地开发用）"""

from __future__ import annotations

from typing import Any, AsyncIterator

import httpx
import structlog

from app.core.llm.base import ChatMessage, LLMResponse

logger = structlog.get_logger()


class OllamaClient:
    backend_name = "ollama"

    def __init__(self, settings) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = settings.ollama_model
        self._timeout = settings.llm_timeout
        self._default_temperature = settings.llm_temperature
        self._default_max_tokens = settings.llm_max_tokens

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {
                "temperature": temperature
                if temperature is not None
                else self._default_temperature,
                "num_predict": max_tokens or self._default_max_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return LLMResponse(
            text=data.get("message", {}).get("content", ""),
            model=data.get("model", self._model),
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            finish_reason="stop" if data.get("done") else None,
            raw=data,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {
                "temperature": temperature
                if temperature is not None
                else self._default_temperature,
                "num_predict": max_tokens or self._default_max_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", f"{self._base_url}/api/chat", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    import orjson

                    chunk = orjson.loads(line)
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.status_code == 200
        except Exception as e:
            logger.warning("ollama_health_failed", error=str(e))
            return False
