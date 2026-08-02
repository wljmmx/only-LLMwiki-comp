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
            timeout=httpx.Timeout(900.0, connect=10.0),  # 本地大模型推理较慢，超时设为 900s
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
        # keep_alive：模型驻留内存时间，避免反复加载导致超时
        # 默认 "-1"（永久驻留），可通过 OLLAMA_KEEP_ALIVE 环境变量调整
        self._keep_alive = getattr(settings, "ollama_keep_alive", "-1")

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
            "keep_alive": self._keep_alive,  # 模型驻留内存，避免反复加载
            "options": {
                "temperature": temperature
                if temperature is not None
                else self._default_temperature,
                "num_predict": max_tokens or self._default_max_tokens,
            },
        }
        logger.info(
            "ollama_chat_request",
            base_url=self._base_url,
            model=self._model,
            timeout=self._timeout,
            msg_count=len(messages),
            keep_alive=self._keep_alive,
        )
        # P1: 使用模块级连接池单例，超时时间使用 settings.llm_timeout
        client = _get_client()
        try:
            resp = await client.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=httpx.Timeout(self._timeout, connect=10.0),
            )
            resp.raise_for_status()
        except httpx.TimeoutException as e:
            logger.error(
                "ollama_chat_timeout",
                base_url=self._base_url,
                model=self._model,
                timeout=self._timeout,
                error_type=type(e).__name__,
                error_str=str(e) or "(empty)",
            )
            raise
        except httpx.HTTPStatusError as e:
            logger.error(
                "ollama_chat_http_error",
                base_url=self._base_url,
                model=self._model,
                status_code=e.response.status_code,
                response_body=e.response.text[:500],
            )
            raise
        except Exception as e:
            logger.error(
                "ollama_chat_error",
                base_url=self._base_url,
                model=self._model,
                error_type=type(e).__name__,
                error_str=str(e),
            )
            raise

        data = resp.json()
        content = data.get("message", {}).get("content", "")
        done = data.get("done", False)

        # Ollama 返回 done=True 但 content 为空 — 模型可能不存在或未 pull
        if done and not content.strip():
            available_models = await self._list_available_models_safe(client)
            model_error_msg = (
                f"Ollama 模型 '{self._model}' 返回空内容。"
                f"可能原因：模型 tag 不正确或未下载。"
                f"可用模型: {', '.join(available_models) if available_models else '未知'}"
            )
            logger.error(
                "ollama_chat_empty_response",
                model=self._model,
                done=done,
                available_models=available_models,
            )
            raise httpx.HTTPStatusError(
                model_error_msg,
                request=resp.request,
                response=resp,
            )

        logger.info(
            "ollama_chat_response",
            model=data.get("model", self._model),
            content_len=len(content),
            content_preview=content[:200] if content else "(empty)",
            done=done,
        )
        return LLMResponse(
            text=content,
            model=data.get("model", self._model),
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            finish_reason="stop" if done else None,
            raw=data,
        )

    async def _list_available_models_safe(self, client: httpx.AsyncClient) -> list[str]:
        """安全查询 Ollama 可用模型列表（不抛异常）"""
        try:
            resp = await client.get(f"{self._base_url}/api/tags", timeout=10.0)
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                return [m.get("name", "") for m in models if m.get("name")]
        except Exception:
            pass
        return []

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
            "keep_alive": self._keep_alive,  # 模型驻留内存，避免反复加载
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
                json={"model": emb_model, "prompt": text, "keep_alive": self._keep_alive, **kwargs},
            )
            resp.raise_for_status()
            data = resp.json()
            emb = data.get("embedding") or []
            results.append(list(emb))
        return results
