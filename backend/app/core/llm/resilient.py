"""P2-1: LLM 调用弹性 — 重试 + 限流 + 降级

包装原始 LLMClient，提供：
1. 重试：指数退避，区分可重试/不可重试错误（LLMError.retryable）
2. 限流：asyncio.Semaphore 限制并发请求数 + Token Bucket 限流器
3. 降级：主后端失败后切换到 fallback 后端链

用法：
    client = ResilientLLMClient(
        primary=OllamaClient(settings),
        fallbacks=[OpenAICompatClient(...)],
        max_retries=3,
        concurrency_limit=5,
        rate_limit=10.0,  # 10 req/s
    )
    resp = await client.chat(messages)
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time as _time
from typing import Any, AsyncIterator

import structlog

from app.core.llm.base import (
    CancelToken,
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMClient,
    LLMConnectionError,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    LLMServerError,
    LLMTimeoutError,
)
from app.core.llm.cache import LlmCache

logger = structlog.get_logger()


def _classify_error(exc: Exception) -> LLMError:
    """将原始异常映射到 LLMError 子类型

    支持 openai SDK 异常 + httpx 异常 + 通用异常。
    """
    # 已经是 LLMError 子类，直接返回
    if isinstance(exc, LLMError):
        return exc

    # openai SDK 异常映射
    exc_type = type(exc).__name__
    try:
        import openai

        if isinstance(exc, openai.APITimeoutError):
            return LLMTimeoutError(str(exc))
        if isinstance(exc, openai.RateLimitError):
            return LLMRateLimitError(str(exc))
        if isinstance(exc, openai.AuthenticationError):
            return LLMAuthError(str(exc))
        if isinstance(exc, openai.BadRequestError):
            return LLMBadRequestError(str(exc))
        if isinstance(exc, openai.InternalServerError):
            return LLMServerError(str(exc))
        if isinstance(exc, openai.APIConnectionError):
            return LLMConnectionError(str(exc))
        if isinstance(exc, openai.APIStatusError):
            # 其他 HTTP 状态码错误，5xx 可重试
            status = getattr(exc, "status_code", 500)
            if status >= 500:
                return LLMServerError(str(exc))
            if status == 429:
                return LLMRateLimitError(str(exc))
            if status in (401, 403):
                return LLMAuthError(str(exc))
            return LLMBadRequestError(str(exc))
    except ImportError:
        pass

    # httpx 异常映射
    try:
        import httpx

        if isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)):
            return LLMTimeoutError(str(exc))
        if isinstance(exc, httpx.ConnectError):
            return LLMConnectionError(str(exc))
        if isinstance(exc, httpx.TimeoutException):
            return LLMTimeoutError(str(exc))
    except ImportError:
        pass

    # asyncio 超时
    if isinstance(exc, asyncio.TimeoutError):
        return LLMTimeoutError(str(exc))

    # 通用异常 — 默认可重试（保守策略）
    return LLMError(f"{exc_type}: {exc}")


def _record_token_usage(backend: str, resp: LLMResponse) -> None:
    """P2-3: 上报 token 用量到 Prometheus + 成本追踪"""
    try:
        from app.observability.metrics import METRICS

        tokens = METRICS.get("llm_tokens_total")
        if tokens is None:
            return
        if resp.prompt_tokens > 0:
            tokens.labels(backend=backend, type="prompt").inc(resp.prompt_tokens)
        if resp.completion_tokens > 0:
            tokens.labels(backend=backend, type="completion").inc(
                resp.completion_tokens
            )
    except Exception:  # noqa: BLE001
        # 指标上报失败不应影响业务
        pass

    # 成本追踪（异步记录，不阻塞 LLM 调用）
    try:
        from app.observability.cost_tracker import get_cost_tracker

        get_cost_tracker().record(
            backend=backend,
            model=resp.model or "unknown",
            input_tokens=resp.prompt_tokens,
            output_tokens=resp.completion_tokens,
        )
    except Exception:  # noqa: BLE001
        pass


class TokenBucket:
    """P2-2: Token Bucket 限流器 — 控制 LLM API 请求速率

    基于 asyncio 的异步 token bucket 实现：
    - max_tokens: 桶容量（突发容忍度）
    - refill_rate: 每秒补充的 token 数（稳态速率）
    - 每次请求消耗 1 token，桶空时 await 等待

    用法：
        limiter = TokenBucket(rate=10.0, burst=5)
        async with limiter:
            await llm.chat(...)
    """

    def __init__(self, rate: float = 10.0, burst: int = 5) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = _time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """获取 1 个 token，若桶空则等待"""
        async with self._lock:
            now = _time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            # 需要等待
            wait = (1.0 - self._tokens) / self._rate
            self._tokens = 0.0

        await asyncio.sleep(wait)

    async def __aenter__(self) -> "TokenBucket":
        await self.acquire()
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class ResilientLLMClient:
    """弹性 LLM 客户端 — 重试 + 限流 + 降级

    实现 LLMClient Protocol（鸭子类型），可透明替换原始客户端。
    """

    # P1: 类级别 embedding 缓存，避免重复向量化
    _embed_cache: dict[str, list[float]] = {}
    _EMBED_CACHE_MAX = 200

    def __init__(
        self,
        primary: LLMClient,
        *,
        fallbacks: list[LLMClient] | None = None,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 30.0,
        concurrency_limit: int = 10,
        rate_limit: float = 0.0,
        cache: LlmCache | None = None,
    ) -> None:
        self._primary = primary
        self._fallbacks = fallbacks or []
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._semaphore = asyncio.Semaphore(concurrency_limit)
        self._cache = cache
        # P2-2: Token Bucket 限流器（rate_limit=0 表示不限流）
        self._rate_limiter: TokenBucket | None = (
            TokenBucket(rate=rate_limit, burst=max(1, int(rate_limit * 0.5)))
            if rate_limit > 0
            else None
        )

    @property
    def backend_name(self) -> str:
        return self._primary.backend_name

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """带重试+限流+降级的 chat 调用"""
        # 尝试主后端 + 所有 fallback
        clients = [self._primary] + self._fallbacks
        last_error: LLMError | None = None

        for client in clients:
            try:
                return await self._chat_with_retry(
                    client, messages, temperature, max_tokens, **kwargs
                )
            except LLMError as e:
                last_error = e
                if e.retryable and client is not clients[-1]:
                    logger.warning(
                        "llm.fallback_triggered",
                        failed_backend=client.backend_name,
                        error=str(e),
                    )
                    continue  # 切到下一个 fallback
                raise  # 不可重试错误或最后一个 fallback，直接抛出

        # 理论上不会到达
        raise last_error or LLMError("所有 LLM 后端均不可用")

    async def _chat_with_retry(
        self,
        client: LLMClient,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
        **kwargs: Any,
    ) -> LLMResponse:
        """对单个后端执行带重试的 chat（含缓存检查）"""
        # 缓存检查（在重试循环之前，避免重复 LLM 调用）
        if self._cache is not None:
            cached = self._cache.get(
                client.backend_name, messages, temperature, max_tokens
            )
            if cached is not None:
                logger.debug(
                    "llm.cache_hit",
                    backend=client.backend_name,
                )
                return cached

        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                # P2-2: Token Bucket 限流（在 Semaphore 之前，避免占着并发槽等待）
                if self._rate_limiter is not None:
                    await self._rate_limiter.acquire()
                async with self._semaphore:
                    resp = await client.chat(
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                # P2-3: 上报 token 用量到 Prometheus
                _record_token_usage(client.backend_name, resp)
                # 缓存成功的响应
                if self._cache is not None:
                    self._cache.set(
                        client.backend_name, messages, temperature, max_tokens, resp
                    )
                return resp
            except Exception as e:
                # P2-1: 分类错误，决定是否可重试
                classified = _classify_error(e)
                last_error = classified
                if not classified.retryable or attempt >= self._max_retries:
                    raise classified from e
                delay = self._calculate_delay(attempt)
                logger.warning(
                    "llm.retry",
                    backend=client.backend_name,
                    attempt=attempt + 1,
                    max_retries=self._max_retries,
                    delay=delay,
                    error=str(classified),
                )
                await asyncio.sleep(delay)

        raise last_error or LLMError("重试耗尽")

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        cancel_token: CancelToken | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """带限流+取消的 stream 调用

        流式不支持重试（已 yield 的 chunk 无法回收），
        不支持降级（切换后端会丢失上下文）。

        P2-2: cancel_token 可用于客户端断连时取消生成
        （如 FastAPI Request.is_disconnected()）。
        """
        async with self._semaphore:
            async for chunk in self._primary.stream(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            ):
                # P2-2: 检查取消信号
                if cancel_token is not None and cancel_token():
                    logger.info(
                        "llm.stream_cancelled",
                        backend=self._primary.backend_name,
                    )
                    break
                yield chunk

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> list[list[float]]:
        """带重试+限流+降级+缓存的 embed 调用

        P1: 对每个文本做 SHA256 缓存，避免重复向量化。
        """
        if not texts:
            return []

        # P1: 检查 embedding 缓存，分离已缓存和未缓存的文本
        result: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []
        for i, text in enumerate(texts):
            cache_key = hashlib.sha256(text.encode()).hexdigest()
            cached = self._embed_cache.get(cache_key)
            if cached is not None:
                result[i] = cached
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        if not uncached_texts:
            return result  # type: ignore[return-value]

        # P1: 调用底层 embed（仅处理未缓存的文本），含 fallback 降级
        clients = [self._primary] + self._fallbacks
        last_error: LLMError | None = None
        embeddings: list[list[float]] | None = None

        for client in clients:
            try:
                embeddings = await self._embed_with_retry(
                    client, uncached_texts, model, **kwargs
                )
                break
            except LLMError as e:
                last_error = e
                if e.retryable and client is not clients[-1]:
                    logger.warning(
                        "llm.embed_fallback_triggered",
                        failed_backend=client.backend_name,
                        error=str(e),
                    )
                    continue
                raise

        if embeddings is None:
            raise last_error or LLMError("所有 LLM 后端均不可用")

        # P1: 将新结果写入缓存，并回填到 result
        for idx, emb in zip(uncached_indices, embeddings):
            text = texts[idx]
            cache_key = hashlib.sha256(text.encode()).hexdigest()
            # LRU 淘汰：缓存满时移除最旧的 10%
            if len(self._embed_cache) >= self._EMBED_CACHE_MAX:
                remove_count = self._EMBED_CACHE_MAX // 10
                for _ in range(remove_count):
                    self._embed_cache.pop(next(iter(self._embed_cache)), None)
            self._embed_cache[cache_key] = emb
            result[idx] = emb

        return result  # type: ignore[return-value]

    async def _embed_with_retry(
        self,
        client: LLMClient,
        texts: list[str],
        model: str | None,
        **kwargs: Any,
    ) -> list[list[float]]:
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                # P2-2: Token Bucket 限流（在 Semaphore 之前，避免占着并发槽等待）
                if self._rate_limiter is not None:
                    await self._rate_limiter.acquire()
                async with self._semaphore:
                    return await client.embed(texts, model=model, **kwargs)
            except Exception as e:
                classified = _classify_error(e)
                last_error = classified
                if not classified.retryable or attempt >= self._max_retries:
                    raise classified from e
                await asyncio.sleep(self._calculate_delay(attempt))

        raise last_error or LLMError("重试耗尽")

    async def health(self) -> bool:
        """健康检查 — 主后端不可用时尝试 fallback"""
        if await self._primary.health():
            return True
        for fb in self._fallbacks:
            if await fb.health():
                return True
        return False

    def _calculate_delay(self, attempt: int) -> float:
        """指数退避 + 抖动"""
        delay = self._retry_base_delay * (2**attempt)
        delay = min(delay, self._retry_max_delay)
        # 抖动：0~50% 的随机增量，避免重试风暴
        jitter = random.uniform(0, delay * 0.5)
        return delay + jitter
