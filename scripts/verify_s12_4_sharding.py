"""S12-4 index.md 分片准备 验证脚本

验证 wiki_index.rebuild_index 支持按类型分片功能。

测试覆盖：
1. rebuild_index 非分片模式（旧行为兼容）
2. rebuild_index 强制分片模式（shard_by_type=True）
3. rebuild_index 自动分片（pages > SHARD_THRESHOLD）
4. rebuild_index 自动不分片（pages <= SHARD_THRESHOLD）
5. rebuild_index 返回 sharded 标志
6. rebuild_index 返回 shards 列表
7. _render_shard_md 渲染分片内容
8. _render_shard_md 含返回主页链接
9. _render_hub_md 渲染 hub 内容
10. _render_hub_md 含分片链接
11. _render_hub_md type=index-hub
12. _render_index_md type=index
13. list_index_shards 列出所有分片
14. list_index_shards 按类型排序
15. get_index_shard 获取指定分片
16. get_index_shard 不存在返回 None
17. SHARD_THRESHOLD 常量值正确
18. 多类型混合时分片正确
19. SHARD_TYPES 外的类型仍可分片（兜底）
20. 分片模式下 hub 不展开各类型详情
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_s124_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

import app.knowledge.wikilink as wl_mod  # noqa: E402

wl_mod.DB_PATH = TMP_DIR / "events.db"

import app.storage.version_control as vc_mod  # noqa: E402

vc_mod.DB_PATH = TMP_DIR / "versions.db"

from app.knowledge.wiki_index import (  # noqa: E402
    SHARD_THRESHOLD,
    _render_hub_md,
    _render_index_md,
    _render_shard_md,
    get_index,
    get_index_shard,
    list_index_shards,
    rebuild_index,
)
from app.knowledge.wikilink import update_backlinks  # noqa: E402
from app.storage.version_control import get_version_control  # noqa: E402


def _reset_db():
    """重置版本库与 backlink 表，确保测试间隔离"""
    import sqlite3
    # 删除整个 DB 文件，让下次调用自动重建 schema
    for p in (vc_mod.DB_PATH, wl_mod.DB_PATH):
        try:
            if p.exists():
                p.unlink()
            # 同时清理 WAL/SHM 文件
            for suffix in ("-wal", "-shm"):
                wal = p.with_suffix(p.suffix + suffix)
                if wal.exists():
                    wal.unlink()
        except OSError:
            pass
    # 触发 schema 初始化（避免后续测试报 no such table）
    vc = get_version_control()
    vc.save_version("__init__", "init", "", author="reset", change_summary="init")
    # 删除刚才的 init 记录
    conn = sqlite3.connect(str(vc_mod.DB_PATH))
    conn.execute("DELETE FROM document_versions WHERE doc_key = '__init__'")
    conn.commit()
    conn.close()


def _seed_page(slug: str, title: str, page_type: str, body: str = "", tags=None, vc=None):
    """在版本库中创建一个 wiki 页面"""
    if vc is None:
        vc = get_version_control()
    tags = tags or []
    front = (
        "---\n"
        f"slug: {slug}\n"
        f"title: {title}\n"
        f"type: {page_type}\n"
        f"tags: {tags}\n"
        "sources: []\n"
        "updated_at: 2026-07-05T00:00:00Z\n"
        "review_status: auto\n"
        "---\n\n"
    )
    content = front + (body or f"# {title}\n\n内容。")
    vc.save_version(
        doc_key=f"wiki:{slug}",
        title=title,
        content=content,
        author="test-seed",
        change_summary="seed",
    )
    update_backlinks(slug, content)


# ────────── 测试 ──────────


def test_rebuild_no_shard_compat():
    """测试 1: rebuild_index 非分片模式（旧行为兼容）"""
    print("\n[1/20] 测试 rebuild_index 非分片模式...")
    _reset_db()
    _seed_page("p1", "Page1", "concept")
    _seed_page("p2", "Page2", "incident")

    result = rebuild_index(shard_by_type=False)
    assert result["saved"] is True
    assert result["sharded"] is False
    assert result["shards"] == []
    assert result["pages_count"] == 2

    idx = get_index()
    assert idx is not None
    # 旧行为：index 含完整类型分组（不再分片）
    assert "## 按类型浏览" in idx["content"]
    assert "[[p1|Page1]]" in idx["content"]
    print("  ✓ 非分片模式 backward-compatible")


def test_rebuild_force_shard():
    """测试 2: rebuild_index 强制分片模式"""
    print("\n[2/20] 测试 rebuild_index 强制分片模式...")
    _reset_db()
    _seed_page("s1", "S1", "service")
    _seed_page("s2", "S2", "service")
    _seed_page("c1", "C1", "concept")

    result = rebuild_index(shard_by_type=True)
    assert result["saved"] is True
    assert result["sharded"] is True
    assert len(result["shards"]) == 2  # service + concept
    shard_types = {s["type"] for s in result["shards"]}
    assert shard_types == {"service", "concept"}
    print(f"  ✓ 强制分片: shards = {result['shards']}")


def test_rebuild_auto_shard_above_threshold():
    """测试 3: rebuild_index 自动分片（pages > SHARD_THRESHOLD）"""
    print("\n[3/20] 测试自动分片（> SHARD_THRESHOLD）...")
    _reset_db()
    # 创建 SHARD_THRESHOLD + 1 个页面
    for i in range(SHARD_THRESHOLD + 1):
        _seed_page(f"auto-{i}", f"Auto{i}", "concept")

    result = rebuild_index()  # shard_by_type=None
    assert result["sharded"] is True
    assert result["pages_count"] == SHARD_THRESHOLD + 1
    print(f"  ✓ 自动分片: pages={result['pages_count']} > {SHARD_THRESHOLD}")


def test_rebuild_auto_no_shard_below_threshold():
    """测试 4: rebuild_index 自动不分片（pages <= SHARD_THRESHOLD）"""
    print("\n[4/20] 测试自动不分片（<= SHARD_THRESHOLD）...")
    _reset_db()
    for i in range(5):
        _seed_page(f"low-{i}", f"Low{i}", "concept")

    result = rebuild_index()  # shard_by_type=None
    assert result["sharded"] is False
    print(f"  ✓ 自动不分片: pages={result['pages_count']} <= {SHARD_THRESHOLD}")


def test_rebuild_returns_sharded_flag():
    """测试 5: rebuild_index 返回 sharded 标志"""
    print("\n[5/20] 测试返回 sharded 标志...")
    _reset_db()
    _seed_page("flag-1", "Flag1", "concept")
    result_shard = rebuild_index(shard_by_type=True)
    result_no = rebuild_index(shard_by_type=False)
    assert result_shard["sharded"] is True
    assert result_no["sharded"] is False
    print("  ✓ sharded 标志正确")


def test_rebuild_returns_shards_list():
    """测试 6: rebuild_index 返回 shards 列表"""
    print("\n[6/20] 测试返回 shards 列表...")
    _reset_db()
    _seed_page("lst-a", "LstA", "incident")
    _seed_page("lst-b", "LstB", "service")
    _seed_page("lst-c", "LstC", "concept")
    result = rebuild_index(shard_by_type=True)
    assert isinstance(result["shards"], list)
    for s in result["shards"]:
        assert "slug" in s
        assert "type" in s
        assert "count" in s
    print(f"  ✓ shards 列表: {result['shards']}")


def test_render_shard_md_content():
    """测试 7: _render_shard_md 渲染分片内容"""
    print("\n[7/20] 测试 _render_shard_md 渲染...")
    pages = [
        {"slug": "p1", "title": "Page1", "tags": ["t1"], "updated_at": "2026-07-05", "review_status": "auto"},
        {"slug": "p2", "title": "Page2", "tags": [], "updated_at": "2026-07-04", "review_status": "review_needed"},
    ]
    md = _render_shard_md("index-concept", "concept", pages)
    assert "slug: index-concept" in md
    assert "type: index-shard" in md
    assert "shard_type: concept" in md
    assert "[[p1|Page1]]" in md
    assert "[[p2|Page2]]" in md
    assert "`需审查`" in md  # review_needed 徽章
    print("  ✓ 分片内容正确")


def test_render_shard_md_back_to_index():
    """测试 8: _render_shard_md 含返回主页链接"""
    print("\n[8/20] 测试分片含返回主页链接...")
    md = _render_shard_md("index-incident", "incident", [])
    assert "[[index|Wiki Index 主页]]" in md
    print("  ✓ 返回主页链接存在")


def test_render_hub_md_content():
    """测试 9: _render_hub_md 渲染 hub 内容"""
    print("\n[9/20] 测试 _render_hub_md 渲染...")
    shards = [
        {"slug": "index-incident", "type": "incident", "count": 3},
        {"slug": "index-concept", "type": "concept", "count": 5},
    ]
    md = _render_hub_md(shards, [], [], [])
    assert "# Wiki Index" in md
    assert "## 按类型浏览（分片）" in md
    assert "[[index-incident|故障（3）]]" in md
    assert "[[index-concept|概念（5）]]" in md
    print("  ✓ hub 内容正确")


def test_render_hub_md_has_shard_links():
    """测试 10: _render_hub_md 含分片链接"""
    print("\n[10/20] 测试 hub 含分片链接...")
    shards = [{"slug": "index-host", "type": "host", "count": 2}]
    md = _render_hub_md(shards, [], [], [])
    assert "[[index-host|" in md
    print("  ✓ 分片链接存在")


def test_render_hub_md_type_index_hub():
    """测试 11: _render_hub_md type=index-hub"""
    print("\n[11/20] 测试 hub frontmatter type=index-hub...")
    md = _render_hub_md([], [], [], [{"slug": "x"}])
    assert "type: index-hub" in md
    assert "shard_count: 0" in md
    print("  ✓ type=index-hub")


def test_render_index_md_type_index():
    """测试 12: _render_index_md type=index（非分片）"""
    print("\n[12/20] 测试 _render_index_md type=index...")
    from collections import defaultdict
    by_type = defaultdict(list)
    by_type["concept"].append({"slug": "p", "title": "P", "tags": [], "updated_at": "2026-07-05"})
    md = _render_index_md(by_type, [], [], [{"slug": "p"}])
    assert "type: index\n" in md
    print("  ✓ type=index")


def test_list_index_shards():
    """测试 13: list_index_shards 列出所有分片"""
    print("\n[13/20] 测试 list_index_shards...")
    _reset_db()
    _seed_page("lst1-a", "LstA", "incident")
    _seed_page("lst1-b", "LstB", "service")
    rebuild_index(shard_by_type=True)

    shards = list_index_shards()
    assert len(shards) >= 2
    slugs = {s["slug"] for s in shards}
    assert "index-incident" in slugs
    assert "index-service" in slugs
    print(f"  ✓ 列出 {len(shards)} 个分片")


def test_list_index_shards_sorted_by_type():
    """测试 14: list_index_shards 按类型排序"""
    print("\n[14/20] 测试 list_index_shards 按类型排序...")
    _reset_db()
    _seed_page("sort-a", "A", "service")
    _seed_page("sort-b", "B", "concept")
    _seed_page("sort-c", "C", "incident")
    rebuild_index(shard_by_type=True)

    shards = list_index_shards()
    types = [s["type"] for s in shards]
    assert types == sorted(types), f"应按类型排序: {types}"
    print(f"  ✓ 排序正确: {types}")


def test_get_index_shard_existing():
    """测试 15: get_index_shard 获取指定分片"""
    print("\n[15/20] 测试 get_index_shard 获取...")
    _reset_db()
    _seed_page("get-a", "GA", "concept")
    rebuild_index(shard_by_type=True)

    shard = get_index_shard("concept")
    assert shard is not None
    assert "Wiki Index - 概念" in shard["title"]
    assert "[[get-a|GA]]" in shard["content"]
    print(f"  ✓ 获取分片: {shard['title']}")


def test_get_index_shard_nonexistent():
    """测试 16: get_index_shard 不存在返回 None"""
    print("\n[16/20] 测试 get_index_shard 不存在...")
    result = get_index_shard("nonexistent-type")
    assert result is None
    print("  ✓ 不存在返回 None")


def test_shard_threshold_value():
    """测试 17: SHARD_THRESHOLD 常量值正确"""
    print("\n[17/20] 测试 SHARD_THRESHOLD 值...")
    assert SHARD_THRESHOLD == 50, f"AGENTS.md §十 规定 > 50 页必须分片，实际 {SHARD_THRESHOLD}"
    print(f"  ✓ SHARD_THRESHOLD = {SHARD_THRESHOLD}")


def test_multi_type_sharding():
    """测试 18: 多类型混合时分片正确"""
    print("\n[18/20] 测试多类型混合分片...")
    _reset_db()
    _seed_page("mix-1", "M1", "incident")
    _seed_page("mix-2", "M2", "incident")
    _seed_page("mix-3", "M3", "service")
    _seed_page("mix-4", "M4", "host")
    _seed_page("mix-5", "M5", "concept")
    _seed_page("mix-6", "M6", "concept")

    result = rebuild_index(shard_by_type=True)
    shard_map = {s["type"]: s["count"] for s in result["shards"]}
    assert shard_map.get("incident") == 2
    assert shard_map.get("service") == 1
    assert shard_map.get("host") == 1
    assert shard_map.get("concept") == 2
    print(f"  ✓ 多类型分片: {shard_map}")


def test_extra_type_fallback():
    """测试 19: SHARD_TYPES 外的类型仍可分片（兜底）"""
    print("\n[19/20] 测试 SHARD_TYPES 外类型兜底...")
    _reset_db()
    # 创建一个 custom 类型页面
    _seed_page("custom-1", "Custom1", "custom-type")

    result = rebuild_index(shard_by_type=True)
    # custom-type 不在 SHARD_TYPES 中，但应通过兜底逻辑生成分片
    shard_types = {s["type"] for s in result["shards"]}
    assert "custom-type" in shard_types
    print(f"  ✓ 兜底分片: {shard_types}")


def test_hub_does_not_expand_details():
    """测试 20: 分片模式下 hub 不展开各类型详情"""
    print("\n[20/20] 测试 hub 不展开详情...")
    _reset_db()
    _seed_page("hub-1", "H1", "incident")
    _seed_page("hub-2", "H2", "concept")
    rebuild_index(shard_by_type=True)

    idx = get_index()
    assert idx is not None
    content = idx["content"]
    # hub 应有分片链接
    assert "[[index-incident|" in content
    assert "[[index-concept|" in content
    # hub 不应有 "## 按类型浏览" + 具体页面展开（应改为 "## 按类型浏览（分片）"）
    assert "## 按类型浏览（分片）" in content
    # 不应出现 "### 故障" 这种类型小标题展开（分片模式下只列分片链接）
    assert "### 故障（" not in content
    assert "### 概念（" not in content
    print("  ✓ hub 不展开详情，仅含分片链接 + 最近变更")


def main():
    print("=" * 60)
    print("S12-4 index.md 分片准备 验证")
    print("=" * 60)

    test_rebuild_no_shard_compat()
    test_rebuild_force_shard()
    test_rebuild_auto_shard_above_threshold()
    test_rebuild_auto_no_shard_below_threshold()
    test_rebuild_returns_sharded_flag()
    test_rebuild_returns_shards_list()
    test_render_shard_md_content()
    test_render_shard_md_back_to_index()
    test_render_hub_md_content()
    test_render_hub_md_has_shard_links()
    test_render_hub_md_type_index_hub()
    test_render_index_md_type_index()
    test_list_index_shards()
    test_list_index_shards_sorted_by_type()
    test_get_index_shard_existing()
    test_get_index_shard_nonexistent()
    test_shard_threshold_value()
    test_multi_type_sharding()
    test_extra_type_fallback()
    test_hub_does_not_expand_details()

    print("\n" + "=" * 60)
    print("✓ S12-4 全部 20 项验证通过！")
    print("=" * 60)


if __name__ == "__main__":
    main()
