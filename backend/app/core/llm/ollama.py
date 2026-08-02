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
        # 默认 -1（永久驻留），可通过 OLLAMA_KEEP_ALIVE 环境变量调整
        # Ollama API 要求：纯数字作为 int 发送（秒），带单位字符串如 "30m" 作为 str 发送
        self._keep_alive = self._parse_keep_alive(getattr(settings, "ollama_keep_alive", "-1"))
        # 思考模式：Qwen3/DeepSeek-R1 等模型默认开启思考模式，思考内容消耗 num_predict 预算
        # 运维知识库场景（实体抽取、段落分类、Wiki编译）不需要深度思考，关闭可避免空回复
        self._think = getattr(settings, "ollama_think", False)

    @staticmethod
    def _parse_keep_alive(value: str) -> int | str:
        """将 keep_alive 配置值转为 Ollama API 要求的类型。

        Ollama API 对 keep_alive 的要求：
        - 纯数字（如 "3600"、"−1"）→ 必须作为 int 发送（秒数，-1=永久驻留）
        - 带单位字符串（如 "30m"、"1h"、"10s"）→ 作为 str 发送

        如果传入字符串 "−1"，Ollama 会尝试解析为 duration 并报错
        "time: missing unit in duration"。
        """
        s = str(value).strip()
        # 纯整数（含负数）→ 转为 int
        try:
            return int(s)
        except ValueError:
            pass
        # 带单位字符串 → 原样返回
        return s

    def _prepare_messages(self, messages: list[ChatMessage]) -> list[dict]:
        """将 ChatMessage 列表转为 Ollama API 格式，并注入 /no_think 指令。

        当 think=False 时，在最后一条 user 消息末尾追加 /no_think，
        这是 Qwen3 模型原生的思考模式开关，不依赖 Ollama 版本支持 think 参数。
        """
        result = []
        for m in messages:
            result.append({"role": m.role, "content": m.content})
        if not self._think and result:
            # 在最后一条 user 消息追加 /no_think 指令
            for i in range(len(result) - 1, -1, -1):
                if result[i]["role"] == "user":
                    result[i]["content"] = result[i]["content"].rstrip() + " /no_think"
                    break
        return result

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
            "messages": self._prepare_messages(messages),
            "stream": False,
            "keep_alive": self._keep_alive,  # 模型驻留内存，避免反复加载
            "think": self._think,  # 控制思考模式（Qwen3/DeepSeek-R1）
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
            think=self._think,
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
        msg = data.get("message", {})
        content = msg.get("content", "")
        thinking = msg.get("thinking", "")
        done = data.get("done", False)

        # Ollama 返回 done=True 但 content 为空
        if done and not content.strip():
            # 检查是否是思考模式导致：thinking 有内容但 content 为空
            # 说明思考耗尽了 num_predict 预算，模型未输出实际回复
            if thinking.strip():
                logger.warning(
                    "ollama_chat_thinking_exhausted",
                    model=self._model,
                    thinking_len=len(thinking),
                    thinking_preview=thinking[:200],
                    num_predict=payload["options"].get("num_predict"),
                    hint="思考模式耗尽 token 预算，建议增大 num_predict 或设置 ollama_think=false",
                )
                raise httpx.HTTPStatusError(
                    f"Ollama 模型 '{self._model}' 思考模式耗尽了 token 预算（num_predict={payload['options'].get('num_predict')}），"
                    f"思考内容 {len(thinking)} 字符但实际回复为空。"
                    f"解决方案：设置 OLLAMA_THINK=false 关闭思考模式，或增大 LLM_MAX_TOKENS。",
                    request=resp.request,
                    response=resp,
                )

            # 非思考模式下的空回复 — 模型可能不存在或未 pull
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
            "messages": self._prepare_messages(messages),
            "stream": True,
            "keep_alive": self._keep_alive,  # 模型驻留内存，避免反复加载
            "think": self._think,  # 控制思考模式（Qwen3/DeepSeek-R1）
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
                msg = chunk.get("message", {})
                content = msg.get("content", "")
                # 思考模式的 thinking 内容不输出到流（用户只需最终结果）
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
