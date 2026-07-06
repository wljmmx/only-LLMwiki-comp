"""S12-2 Ingest 反向回链 验证脚本

验证 AGENTS.md §五 5.b："已有页面中提及新概念时回链到新页面"

测试覆盖：
1. _is_meaningful_token 长度过滤
2. _is_meaningful_token 中英文差异
3. _replace_first_outside_wikilink 基础替换
4. _replace_first_outside_wikilink 词边界（英文）
5. _replace_first_outside_wikilink 跳过已有 wikilink
6. _replace_first_outside_wikilink 中文匹配
7. _replace_first_outside_wikilink 无匹配返回 False
8. _replace_first_outside_wikilink 跳过紧邻 ]/| 的位置
9. _split_frontmatter_raw 拆分 frontmatter
10. _split_frontmatter_raw 无 frontmatter
11. _insert_wikilink_in_body 基础插入
12. _insert_wikilink_in_body 跳过 frontmatter
13. _insert_wikilink_in_body 跳过代码块
14. _insert_wikilink_in_body 跳过 H1 标题
15. _insert_wikilink_in_body 跳过表格行
16. _insert_wikilink_in_body 已有链接不重复插入
17. _insert_wikilink_in_body 全文仅替换首次出现
18. _backlink_existing_pages 跳过自身
19. _backlink_existing_pages 跳过 index
20. _backlink_existing_pages 已有链接跳过
21. _backlink_existing_pages 计数正确
22. _backlink_existing_pages 候选词过短返回 0
23. _backlink_existing_pages 实际写入版本库
24. _save_page created 触发反向回链
25. _save_page updated 不触发反向回链
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_s122_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

# 重定向关键模块的 DB_PATH 到临时目录
import app.knowledge.wikilink as wl_mod  # noqa: E402

wl_mod.DB_PATH = TMP_DIR / "events.db"

import app.storage.version_control as vc_mod  # noqa: E402

vc_mod.DB_PATH = TMP_DIR / "versions.db"

from app.knowledge.wiki_compiler import WikiCompiler  # noqa: E402
from app.knowledge.wikilink import (  # noqa: E402
    update_backlinks,
)
from app.storage.version_control import get_version_control  # noqa: E402


def _make_compiler() -> WikiCompiler:
    """构造一个不初始化 LLM 的 WikiCompiler（绕过 get_llm_client）"""
    c = WikiCompiler.__new__(WikiCompiler)
    c.vc = get_version_control()
    return c


def _seed_page(slug: str, title: str, body: str, vc=None) -> dict:
    """在版本库中创建一个 wiki 页面，返回最新版本"""
    if vc is None:
        vc = get_version_control()
    front = (
        "---\n"
        f"slug: {slug}\n"
        f"title: {title}\n"
        "type: concept\n"
        "tags: []\n"
        "sources: []\n"
        f"updated_at: 2026-07-05T00:00:00Z\n"
        "review_status: auto\n"
        "---\n\n"
    )
    content = front + body
    result = vc.save_version(
        doc_key=f"wiki:{slug}",
        title=title,
        content=content,
        author="test-seed",
        change_summary="seed",
    )
    update_backlinks(slug, content)
    return result


# ────────── 测试 ──────────


def test_is_meaningful_length():
    """测试 1: _is_meaningful_token 长度过滤"""
    print("\n[1/25] 测试 _is_meaningful_token 长度过滤...")
    assert WikiCompiler._is_meaningful_token("nginx") is True  # 5 字符
    assert WikiCompiler._is_meaningful_token("go") is False  # 2 字符纯 ASCII 太短
    assert WikiCompiler._is_meaningful_token("") is False
    print("  ✓ 短词被正确过滤")


def test_is_meaningful_cjk():
    """测试 2: _is_meaningful_token 中英文差异"""
    print("\n[2/25] 测试 _is_meaningful_token 中英文差异...")
    # 中文 2 字符也算
    assert WikiCompiler._is_meaningful_token("反向代理") is True
    assert WikiCompiler._is_meaningful_token("代理") is True  # 2 个汉字
    # 英文必须 >= 3
    assert WikiCompiler._is_meaningful_token("ok") is False
    assert WikiCompiler._is_meaningful_token("api") is True
    print("  ✓ 中英文最小长度差异正确")


def test_replace_basic():
    """测试 3: _replace_first_outside_wikilink 基础替换"""
    print("\n[3/25] 测试 _replace_first_outside_wikilink 基础替换...")
    c = _make_compiler()
    line = "Nginx 是一个反向代理服务器。"
    new_line, did = c._replace_first_outside_wikilink(
        line, "Nginx", "[[service-nginx|Nginx]]"
    )
    assert did is True
    assert "[[service-nginx|Nginx]]" in new_line
    assert new_line.count("Nginx") == 1  # 替换后只剩 wikilink 内的 Nginx
    print(f"  ✓ 替换成功: {new_line}")


def test_replace_word_boundary():
    """测试 4: _replace_first_outside_wikilink 词边界（英文）"""
    print("\n[4/25] 测试 _replace_first_outside_wikilink 词边界...")
    c = _make_compiler()
    # 不应匹配子串
    line = "Nginxification is not Nginx."
    new_line, did = c._replace_first_outside_wikilink(
        line, "Nginx", "[[service-nginx|Nginx]]"
    )
    assert did is True
    # 第一个 Nginxification 不应被替换（词边界），应该替换第二个 Nginx
    assert "Nginxification" in new_line
    assert "[[service-nginx|Nginx]]" in new_line
    print(f"  ✓ 词边界生效: {new_line}")


def test_replace_skip_existing_wikilink():
    """测试 5: _replace_first_outside_wikilink 跳过已有 wikilink"""
    print("\n[5/25] 测试 _replace_first_outside_wikilink 跳过已有 wikilink...")
    c = _make_compiler()
    line = "参见 [[service-nginx|Nginx]] 配置。Nginx 也可独立运行。"
    new_line, did = c._replace_first_outside_wikilink(
        line, "Nginx", "[[service-nginx|Nginx]]"
    )
    assert did is True
    # 第一个 Nginx 在 [[...]] 内应跳过，替换第二个
    assert new_line.count("[[service-nginx|Nginx]]") == 2
    print(f"  ✓ 跳过已有 wikilink: {new_line}")


def test_replace_cjk():
    """测试 6: _replace_first_outside_wikilink 中文匹配"""
    print("\n[6/25] 测试 _replace_first_outside_wikilink 中文匹配...")
    c = _make_compiler()
    line = "反向代理是常见架构模式。"
    new_line, did = c._replace_first_outside_wikilink(
        line, "反向代理", "[[reverse-proxy|反向代理]]"
    )
    assert did is True
    assert "[[reverse-proxy|反向代理]]" in new_line
    print(f"  ✓ 中文匹配: {new_line}")


def test_replace_no_match():
    """测试 7: _replace_first_outside_wikilink 无匹配返回 False"""
    print("\n[7/25] 测试 _replace_first_outside_wikilink 无匹配...")
    c = _make_compiler()
    line = "Apache 是另一个 Web 服务器。"
    new_line, did = c._replace_first_outside_wikilink(
        line, "Nginx", "[[service-nginx|Nginx]]"
    )
    assert did is False
    assert new_line == line
    print("  ✓ 无匹配返回 False")


def test_replace_skip_boundary_chars():
    """测试 8: _replace_first_outside_wikilink 跳过紧邻 ]/| 的位置"""
    print("\n[8/25] 测试 _replace_first_outside_wikilink 跳过紧邻边界字符...")
    c = _make_compiler()
    # Nginx 紧跟在 | 之后（在 wikilink 内但未匹配整体）— 实际上这种情况
    # 上面 test_replace_skip_existing_wikilink 已覆盖。这里测试紧邻 ] 的边界
    line = "see [[other]] Nginx here"
    new_line, did = c._replace_first_outside_wikilink(
        line, "Nginx", "[[service-nginx|Nginx]]"
    )
    assert did is True
    # 第二个 Nginx 应被替换
    assert "[[service-nginx|Nginx]]" in new_line
    print(f"  ✓ 边界保护生效: {new_line}")


def test_split_frontmatter_raw():
    """测试 9: _split_frontmatter_raw 拆分 frontmatter"""
    print("\n[9/25] 测试 _split_frontmatter_raw 拆分 frontmatter...")
    content = "---\nslug: test\ntitle: Test\n---\n\n# Test\n\nBody text."
    front, body = WikiCompiler._split_frontmatter_raw(content)
    assert front.startswith("---")
    assert front.endswith("---\n")
    assert "slug: test" in front
    assert body.startswith("# Test")
    print("  ✓ frontmatter 拆分正确")


def test_split_frontmatter_raw_no_frontmatter():
    """测试 10: _split_frontmatter_raw 无 frontmatter"""
    print("\n[10/25] 测试 _split_frontmatter_raw 无 frontmatter...")
    content = "# Test\n\nBody text."
    front, body = WikiCompiler._split_frontmatter_raw(content)
    assert front == ""
    assert body == content
    print("  ✓ 无 frontmatter 返回空字符串")


def test_insert_basic():
    """测试 11: _insert_wikilink_in_body 基础插入"""
    print("\n[11/25] 测试 _insert_wikilink_in_body 基础插入...")
    c = _make_compiler()
    content = "---\nslug: a\ntitle: A\n---\n\n# A\n\nNginx 是反向代理。"
    new_content, matched = c._insert_wikilink_in_body(
        content, "service-nginx", ["Nginx"]
    )
    assert matched is True
    assert "[[service-nginx|Nginx]]" in new_content
    # frontmatter 不被破坏
    assert new_content.startswith("---\nslug: a")
    print("  ✓ 基础插入成功")


def test_insert_skip_frontmatter():
    """测试 12: _insert_wikilink_in_body 跳过 frontmatter"""
    print("\n[12/25] 测试 _insert_wikilink_in_body 跳过 frontmatter...")
    c = _make_compiler()
    # title 字段含 Nginx，但不应被替换
    content = "---\nslug: a\ntitle: Nginx Guide\n---\n\n# Nginx Guide\n\nSome text."
    new_content, matched = c._insert_wikilink_in_body(
        content, "service-nginx", ["Nginx"]
    )
    # Nginx 在正文中首次出现是 H1 标题，应被跳过；
    # 然后正文 "Some text." 不含 Nginx，所以不应匹配
    assert matched is False
    print("  ✓ frontmatter 与 H1 都被跳过")


def test_insert_skip_code_block():
    """测试 13: _insert_wikilink_in_body 跳过代码块"""
    print("\n[13/25] 测试 _insert_wikilink_in_body 跳过代码块...")
    c = _make_compiler()
    content = (
        "---\nslug: a\ntitle: A\n---\n\n# A\n\n"
        "```\nNginx config here\n```\n\n"
        "After code block."
    )
    new_content, matched = c._insert_wikilink_in_body(
        content, "service-nginx", ["Nginx"]
    )
    # 代码块内的 Nginx 应被跳过，正文不含 Nginx，所以无匹配
    assert matched is False
    print("  ✓ 代码块被跳过")


def test_insert_skip_h1():
    """测试 14: _insert_wikilink_in_body 跳过 H1 标题"""
    print("\n[14/25] 测试 _insert_wikilink_in_body 跳过 H1 标题...")
    c = _make_compiler()
    content = "---\nslug: a\ntitle: A\n---\n\n# Nginx 配置\n\n正文行。"
    new_content, matched = c._insert_wikilink_in_body(
        content, "service-nginx", ["Nginx"]
    )
    # H1 含 Nginx 应被跳过，正文不含，所以无匹配
    assert matched is False
    print("  ✓ H1 标题被跳过")


def test_insert_skip_table():
    """测试 15: _insert_wikilink_in_body 跳过表格行"""
    print("\n[15/25] 测试 _insert_wikilink_in_body 跳过表格行...")
    c = _make_compiler()
    content = (
        "---\nslug: a\ntitle: A\n---\n\n# A\n\n"
        "| Nginx | value |\n|---|---|\n"
        "\n表格之后是正文。"
    )
    new_content, matched = c._insert_wikilink_in_body(
        content, "service-nginx", ["Nginx"]
    )
    # 表格行的 Nginx 应被跳过，正文 "表格之后" 不含 Nginx，所以无匹配
    assert matched is False
    print("  ✓ 表格行被跳过")


def test_insert_skip_if_already_linked():
    """测试 16: _insert_wikilink_in_body 已有链接不重复插入"""
    print("\n[16/25] 测试 _insert_wikilink_in_body 已有链接跳过...")
    c = _make_compiler()
    # 注意：_insert_wikilink_in_body 自身不做"已有链接"检查
    # 这由 _backlink_existing_pages 在调用前判断
    # 但 _replace_first_outside_wikilink 会跳过 [[...]] 内的匹配
    content = "---\nslug: a\ntitle: A\n---\n\n# A\n\n参见 [[service-nginx|Nginx]] 文档。"
    new_content, matched = c._insert_wikilink_in_body(
        content, "service-nginx", ["Nginx"]
    )
    # 唯一的 Nginx 在 [[...]] 内，应被跳过 → 无匹配
    assert matched is False
    print("  ✓ 已有 wikilink 内的文字被跳过")


def test_insert_only_first_occurrence():
    """测试 17: _insert_wikilink_in_body 全文仅替换首次出现"""
    print("\n[17/25] 测试 _insert_wikilink_in_body 全文仅替换首次...")
    c = _make_compiler()
    content = (
        "---\nslug: a\ntitle: A\n---\n\n# A\n\n"
        "Nginx 第一次出现。\n\nNginx 第二次出现。"
    )
    new_content, matched = c._insert_wikilink_in_body(
        content, "service-nginx", ["Nginx"]
    )
    assert matched is True
    # 应只替换第一次
    assert new_content.count("[[service-nginx|Nginx]]") == 1
    # 第二次的 Nginx 保留原样
    assert "Nginx 第二次出现" in new_content
    print("  ✓ 仅替换首次出现")


def test_backlink_skip_self():
    """测试 18: _backlink_existing_pages 跳过自身"""
    print("\n[18/25] 测试 _backlink_existing_pages 跳过自身...")
    vc = get_version_control()
    c = _make_compiler()
    _seed_page("self-page", "SelfPage", "# SelfPage\n\nSelfPage 是个测试页。", vc)
    # 自身页面正文有 SelfPage 字样，但应跳过
    n = c._backlink_existing_pages("self-page", "SelfPage")
    assert n == 0
    # 自身版本不应增加
    latest = vc.get_latest("wiki:self-page")
    assert latest["version"] == 1
    print("  ✓ 自身被跳过，版本未增加")


def test_backlink_skip_index():
    """测试 19: _backlink_existing_pages 跳过 index"""
    print("\n[19/25] 测试 _backlink_existing_pages 跳过 index...")
    vc = get_version_control()
    c = _make_compiler()
    # index 页面含某概念名，但不应被回链修改
    _seed_page("index", "Wiki Index", "# Wiki Index\n\nNginx 相关页面。", vc)
    n = c._backlink_existing_pages("service-nginx", "Nginx")
    # index 应被跳过；其他页面也没有，所以返回 0
    assert n == 0
    # index 版本不应增加
    latest = vc.get_latest("wiki:index")
    assert latest["version"] == 1
    print("  ✓ index 被跳过")


def test_backlink_skip_already_linked():
    """测试 20: _backlink_existing_pages 已有链接跳过"""
    print("\n[20/25] 测试 _backlink_existing_pages 已有链接跳过...")
    vc = get_version_control()
    c = _make_compiler()
    # 已有页面已含 [[service-nginx]] 链接
    _seed_page(
        "page-a",
        "PageA",
        "# PageA\n\n参见 [[service-nginx|Nginx]] 配置。",
        vc,
    )
    n = c._backlink_existing_pages("service-nginx", "Nginx")
    assert n == 0
    latest = vc.get_latest("wiki:page-a")
    assert latest["version"] == 1  # 不应增加版本
    print("  ✓ 已有链接的页面被跳过")


def test_backlink_count_correct():
    """测试 21: _backlink_existing_pages 计数正确"""
    print("\n[21/25] 测试 _backlink_existing_pages 计数正确...")
    vc = get_version_control()
    c = _make_compiler()
    # 3 个页面都提及 Nginx
    _seed_page("p1", "P1", "# P1\n\nNginx 配置说明。", vc)
    _seed_page("p2", "P2", "# P2\n\nApache 与 Nginx 对比。", vc)
    _seed_page("p3", "P3", "# P3\n\nNginx 反向代理设置。", vc)
    # 1 个页面不含 Nginx
    _seed_page("p4", "P4", "# P4\n\nRedis 配置说明。", vc)

    n = c._backlink_existing_pages("service-nginx", "Nginx")
    assert n == 3, f"应回链 3 个页面，实际 {n}"
    print(f"  ✓ 正确回链 {n} 个页面")


def test_backlink_short_candidate_returns_zero():
    """测试 22: _backlink_existing_pages 候选词过短返回 0"""
    print("\n[22/25] 测试 _backlink_existing_pages 候选词过短...")
    vc = get_version_control()
    c = _make_compiler()
    _seed_page("p1", "P1", "# P1\n\nok 配置说明。", vc)
    # "ok" 是纯 ASCII 2 字符，不达最小长度
    n = c._backlink_existing_pages("concept-ok", "ok")
    assert n == 0
    print("  ✓ 过短候选词返回 0")


def test_backlink_writes_to_version_control():
    """测试 23: _backlink_existing_pages 实际写入版本库"""
    print("\n[23/25] 测试 _backlink_existing_pages 实际写入版本库...")
    vc = get_version_control()
    c = _make_compiler()
    _seed_page("existing", "Existing", "# Existing\n\nNginx 是反向代理。", vc)
    # 调用前版本是 1
    before = vc.get_latest("wiki:existing")
    assert before["version"] == 1

    n = c._backlink_existing_pages("service-nginx", "Nginx")
    assert n == 1

    after = vc.get_latest("wiki:existing")
    assert after["version"] == 2
    assert "[[service-nginx|Nginx]]" in after["content"]
    assert "wiki-backlink-bot" == after["author"]
    assert "反向回链" in after["change_summary"]
    print(f"  ✓ 版本 {before['version']} → {after['version']}，backlink 已写入")


def test_save_page_created_triggers_backlink():
    """测试 24: _save_page created 触发反向回链"""
    print("\n[24/25] 测试 _save_page created 触发反向回链...")
    vc = get_version_control()
    c = _make_compiler()
    # 预先存在的页面，正文含 "Redis"
    _seed_page("page-old", "OldPage", "# OldPage\n\nRedis 是内存数据库。", vc)

    # 新建一个 service-redis 页面
    from app.knowledge.wiki_compiler import WikiPage

    new_page = WikiPage(
        slug="service-redis",
        title="Redis",
        type="service",
        tags=["redis"],
        sources=[{"doc_id": "d1", "title": "doc", "checksum": "x"}],
        body_md="# Redis\n\nRedis 是内存数据库。",
        source_doc_id="d1",
    )
    outcome = c._save_page(new_page, force=False)
    assert outcome == "created"

    # page-old 应被反向回链，版本递增
    old_latest = vc.get_latest("wiki:page-old")
    assert old_latest["version"] == 2, "page-old 应被反向回链更新"
    assert "[[service-redis|Redis]]" in old_latest["content"]
    print("  ✓ _save_page created 自动触发反向回链")


def test_save_page_updated_no_backlink():
    """测试 25: _save_page updated 不触发反向回链"""
    print("\n[25/25] 测试 _save_page updated 不触发反向回链...")
    vc = get_version_control()
    c = _make_compiler()
    # 先建一个已有 service-redis 页面
    _seed_page(
        "service-redis",
        "Redis",
        "# Redis\n\nRedis 是内存数据库。",
        vc,
    )
    # 另一个页面正文含 Redis
    _seed_page("page-old", "OldPage", "# OldPage\n\nRedis 是数据库。", vc)
    old_version = vc.get_latest("wiki:page-old")["version"]

    # 更新 service-redis（已存在）
    from app.knowledge.wiki_compiler import WikiPage

    updated_page = WikiPage(
        slug="service-redis",
        title="Redis",
        type="service",
        tags=["redis"],
        sources=[{"doc_id": "d2", "title": "doc2", "checksum": "y"}],
        body_md="# Redis\n\nRedis 是内存数据库，更新版本。",
        source_doc_id="d2",
    )
    outcome = c._save_page(updated_page, force=True)
    assert outcome == "updated"

    # page-old 不应被反向回链（因为 service-redis 不是新建）
    after_version = vc.get_latest("wiki:page-old")["version"]
    assert after_version == old_version, "updated 不应触发反向回链"
    print("  ✓ _save_page updated 不触发反向回链")


def main():
    print("=" * 60)
    print("S12-2 Ingest 反向回链 验证")
    print("=" * 60)

    test_is_meaningful_length()
    test_is_meaningful_cjk()
    test_replace_basic()
    test_replace_word_boundary()
    test_replace_skip_existing_wikilink()
    test_replace_cjk()
    test_replace_no_match()
    test_replace_skip_boundary_chars()
    test_split_frontmatter_raw()
    test_split_frontmatter_raw_no_frontmatter()
    test_insert_basic()
    test_insert_skip_frontmatter()
    test_insert_skip_code_block()
    test_insert_skip_h1()
    test_insert_skip_table()
    test_insert_skip_if_already_linked()
    test_insert_only_first_occurrence()
    test_backlink_skip_self()
    test_backlink_skip_index()
    test_backlink_skip_already_linked()
    test_backlink_count_correct()
    test_backlink_short_candidate_returns_zero()
    test_backlink_writes_to_version_control()
    test_save_page_created_triggers_backlink()
    test_save_page_updated_no_backlink()

    print("\n" + "=" * 60)
    print("✓ S12-2 全部 25 项验证通过！")
    print("=" * 60)


if __name__ == "__main__":
    main()
