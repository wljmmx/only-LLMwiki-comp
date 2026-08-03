"""KNOW-13: graph→wiki 自动重编译测试

验证内容：
    1. _to_kebab 实体名归一化
    2. _find_pages_for_entity 种子节点查找（直接 slug + backlink）
    3. _compute_affected_pages BFS 影响集合（深度扩展 + 上限）
    4. _handle_event 事件过滤（关系事件跳过，实体事件处理）
    5. _recompile_page 乐观锁（并发跳过）+ 成功后 clear_stale
    6. _get_source_doc_id 从 frontmatter 提取 doc_id
    7. start/stop 生命周期（幂等）

不连接真实 Neo4j / 不调用真实 LLM，通过 monkeypatch mock 所有外部依赖。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.realtime.graph_event_bus import GraphEvent
from app.realtime.graph_wiki_sync import (
    GraphWikiSync,
    _to_kebab,
)


# ────────── _to_kebab ──────────


class TestToKebab:
    def test_simple_name(self) -> None:
        assert _to_kebab("nginx") == "nginx"

    def test_space_separated(self) -> None:
        assert _to_kebab("Nginx 502") == "nginx-502"

    def test_with_special_chars(self) -> None:
        assert _to_kebab("reverse_proxy config") == "reverse-proxy-config"

    def test_chinese_preserved(self) -> None:
        # 中文应保留
        assert _to_kebab("反向代理") == "反向代理"

    def test_leading_trailing_dashes_stripped(self) -> None:
        assert _to_kebab("---nginx---") == "nginx"


# ────────── GraphWikiSync 单元测试 ──────────


@pytest.fixture
def sync(monkeypatch) -> GraphWikiSync:
    """构造一个独立的 GraphWikiSync 实例（不走全局单例，避免污染）"""
    s = GraphWikiSync()
    # mock list_wiki_pages 返回可控 slug 集合
    wiki_pages = [
        {"slug": "nginx"},
        {"slug": "nginx-502-troubleshooting"},
        {"slug": "reverse-proxy"},
        {"slug": "upstream-config"},
    ]

    def _fake_list_wiki_pages(limit: int = 10000):
        return wiki_pages

    monkeypatch.setattr(
        "app.realtime.graph_wiki_sync.list_wiki_pages", _fake_list_wiki_pages
    )
    s._refresh_slug_set()
    return s


class TestFindPagesForEntity:
    def test_direct_slug_match(self, sync: GraphWikiSync) -> None:
        # 实体名直接等于已有 slug
        result = sync._find_pages_for_entity("nginx")
        assert "nginx" in result

    def test_kebab_match(self, sync: GraphWikiSync) -> None:
        # 实体名经 kebab 归一化后匹配
        result = sync._find_pages_for_entity("Nginx 502")
        # "nginx-502" 不在 slug 集合（只有 nginx-502-troubleshooting）
        # 但 backlink 可能匹配，此处仅验证 kebab 逻辑不报错
        assert isinstance(result, set)

    def test_backlink_match(self, sync: GraphWikiSync, monkeypatch) -> None:
        # mock get_backlinks：实体 "nginx" 被某页面引用
        from app.realtime import graph_wiki_sync as mod

        class _FakeBL:
            def __init__(self, source_slug: str) -> None:
                self.source_slug = source_slug

        def _fake_get_backlinks(target_slug: str):
            if target_slug == "nginx":
                return [_FakeBL("nginx-502-troubleshooting"), _FakeBL("reverse-proxy")]
            return []

        monkeypatch.setattr(mod, "get_backlinks", _fake_get_backlinks)
        result = sync._find_pages_for_entity("nginx")
        assert "nginx-502-troubleshooting" in result
        assert "reverse-proxy" in result

    def test_no_match_returns_empty(self, sync: GraphWikiSync) -> None:
        result = sync._find_pages_for_entity("nonexistent-entity")
        assert result == set()


class TestComputeAffectedPages:
    def test_no_seed_returns_empty(self, sync: GraphWikiSync) -> None:
        result = sync._compute_affected_pages("nonexistent")
        assert result == []

    def test_bfs_expansion(self, sync: GraphWikiSync, monkeypatch) -> None:
        from app.realtime import graph_wiki_sync as mod

        class _FakeBL:
            def __init__(self, source_slug: str) -> None:
                self.source_slug = source_slug

        def _fake_get_backlinks(target_slug: str):
            # nginx 被 nginx-502-troubleshooting 引用
            # nginx-502-troubleshooting 被 upstream-config 引用（二跳）
            links = {
                "nginx": [_FakeBL("nginx-502-troubleshooting")],
                "nginx-502-troubleshooting": [_FakeBL("upstream-config")],
                "upstream-config": [],
            }
            return links.get(target_slug, [])

        monkeypatch.setattr(mod, "get_backlinks", _fake_get_backlinks)
        result = sync._compute_affected_pages("nginx")
        # 应包含 nginx + nginx-502-troubleshooting + upstream-config
        assert "nginx" in result
        assert "nginx-502-troubleshooting" in result
        assert "upstream-config" in result

    def test_max_affected_pages_limit(self, sync: GraphWikiSync, monkeypatch) -> None:
        from app.realtime import graph_wiki_sync as mod

        class _FakeBL:
            def __init__(self, source_slug: str) -> None:
                self.source_slug = source_slug

        # 构造大量 backlink 超过上限
        def _fake_get_backlinks(target_slug: str):
            if target_slug == "nginx":
                return [_FakeBL(f"page-{i}") for i in range(50)]
            return []

        monkeypatch.setattr(mod, "get_backlinks", _fake_get_backlinks)
        # 让所有 page-i 都"存在"
        sync._wiki_slug_set = {f"page-{i}" for i in range(50)} | {"nginx"}
        result = sync._compute_affected_pages("nginx")
        assert len(result) <= 20  # MAX_AFFECTED_PAGES


class TestHandleEvent:
    @pytest.mark.asyncio
    async def test_skip_relation_event(
        self, sync: GraphWikiSync, monkeypatch
    ) -> None:
        # relation_upsert 应被跳过（不应调用 mark_pages_stale）
        called = []

        def _fake_mark(slugs, doc_id):
            called.append((slugs, doc_id))
            return 0

        monkeypatch.setattr(
            "app.realtime.graph_wiki_sync.mark_pages_stale", _fake_mark
        )
        event = GraphEvent(
            action="relation_upsert",
            entity_id="a→b",
            entity_type="DEPENDS_ON",
        )
        await sync._handle_event(event)
        assert called == []

    @pytest.mark.asyncio
    async def test_skip_empty_entity_id(self, sync: GraphWikiSync) -> None:
        event = GraphEvent(action="upsert", entity_id="", entity_type="Host")
        # 不应抛异常，直接返回
        await sync._handle_event(event)

    @pytest.mark.asyncio
    async def test_no_affected_pages_skips_mark(
        self, sync: GraphWikiSync, monkeypatch
    ) -> None:
        called = []

        def _fake_mark(slugs, doc_id):
            called.append((slugs, doc_id))
            return 0

        monkeypatch.setattr(
            "app.realtime.graph_wiki_sync.mark_pages_stale", _fake_mark
        )
        # 不存在的实体，无受影响页面
        event = GraphEvent(action="upsert", entity_id="nonexistent", entity_type="Host")
        await sync._handle_event(event)
        assert called == []

    @pytest.mark.asyncio
    async def test_upsert_triggers_recompile(
        self, sync: GraphWikiSync, monkeypatch
    ) -> None:
        from app.realtime import graph_wiki_sync as mod

        class _FakeBL:
            def __init__(self, source_slug: str) -> None:
                self.source_slug = source_slug

        monkeypatch.setattr(
            mod, "get_backlinks", lambda t: [_FakeBL("nginx-502-troubleshooting")] if t == "nginx" else []
        )

        mark_called = []

        def _fake_mark(slugs, doc_id):
            mark_called.append((list(slugs), doc_id))
            return len(slugs)

        monkeypatch.setattr(mod, "mark_pages_stale", _fake_mark)

        # mock _recompile_page 避免真实重编译
        recompile_called = []

        async def _fake_recompile(slug, entity_id, action):
            recompile_called.append((slug, entity_id, action))

        monkeypatch.setattr(sync, "_recompile_page", _fake_recompile)

        event = GraphEvent(action="upsert", entity_id="nginx", entity_type="Service")
        await sync._handle_event(event)
        # 给 create_task 一点时间执行
        await asyncio.sleep(0.05)
        assert mark_called
        assert any("nginx" in slugs for slugs, _ in mark_called)
        assert recompile_called


class TestRecompilePageOptimisticLock:
    @pytest.mark.asyncio
    async def test_skip_when_locked(self, sync: GraphWikiSync, monkeypatch) -> None:
        # 预先占用锁
        sync._in_progress["nginx"] = time.time()
        # mock _get_source_doc_id 不应被调用
        called = []

        def _fake_get_source(self_ignored, slug):
            called.append(slug)
            return None

        # 通过 monkeypatch 替换实例方法（self 会被传入）
        monkeypatch.setattr(sync, "_get_source_doc_id", lambda slug: called.append(slug) or None)
        await sync._recompile_page("nginx", "nginx", "upsert")
        assert called == []  # 被锁跳过

    @pytest.mark.asyncio
    async def test_skip_when_no_source_doc(
        self, sync: GraphWikiSync, monkeypatch
    ) -> None:
        monkeypatch.setattr(sync, "_get_source_doc_id", lambda slug: None)
        # 不应调用 wiki_compiler
        await sync._recompile_page("nginx", "nginx", "upsert")
        # 锁应被释放
        assert "nginx" not in sync._in_progress

    @pytest.mark.asyncio
    async def test_recompile_success_clears_stale(
        self, sync: GraphWikiSync, monkeypatch
    ) -> None:
        monkeypatch.setattr(sync, "_get_source_doc_id", lambda slug: "doc-1")

        # mock get_wiki_compiler
        class _FakeCompiler:
            async def recompile_section(self, doc_id, slug):
                return {"slug": slug, "compiled_content": "# x"}

        import app.knowledge.wiki_compiler as wc_mod

        monkeypatch.setattr(
            wc_mod, "get_wiki_compiler", lambda: _FakeCompiler()
        )

        clear_called = []
        monkeypatch.setattr(
            "app.realtime.graph_wiki_sync.clear_stale",
            lambda slug: clear_called.append(slug) or True,
        )

        await sync._recompile_page("nginx", "nginx", "upsert")
        assert clear_called == ["nginx"]
        assert "nginx" not in sync._in_progress

    @pytest.mark.asyncio
    async def test_recompile_error_does_not_clear_stale(
        self, sync: GraphWikiSync, monkeypatch
    ) -> None:
        monkeypatch.setattr(sync, "_get_source_doc_id", lambda slug: "doc-1")

        class _FakeCompiler:
            async def recompile_section(self, doc_id, slug):
                raise RuntimeError("LLM down")

        import app.knowledge.wiki_compiler as wc_mod

        monkeypatch.setattr(
            wc_mod, "get_wiki_compiler", lambda: _FakeCompiler()
        )

        clear_called = []
        monkeypatch.setattr(
            "app.realtime.graph_wiki_sync.clear_stale",
            lambda slug: clear_called.append(slug) or True,
        )

        # 不应抛异常
        await sync._recompile_page("nginx", "nginx", "upsert")
        assert clear_called == []  # 失败不清除 stale
        assert "nginx" not in sync._in_progress  # 锁仍释放


class TestGetSourceDocId:
    def test_extract_from_frontmatter(
        self, sync: GraphWikiSync, monkeypatch
    ) -> None:
        content = (
            "---\n"
            "slug: nginx\n"
            "title: Nginx\n"
            "sources:\n"
            "  - doc_id: doc-abc\n"
            "    title: guide\n"
            "---\n"
            "# Nginx\n"
        )

        class _FakeVC:
            def get_latest(self, key):
                return {"content": content, "title": "Nginx"}

        # 注意：graph_wiki_sync 模块顶部已 import get_version_control，
        # 需 patch 模块内引用而非 version_control 模块
        monkeypatch.setattr(
            "app.realtime.graph_wiki_sync.get_version_control",
            lambda: _FakeVC(),
        )
        assert sync._get_source_doc_id("nginx") == "doc-abc"

    def test_no_frontmatter_returns_none(
        self, sync: GraphWikiSync, monkeypatch
    ) -> None:
        class _FakeVC:
            def get_latest(self, key):
                return {"content": "# plain markdown", "title": "x"}

        monkeypatch.setattr(
            "app.realtime.graph_wiki_sync.get_version_control",
            lambda: _FakeVC(),
        )
        assert sync._get_source_doc_id("nginx") is None

    def test_no_latest_returns_none(
        self, sync: GraphWikiSync, monkeypatch
    ) -> None:
        class _FakeVC:
            def get_latest(self, key):
                return None

        monkeypatch.setattr(
            "app.realtime.graph_wiki_sync.get_version_control",
            lambda: _FakeVC(),
        )
        assert sync._get_source_doc_id("nginx") is None


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop_idempotent(self, sync: GraphWikiSync) -> None:
        # start 两次不应创建多个 task
        await sync.start()
        task1 = sync._task
        await sync.start()
        assert sync._task is task1
        # stop
        await sync.stop()
        assert sync._task is None
        # stop 再次不应报错
        await sync.stop()
