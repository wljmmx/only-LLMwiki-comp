"""LLM 调用缓存：对相同 prompt + 参数组合缓存 LLMResponse，减少重复 API 调用

基于内存的 LRU 缓存，支持 TTL 过期和线程安全。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any

from app.core.llm.base import ChatMessage, LLMResponse


def _make_cache_key(
    model: str,
    messages: list[ChatMessage],
    temperature: float | None,
    max_tokens: int | None,
) -> str:
    """根据 (model, messages, temperature, max_tokens) 生成确定性缓存键"""
    payload = {
        "model": model,
        "messages": [
            {"role": m.role, "content": m.content} for m in messages
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LlmCache:
    """LLM 响应内存缓存

    - LRU 淘汰：使用 OrderedDict，容量满时淘汰最久未使用的条目
    - TTL 过期：每个条目有独立的过期时间，读取时惰性检查
    - 线程安全：所有公共方法由 threading.Lock 保护
    """

    def __init__(
        self,
        *,
        ttl: int = 300,
        max_size: int = 1000,
    ) -> None:
        """初始化缓存

        Args:
            ttl: 条目存活时间（秒），默认 300 秒
            max_size: 最大缓存条目数，默认 1000
        """
        self._ttl = ttl
        self._max_size = max_size
        self._store: OrderedDict[str, tuple[LLMResponse, float]] = OrderedDict()
        self._lock = threading.Lock()

        # 统计信息
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        self._expirations: int = 0

    def _key(self, model: str, messages: list[ChatMessage],
             temperature: float | None, max_tokens: int | None) -> str:
        """生成缓存键（便捷方法）"""
        return _make_cache_key(model, messages, temperature, max_tokens)

    def get(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
    ) -> LLMResponse | None:
        """获取缓存的 LLM 响应

        Returns:
            缓存的 LLMResponse，若未命中或已过期则返回 None
        """
        cache_key = self._key(model, messages, temperature, max_tokens)
        with self._lock:
            entry = self._store.get(cache_key)
            if entry is None:
                self._misses += 1
                return None

            response, timestamp = entry
            if time.monotonic() - timestamp > self._ttl:
                # TTL 过期，移除条目
                del self._store[cache_key]
                self._misses += 1
                self._expirations += 1
                return None

            # LRU：将访问的条目移到末尾（最近使用）
            self._store.move_to_end(cache_key)
            self._hits += 1
            return response

    def set(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
        response: LLMResponse,
    ) -> None:
        """缓存 LLM 响应

        LRU 淘汰：若缓存已满，移除最久未使用的条目后插入新条目。
        """
        cache_key = self._key(model, messages, temperature, max_tokens)
        with self._lock:
            # 若 key 已存在，先移除旧条目再插入（move_to_end 语义）
            if cache_key in self._store:
                del self._store[cache_key]

            # LRU 淘汰：容量满时移除最久未使用的条目
            while len(self._store) >= self._max_size:
                self._store.popitem(last=False)
                self._evictions += 1

            self._store[cache_key] = (response, time.monotonic())

    def clear(self) -> None:
        """清空所有缓存条目和统计信息"""
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0
            self._expirations = 0

    def stats(self) -> dict[str, Any]:
        """返回缓存统计信息

        Returns:
            dict，包含 hits, misses, entries, evictions, expirations, hit_rate
        """
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "entries": len(self._store),
                "max_size": self._max_size,
                "ttl": self._ttl,
                "evictions": self._evictions,
                "expirations": self._expirations,
                "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
            }