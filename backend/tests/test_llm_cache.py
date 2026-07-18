"""LLM 调用缓存 LlmCache 单元测试"""

from __future__ import annotations

import threading
import time
from unittest.mock import AsyncMock

import pytest

from app.core.llm.base import ChatMessage, LLMResponse
from app.core.llm.cache import LlmCache

# ────────── 辅助函数 ──────────

def _make_msg(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=role, content=content)


def _make_resp(text: str = "test response") -> LLMResponse:
    return LLMResponse(
        text=text,
        model="test-model",
        prompt_tokens=10,
        completion_tokens=20,
        finish_reason="stop",
    )


# ────────── 缓存键测试 ──────────


class TestCacheKey:
    """缓存键确定性测试"""

    def test_same_input_same_key(self):
        """相同输入产生相同缓存键"""
        cache = LlmCache()
        msgs = [_make_msg("user", "hello")]
        key1 = cache._key("test-model", msgs, 0.5, 100)
        key2 = cache._key("test-model", msgs, 0.5, 100)
        assert key1 == key2

    def test_different_model_different_key(self):
        """不同 model 产生不同缓存键"""
        cache = LlmCache()
        msgs = [_make_msg("user", "hello")]
        key1 = cache._key("model-a", msgs, 0.5, 100)
        key2 = cache._key("model-b", msgs, 0.5, 100)
        assert key1 != key2

    def test_different_temperature_different_key(self):
        """不同 temperature 产生不同缓存键"""
        cache = LlmCache()
        msgs = [_make_msg("user", "hello")]
        key1 = cache._key("model", msgs, 0.1, 100)
        key2 = cache._key("model", msgs, 0.5, 100)
        assert key1 != key2

    def test_different_max_tokens_different_key(self):
        """不同 max_tokens 产生不同缓存键"""
        cache = LlmCache()
        msgs = [_make_msg("user", "hello")]
        key1 = cache._key("model", msgs, 0.5, 100)
        key2 = cache._key("model", msgs, 0.5, 200)
        assert key1 != key2

    def test_different_messages_different_key(self):
        """不同 messages 产生不同缓存键"""
        cache = LlmCache()
        msgs1 = [_make_msg("user", "hello")]
        msgs2 = [_make_msg("user", "world")]
        key1 = cache._key("model", msgs1, 0.5, 100)
        key2 = cache._key("model", msgs2, 0.5, 100)
        assert key1 != key2

    def test_none_params_same_as_default(self):
        """None temperature/max_tokens 确定性"""
        cache = LlmCache()
        msgs = [_make_msg("user", "hello")]
        key1 = cache._key("model", msgs, None, None)
        key2 = cache._key("model", msgs, None, None)
        assert key1 == key2


# ────────── 缓存命中/未命中测试 ──────────


class TestCacheHitMiss:
    """缓存命中与未命中测试"""

    def test_cache_miss_returns_none(self):
        """空缓存时 get 返回 None"""
        cache = LlmCache()
        msgs = [_make_msg("user", "hello")]
        result = cache.get("model", msgs, 0.5, 100)
        assert result is None

    def test_cache_hit_returns_stored_response(self):
        """set 后 get 返回相同响应"""
        cache = LlmCache()
        msgs = [_make_msg("user", "hello")]
        resp = _make_resp("cached")
        cache.set("model", msgs, 0.5, 100, resp)
        result = cache.get("model", msgs, 0.5, 100)
        assert result is resp
        assert result.text == "cached"

    def test_cache_hit_does_not_return_different_prompt(self):
        """不同 prompt 不会命中缓存"""
        cache = LlmCache()
        msgs1 = [_make_msg("user", "hello")]
        msgs2 = [_make_msg("user", "world")]
        cache.set("model", msgs1, 0.5, 100, _make_resp("hello"))
        result = cache.get("model", msgs2, 0.5, 100)
        assert result is None

    def test_overwrite_existing_key(self):
        """相同 key 再次 set 会覆盖旧值"""
        cache = LlmCache()
        msgs = [_make_msg("user", "hello")]
        cache.set("model", msgs, 0.5, 100, _make_resp("v1"))
        cache.set("model", msgs, 0.5, 100, _make_resp("v2"))
        result = cache.get("model", msgs, 0.5, 100)
        assert result.text == "v2"


# ────────── TTL 过期测试 ──────────


class TestTTLExpiration:
    """TTL 过期测试"""

    def test_entry_expires_after_ttl(self):
        """TTL 过期后 get 返回 None"""
        cache = LlmCache(ttl=1)  # 1 秒 TTL
        msgs = [_make_msg("user", "hello")]
        resp = _make_resp("expirable")
        cache.set("model", msgs, 0.5, 100, resp)

        # 立即获取应命中
        assert cache.get("model", msgs, 0.5, 100) is resp

        # 等待超过 TTL
        time.sleep(1.1)

        assert cache.get("model", msgs, 0.5, 100) is None

    def test_entry_not_expired_before_ttl(self):
        """TTL 未到时不失效"""
        cache = LlmCache(ttl=10)
        msgs = [_make_msg("user", "hello")]
        resp = _make_resp("fresh")
        cache.set("model", msgs, 0.5, 100, resp)

        time.sleep(0.1)
        assert cache.get("model", msgs, 0.5, 100) is resp

    def test_ttl_refresh_on_access(self):
        """TTL 基于创建时间，非访问时间（不刷新）"""
        cache = LlmCache(ttl=2)
        msgs = [_make_msg("user", "hello")]
        cache.set("model", msgs, 0.5, 100, _make_resp("ttl test"))

        time.sleep(1.0)
        # 访问一次（不刷新 TTL）
        cache.get("model", msgs, 0.5, 100)

        time.sleep(1.1)
        # 总时间超过 TTL，应过期
        assert cache.get("model", msgs, 0.5, 100) is None


# ────────── 缓存清空测试 ──────────


class TestCacheClear:
    """缓存清空测试"""

    def test_clear_removes_all_entries(self):
        """clear() 移除所有条目"""
        cache = LlmCache()
        msgs1 = [_make_msg("user", "a")]
        msgs2 = [_make_msg("user", "b")]
        cache.set("model", msgs1, 0.5, 100, _make_resp("a"))
        cache.set("model", msgs2, 0.5, 100, _make_resp("b"))

        cache.clear()

        assert cache.get("model", msgs1, 0.5, 100) is None
        assert cache.get("model", msgs2, 0.5, 100) is None

    def test_clear_resets_stats(self):
        """clear() 重置统计信息"""
        cache = LlmCache()
        msgs = [_make_msg("user", "hello")]
        cache.set("model", msgs, 0.5, 100, _make_resp())
        cache.get("model", msgs, 0.5, 100)  # hit

        cache.clear()
        s = cache.stats()
        assert s["hits"] == 0
        assert s["misses"] == 0
        assert s["entries"] == 0
        assert s["evictions"] == 0
        assert s["expirations"] == 0


# ────────── LRU 淘汰测试 ──────────


class TestLRUEviction:
    """LRU 淘汰测试"""

    def test_evicts_when_max_size_exceeded(self):
        """超过 max_size 时淘汰最久未使用的条目"""
        cache = LlmCache(max_size=3)

        # 插入 3 个条目
        for i in range(3):
            msgs = [_make_msg("user", f"msg{i}")]
            cache.set("model", msgs, 0.5, 100, _make_resp(f"resp{i}"))

        # 访问 msg0 使其变为最近使用
        msgs0 = [_make_msg("user", "msg0")]
        cache.get("model", msgs0, 0.5, 100)

        # 插入第 4 个条目，应淘汰 msg1（最久未使用）
        msgs3 = [_make_msg("user", "msg3")]
        cache.set("model", msgs3, 0.5, 100, _make_resp("resp3"))

        # msg0 应保留（被访问过）
        assert cache.get("model", msgs0, 0.5, 100) is not None
        # msg1 应被淘汰
        msgs1 = [_make_msg("user", "msg1")]
        assert cache.get("model", msgs1, 0.5, 100) is None
        # msg2 应保留
        msgs2 = [_make_msg("user", "msg2")]
        assert cache.get("model", msgs2, 0.5, 100) is not None
        # msg3 应存在
        assert cache.get("model", msgs3, 0.5, 100) is not None

    def test_eviction_count_in_stats(self):
        """淘汰计数在 stats 中正确反映"""
        cache = LlmCache(max_size=2)
        for i in range(5):
            msgs = [_make_msg("user", f"msg{i}")]
            cache.set("model", msgs, 0.5, 100, _make_resp(f"resp{i}"))

        s = cache.stats()
        # 5 次插入，max_size=2：前 2 次无淘汰，后 3 次各淘汰 1 个
        assert s["evictions"] == 3
        assert s["entries"] == 2


# ────────── 统计信息测试 ──────────


class TestStats:
    """统计信息测试"""

    def test_stats_initial_state(self):
        """初始 stats 全为零"""
        cache = LlmCache()
        s = cache.stats()
        assert s["hits"] == 0
        assert s["misses"] == 0
        assert s["entries"] == 0
        assert s["evictions"] == 0
        assert s["expirations"] == 0
        assert s["hit_rate"] == 0.0
        assert s["max_size"] == 1000
        assert s["ttl"] == 300

    def test_stats_hit_and_miss(self):
        """stats 正确统计命中和未命中"""
        cache = LlmCache()
        msgs = [_make_msg("user", "hello")]
        cache.get("model", msgs, 0.5, 100)  # miss
        cache.set("model", msgs, 0.5, 100, _make_resp())
        cache.get("model", msgs, 0.5, 100)  # hit

        s = cache.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["hit_rate"] == 0.5

    def test_stats_hit_rate(self):
        """hit_rate 正确计算"""
        cache = LlmCache()
        msgs_hit = [_make_msg("user", "hit")]
        msgs_miss = [_make_msg("user", "miss")]

        cache.set("model", msgs_hit, 0.5, 100, _make_resp())
        cache.get("model", msgs_hit, 0.5, 100)  # hit
        cache.get("model", msgs_miss, 0.5, 100)  # miss

        s = cache.stats()
        assert s["hit_rate"] == 0.5

    def test_stats_expirations_count(self):
        """过期次数正确统计"""
        cache = LlmCache(ttl=1)
        msgs = [_make_msg("user", "hello")]
        cache.set("model", msgs, 0.5, 100, _make_resp())

        time.sleep(1.1)
        cache.get("model", msgs, 0.5, 100)  # 触发过期

        s = cache.stats()
        assert s["expirations"] == 1


# ────────── 线程安全测试 ──────────


class TestThreadSafety:
    """线程安全测试"""

    def test_concurrent_read_write(self):
        """并发读写不引发异常"""
        cache = LlmCache(max_size=500)
        errors = []
        barrier = threading.Barrier(4)

        def worker(worker_id: int):
            try:
                barrier.wait()
                for i in range(200):
                    msgs = [_make_msg("user", f"w{worker_id}-m{i}")]
                    cache.set("model", msgs, 0.5, 100, _make_resp(f"w{worker_id}"))
                    cache.get("model", msgs, 0.5, 100)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_concurrent_stats_safe(self):
        """并发 stats 调用不崩溃"""
        cache = LlmCache(max_size=100)
        barrier = threading.Barrier(4)

        def worker(worker_id: int):
            barrier.wait()
            for i in range(100):
                msgs = [_make_msg("user", f"w{worker_id}-m{i}")]
                cache.set("model", msgs, 0.5, 100, _make_resp())
                _ = cache.stats()

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 不应崩溃
        s = cache.stats()
        assert s["entries"] <= 100


# ────────── ResilientLLMClient 集成测试 ──────────


class TestResilientLLMClientIntegration:
    """ResilientLLMClient + LlmCache 集成测试"""

    @pytest.mark.anyio
    async def test_cache_hit_skips_llm_call(self):
        """缓存命中时跳过 LLM API 调用"""
        from app.core.llm.resilient import ResilientLLMClient

        msgs = [_make_msg("user", "hello")]
        mock_resp = _make_resp("from-llm")
        mock_client = AsyncMock()
        mock_client.backend_name = "test-backend"
        mock_client.chat.return_value = mock_resp

        cache = LlmCache()
        resilient = ResilientLLMClient(mock_client, cache=cache)

        # 第一次调用：miss → 实际调用 LLM
        resp1 = await resilient.chat(msgs, temperature=0.5, max_tokens=100)
        assert resp1.text == "from-llm"
        assert mock_client.chat.call_count == 1

        # 第二次调用：hit → 不调用 LLM
        resp2 = await resilient.chat(msgs, temperature=0.5, max_tokens=100)
        assert resp2.text == "from-llm"
        assert mock_client.chat.call_count == 1  # 未增加

    @pytest.mark.anyio
    async def test_cache_miss_calls_llm(self):
        """不同 prompt 触发新的 LLM 调用"""
        from app.core.llm.resilient import ResilientLLMClient

        mock_client = AsyncMock()
        mock_client.backend_name = "test-backend"
        mock_client.chat.side_effect = [
            _make_resp("resp-a"),
            _make_resp("resp-b"),
        ]

        cache = LlmCache()
        resilient = ResilientLLMClient(mock_client, cache=cache)

        resp1 = await resilient.chat(
            [_make_msg("user", "a")], temperature=0.5, max_tokens=100
        )
        resp2 = await resilient.chat(
            [_make_msg("user", "b")], temperature=0.5, max_tokens=100
        )

        assert resp1.text == "resp-a"
        assert resp2.text == "resp-b"
        assert mock_client.chat.call_count == 2

    @pytest.mark.anyio
    async def test_no_cache_always_calls_llm(self):
        """cache=None 时始终调用 LLM"""
        from app.core.llm.resilient import ResilientLLMClient

        mock_client = AsyncMock()
        mock_client.backend_name = "test-backend"
        mock_client.chat.return_value = _make_resp("always-new")

        resilient = ResilientLLMClient(mock_client, cache=None)

        msgs = [_make_msg("user", "hello")]
        await resilient.chat(msgs, temperature=0.5, max_tokens=100)
        await resilient.chat(msgs, temperature=0.5, max_tokens=100)

        assert mock_client.chat.call_count == 2

    @pytest.mark.anyio
    async def test_cache_only_used_for_primary_backend(self):
        """缓存键使用 client.backend_name，不同后端使用不同缓存键"""
        from app.core.llm.resilient import ResilientLLMClient

        primary = AsyncMock()
        primary.backend_name = "primary-backend"
        primary.chat.return_value = _make_resp("primary-resp")

        fallback = AsyncMock()
        fallback.backend_name = "fallback-backend"
        fallback.chat.return_value = _make_resp("fallback-resp")

        cache = LlmCache()
        resilient = ResilientLLMClient(
            primary, fallbacks=[fallback], cache=cache
        )

        # 先通过 primary 调用，缓存 key 使用 "primary-backend"
        msgs = [_make_msg("user", "hello")]
        resp1 = await resilient.chat(msgs, temperature=0.5, max_tokens=100)
        assert resp1.text == "primary-resp"

        # 再次调用，直接从缓存命中（key="primary-backend"）
        resp2 = await resilient.chat(msgs, temperature=0.5, max_tokens=100)
        assert resp2.text == "primary-resp"
        assert primary.chat.call_count == 1

    @pytest.mark.anyio
    async def test_error_response_not_cached(self):
        """LLM 调用失败时不缓存响应"""
        from app.core.llm.base import LLMServerError
        from app.core.llm.resilient import ResilientLLMClient

        msgs = [_make_msg("user", "hello")]
        mock_client = AsyncMock()
        mock_client.backend_name = "test-backend"
        # 第一次失败，第二次成功
        mock_client.chat.side_effect = [
            LLMServerError("server error"),
            _make_resp("success-after-retry"),
        ]

        cache = LlmCache()
        resilient = ResilientLLMClient(mock_client, max_retries=1, cache=cache)

        resp = await resilient.chat(msgs, temperature=0.5, max_tokens=100)
        assert resp.text == "success-after-retry"

        # 缓存中应有成功响应
        cached = cache.get("test-backend", msgs, 0.5, 100)
        assert cached is not None
        assert cached.text == "success-after-retry"
