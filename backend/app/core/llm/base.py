"""LLM 统一抽象：protocol + 消息/响应数据结构 + 错误类型"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Protocol, runtime_checkable

# P2-2: 流式取消令牌 — 返回 True 表示应取消
# 典型实现：lambda: request.is_disconnected() 或 asyncio.Event().is_set()
CancelToken = Callable[[], bool]


# ────────── P2-1: LLM 错误类型层次 ──────────
# 调用方可按错误类型决策重试/降级/直接失败


class LLMError(Exception):
    """LLM 调用基础异常"""

    retryable: bool = False


class LLMTimeoutError(LLMError):
    """请求超时（可重试）"""

    retryable = True


class LLMRateLimitError(LLMError):
    """被限流 429（可重试，需退避）"""

    retryable = True


class LLMConnectionError(LLMError):
    """网络连接失败（可重试）"""

    retryable = True


class LLMServerError(LLMError):
    """后端 5xx 错误（可重试）"""

    retryable = True


class LLMAuthError(LLMError):
    """鉴权失败 401/403（不可重试，需检查密钥）"""

    retryable = False


class LLMBadRequestError(LLMError):
    """请求参数错误 4xx（不可重试）"""

    retryable = False


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str | None = None
    raw: dict[str, Any] | None = None


@runtime_checkable
class LLMClient(Protocol):
    @property
    def backend_name(self) -> str: ...
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse: ...
    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]: ...
    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> list[list[float]]: ...
    async def health(self) -> bool: ...
