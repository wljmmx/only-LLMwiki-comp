"""Ollama 后端（本地开发用）"""

from __future__ import annotations

from typing import Any, AsyncIterator

import httpx
import structlog

from app.core.llm.base import ChatMessage, LLMResponse

logger = structlog.get_logger()

# P1: 模块级 httpx 连接池单例，避免每次请求创建新连接
_httpx_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _httpx_client
    if _httpx_client is None:
        _httpx_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=50),
        )
    return _httpx_client


async def close_client() -> None:
    """P1: 关闭全局 httpx 连接池"""
    global _httpx_client
    if _httpx_client:
        await _httpx_client.aclose()
        _httpx_client = None


class OllamaClient:
    backend_name = "ollama"

    def __init__(self, settings) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        # .env 配置的地址是管理员可信配置，无需 SSRF 检查
        # 与 openai_compat/vLLM 保持一致的处理方式
        self._model = settings.ollama_model
        self._embedding_model = getattr(settings, "embedding_model", None) or settings.ollama_model
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
        # P1: 使用模块级连接池单例
        client = _get_client()
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
        # P1: 使用模块级连接池单例
        client = _get_client()
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
            # P1: 使用模块级连接池单例
            client = _get_client()
            resp = await client.get(f"{self._base_url}/api/tags")
            return resp.status_code == 200
        except Exception as e:
            logger.warning("ollama_health_failed", error=str(e))
            return False

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> list[list[float]]:
        """调用 Ollama 的 /api/embeddings 接口生成向量

        注意：Ollama 的 embeddings 接口单次只接受一段文本，这里循环调用。
        如需批量优化，可改用 /api/embed（新版 Ollama 支持）。
        """
        if not texts:
            return []
        emb_model = model or self._embedding_model
        results: list[list[float]] = []
        # P1: 使用模块级连接池单例
        client = _get_client()
        for text in texts:
            resp = await client.post(
                f"{self._base_url}/api/embeddings",
                json={"model": emb_model, "prompt": text, **kwargs},
            )
            resp.raise_for_status()
            data = resp.json()
            emb = data.get("embedding") or []
            results.append(list(emb))
        return results
