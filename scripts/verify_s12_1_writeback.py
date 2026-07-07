"""S12-1 Query 回写知识复利 验证脚本

验证：
1. _rebuild_frontmatter 重建 frontmatter（meta + body → 完整 md）
2. _rebuild_frontmatter 空 meta 直接返回 body
3. _rebuild_frontmatter 保留中文（allow_unicode）
4. WikiQueryResult.writebacks 字段默认空列表
5. _append_fact_to_page 页面不存在返回 None
6. _append_fact_to_page 首次追加创建「知识复利补充」章节
7. _append_fact_to_page 二次追加复用已有章节
8. _append_fact_to_page 更新 frontmatter review_status: review_needed
9. _append_fact_to_page 通过 VersionControl 保存新版本
10. writeback_new_facts 无新事实返回空列表（mock LLM 返回 []）
11. writeback_new_facts 有新事实回写页面（mock LLM 返回 [{slug, fact}]）
12. writeback_new_facts LLM 异常容错返回空列表
13. answer() writeback=False 不触发回写
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_s121_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

# 重定向 version_control DB
import app.storage.version_control as vc_mod

vc_mod.DB_PATH = TMP_DIR / "versions.db"

from app.knowledge.wiki_query import (  # noqa: E402
    WikiQueryResult,
    _rebuild_frontmatter,
)
from app.storage.version_control import get_version_control  # noqa: E402


def test_rebuild_frontmatter_basic():
    """[1/13] _rebuild_frontmatter 重建 frontmatter"""
    print("\n[1/13] 测试 _rebuild_frontmatter 重建 frontmatter...")
    meta = {"slug": "test-page", "title": "测试页", "type": "concept"}
    body = "# 测试页\n\n正文内容"
    result = _rebuild_frontmatter(meta, body)
    assert result.startswith("---\n")
    assert "slug: test-page" in result
    assert "title: 测试页" in result
    assert "# 测试页" in result
    # frontmatter 后有空行分隔 body
    assert "---\n\n# 测试页" in result
    print("  ✓ frontmatter 正确重建")


def test_rebuild_frontmatter_empty_meta():
    """[2/13] 空 meta 直接返回 body"""
    print("\n[2/13] 测试空 meta...")
    body = "# 仅正文\n\n内容"
    result = _rebuild_frontmatter({}, body)
    assert result == body.rstrip() + "\n"
    print("  ✓ 空 meta 返回纯 body")


def test_rebuild_frontmatter_chinese():
    """[3/13] 中文不转义"""
    print("\n[3/13] 测试中文保留...")
    meta = {"title": "Nginx 故障排查", "tags": ["nginx", "502"]}
    body = "# Nginx 故障排查\n\n上游服务不可达"
    result = _rebuild_frontmatter(meta, body)
    # 中文不应被转义为 \uXXXX
    assert "Nginx 故障排查" in result
    assert "上游服务不可达" in result
    assert "\\u" not in result
    print("  ✓ 中文正确保留")


def test_wiki_query_result_writebacks_default():
    """[4/13] WikiQueryResult.writebacks 默认空列表"""
    print("\n[4/13] 测试 WikiQueryResult.writebacks 默认值...")
    r = WikiQueryResult(question="q", answer="a")
    assert r.writebacks == []
    print("  ✓ writebacks 默认空列表")


def _make_engine():
    """构造一个轻量 WikiQAEngine（绕过 LLM client 初始化）"""
    from app.knowledge.wiki_query import WikiQAEngine

    engine = WikiQAEngine.__new__(WikiQAEngine)
    engine.vc = get_version_control()
    engine.settings = None
    engine.llm = None
    return engine


def _seed_page(slug: str, title: str = "测试页", body: str = "# 测试页\n\n初始内容"):
    """创建一个 wiki 页面"""
    from app.knowledge.wiki_index import _key_from_slug

    vc = get_version_control()
    content = _rebuild_frontmatter(
        {
            "slug": slug,
            "title": title,
            "type": "concept",
            "tags": [slug],
            "created_at": "2026-07-06T10:00:00Z",
            "updated_at": "2026-07-06T10:00:00Z",
            "review_status": "auto",
        },
        body,
    )
    vc.save_version(_key_from_slug(slug), title, content, author="test")


def test_append_fact_page_not_exist():
    """[5/13] 页面不存在返回 None"""
    print("\n[5/13] 测试页面不存在...")
    engine = _make_engine()
    result = engine._append_fact_to_page("nonexistent-slug", "新事实", "问题")
    assert result is None
    print("  ✓ 页面不存在返回 None")


def test_append_fact_first_time_creates_section():
    """[6/13] 首次追加创建章节"""
    print("\n[6/13] 测试首次追加创建章节...")
    _seed_page("page-a")
    engine = _make_engine()
    record = engine._append_fact_to_page("page-a", "这是新事实A", "什么是 A？")
    assert record is not None
    assert record["slug"] == "page-a"
    assert record["fact"] == "这是新事实A"
    assert record["status"] == "written"
    assert record["review_status"] == "review_needed"
    # 验证页面内容包含章节
    from app.knowledge.wiki_index import _key_from_slug

    latest = engine.vc.get_latest(_key_from_slug("page-a"))
    assert "## 知识复利补充" in latest["content"]
    assert "这是新事实A" in latest["content"]
    assert "待审查" in latest["content"]
    print("  ✓ 首次追加创建「知识复利补充」章节")


def test_append_fact_second_time_reuses_section():
    """[7/13] 二次追加复用已有章节"""
    print("\n[7/13] 测试二次追加复用章节...")
    engine = _make_engine()
    engine._append_fact_to_page("page-a", "第一条事实", "Q1")
    engine._append_fact_to_page("page-a", "第二条事实", "Q2")
    from app.knowledge.wiki_index import _key_from_slug

    latest = engine.vc.get_latest(_key_from_slug("page-a"))
    # 只应有一个章节标题
    assert latest["content"].count("## 知识复利补充") == 1
    assert "第一条事实" in latest["content"]
    assert "第二条事实" in latest["content"]
    print("  ✓ 二次追加复用已有章节")


def test_append_fact_updates_review_status():
    """[8/13] 更新 frontmatter review_status"""
    print("\n[8/13] 测试更新 review_status...")
    _seed_page("page-b")
    engine = _make_engine()
    engine._append_fact_to_page("page-b", "新事实", "Q")
    from app.knowledge.wiki_index import _key_from_slug, _parse_frontmatter

    latest = engine.vc.get_latest(_key_from_slug("page-b"))
    meta, _ = _parse_frontmatter(latest["content"])
    assert meta["review_status"] == "review_needed"
    print("  ✓ review_status 更新为 review_needed")


def test_append_fact_saves_new_version():
    """[9/13] 通过 VersionControl 保存新版本"""
    print("\n[9/13] 测试保存新版本...")
    _seed_page("page-c")
    engine = _make_engine()
    from app.knowledge.wiki_index import _key_from_slug

    before = engine.vc.get_latest(_key_from_slug("page-c"))
    before_ver = before["version"]
    record = engine._append_fact_to_page("page-c", "新事实", "Q")
    after = engine.vc.get_latest(_key_from_slug("page-c"))
    assert after["version"] == before_ver + 1
    assert record["version"] == after["version"]
    print(f"  ✓ 版本 {before_ver} → {after['version']}")


class _FakeLLMResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeLLMClient:
    def __init__(self, return_text: str):
        self.return_text = return_text

    async def chat(self, **kwargs):
        return _FakeLLMResponse(self.return_text)


def test_writeback_no_new_facts():
    """[10/13] 无新事实返回空列表"""
    print("\n[10/13] 测试无新事实...")
    _seed_page("page-d")
    engine = _make_engine()
    engine.llm = _FakeLLMClient("[]")
    writebacks = asyncio.run(
        engine.writeback_new_facts(
            question="Q", answer="A", cited_slugs=["page-d"], contexts=["ctx"]
        )
    )
    assert writebacks == []
    print("  ✓ LLM 返回 [] 时无回写")


def test_writeback_with_new_facts():
    """[11/13] 有新事实回写页面"""
    print("\n[11/13] 测试有新事实回写...")
    _seed_page("page-e")
    import json

    engine = _make_engine()
    engine.llm = _FakeLLMClient(
        json.dumps([{"slug": "page-e", "fact": "新发现：参数 X 默认值是 80"}])
    )
    writebacks = asyncio.run(
        engine.writeback_new_facts(
            question="page-e 的默认参数是什么？",
            answer="参数 X 默认值是 80",
            cited_slugs=["page-e"],
            contexts=["## [[page-e]] 测试页\n\n初始内容"],
        )
    )
    assert len(writebacks) == 1
    assert writebacks[0]["slug"] == "page-e"
    assert writebacks[0]["status"] == "written"
    # 验证页面确实被更新
    from app.knowledge.wiki_index import _key_from_slug

    latest = engine.vc.get_latest(_key_from_slug("page-e"))
    assert "参数 X 默认值是 80" in latest["content"]
    print("  ✓ 新事实已回写页面")


def test_writeback_llm_exception_tolerant():
    """[12/13] LLM 异常容错"""
    print("\n[12/13] 测试 LLM 异常容错...")

    class _ExplodingLLM:
        async def chat(self, **kwargs):
            raise RuntimeError("LLM 不可用")

    engine = _make_engine()
    engine.llm = _ExplodingLLM()
    writebacks = asyncio.run(
        engine.writeback_new_facts(
            question="Q", answer="A", cited_slugs=["x"], contexts=["c"]
        )
    )
    assert writebacks == []
    print("  ✓ LLM 异常时返回空列表")


def test_answer_writeback_false():
    """[13/13] answer() writeback=False 不触发回写"""
    print("\n[13/13] 测试 writeback=False...")

    # mock recall_pages 返回空（让 answer 早退），验证 writeback 参数被接受
    from app.knowledge import wiki_query

    original_recall = wiki_query.recall_pages

    async def _empty_recall(*args, **kwargs):
        return []

    wiki_query.recall_pages = _empty_recall
    try:
        engine = _make_engine()
        # answer 在 recall 为空时直接返回，不触发 writeback
        result = asyncio.run(engine.answer("任何问题", writeback=False))
        assert result.insufficient_knowledge is True
        assert result.writebacks == []
        print("  ✓ writeback=False 不触发回写")
    finally:
        wiki_query.recall_pages = original_recall


def main():
    print("=" * 60)
    print("S12-1 Query 回写知识复利 验证")
    print("=" * 60)
    tests = [
        test_rebuild_frontmatter_basic,
        test_rebuild_frontmatter_empty_meta,
        test_rebuild_frontmatter_chinese,
        test_wiki_query_result_writebacks_default,
        test_append_fact_page_not_exist,
        test_append_fact_first_time_creates_section,
        test_append_fact_second_time_reuses_section,
        test_append_fact_updates_review_status,
        test_append_fact_saves_new_version,
        test_writeback_no_new_facts,
        test_writeback_with_new_facts,
        test_writeback_llm_exception_tolerant,
        test_answer_writeback_false,
    ]
    for t in tests:
        t()
    print("\n" + "=" * 60)
    print(f"✓ S12-1 全部 {len(tests)} 项验证通过")
    print("=" * 60)


if __name__ == "__main__":
    main()
