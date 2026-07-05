"""LLM 统一抽象：protocol + 消息/响应数据结构"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol, runtime_checkable


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
