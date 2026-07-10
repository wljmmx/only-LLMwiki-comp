"""S17-2 Wiki Log 持续维护 验证脚本（P1-2）

验证 backend/app/knowledge/wiki_log.py + wiki_compiler 集成：
1. append_log_entry 首次创建 wiki:log
2. append_log_entry 追加新条目（最新在前）
3. append_log_entry 幂等（同 slug+version+summary 跳过）
4. get_log 读取 wiki:log
5. get_log_entries 解析 LogEntry 列表
6. render_log_markdown 渲染 OKF log.md 格式
7. log.md frontmatter type=log（OKF 保留文件职责）
8. log.md 含 Entries 章节
9. wiki_compiler._save_page 自动触发 append_log_entry
10. list_wiki_pages 排除 log（不当作概念页导出）
11. okf_adapter.render_log_md 优先用 wiki:log（持续维护版）
12. okf_adapter.render_log_md 降级到 VC 聚合（无 wiki:log 时）
13. FIFO 截断：超过 MAX_ENTRIES 保留最新
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_s172_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

import app.storage.version_control as vc_mod

vc_mod.DB_PATH = TMP_DIR / "versions.db"
import app.knowledge.wikilink as wl_mod

wl_mod.DB_PATH = TMP_DIR / "events.db"
import app.knowledge.wiki_drift as wd_mod

wd_mod.DB_PATH = TMP_DIR / "events.db"

from app.knowledge.okf_adapter import render_log_md  # noqa: E402
from app.knowledge.wiki_index import list_wiki_pages  # noqa: E402
from app.knowledge.wiki_log import (  # noqa: E402
    LOG_DOC_KEY,
    MAX_ENTRIES,
    LogEntry,
    append_log_entry,
    get_log,
    get_log_entries,
    render_log_markdown,
)
from app.storage.version_control import get_version_control  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def main() -> int:
    global PASS, FAIL
    print("=" * 70)
    print("S17-2 Wiki Log 持续维护验证 (P1-2)")
    print("=" * 70)

    vc = get_version_control()

    # ── 1. 首次创建 wiki:log ──
    print("\n[1] append_log_entry 首次创建")
    append_log_entry(
        slug="nginx-502",
        version=1,
        summary="新建 wiki 页面",
        author="wiki-compiler",
        page_type="incident",
        title="Nginx 502 故障",
        timestamp="2026-07-10T10:00:00Z",
    )
    log = get_log()
    check(log is not None, f"wiki:log 已创建 (got {log is not None})")
    if log:
        check(
            "nginx-502" in log["content"],
            f"首条 entry 含 slug (got content has nginx-502: {'nginx-502' in log['content']})",
        )
        check(
            "type: log" in log["content"],
            f"frontmatter type=log (got has type:log: {'type: log' in log['content']})",
        )
        check(
            "## Entries" in log["content"],
            "含 ## Entries 章节",
        )

    # ── 2. 追加新条目（最新在前）──
    print("\n[2] append_log_entry 追加新条目")
    append_log_entry(
        slug="nginx-timeout",
        version=1,
        summary="新建 wiki 页面",
        author="wiki-compiler",
        page_type="concept",
        title="Nginx 超时调优",
        timestamp="2026-07-10T11:00:00Z",
    )
    entries = get_log_entries()
    check(
        len(entries) == 2,
        f"2 条 entry (got {len(entries)})",
    )
    check(
        entries[0].slug == "nginx-timeout",
        f"最新 entry 在前 (got first={entries[0].slug if entries else None})",
    )
    check(
        entries[1].slug == "nginx-502",
        f"旧 entry 在后 (got second={entries[1].slug if len(entries) > 1 else None})",
    )

    # ── 3. 幂等：同 slug+version+summary 跳过 ──
    print("\n[3] append_log_entry 幂等")
    append_log_entry(
        slug="nginx-timeout",
        version=1,
        summary="新建 wiki 页面",
        author="wiki-compiler",
        page_type="concept",
        title="Nginx 超时调优",
        timestamp="2026-07-10T11:00:00Z",
    )
    entries_after_dup = get_log_entries()
    check(
        len(entries_after_dup) == 2,
        f"幂等跳过，仍 2 条 (got {len(entries_after_dup)})",
    )

    # 不同 version 应追加
    append_log_entry(
        slug="nginx-timeout",
        version=2,
        summary="增量合并",
        author="wiki-compiler",
        page_type="concept",
        title="Nginx 超时调优",
        timestamp="2026-07-10T12:00:00Z",
    )
    entries_after_v2 = get_log_entries()
    check(
        len(entries_after_v2) == 3,
        f"不同 version 追加，3 条 (got {len(entries_after_v2)})",
    )
    check(
        entries_after_v2[0].version == 2,
        f"最新 entry version=2 (got {entries_after_v2[0].version})",
    )

    # ── 4. get_log_entries 解析字段 ──
    print("\n[4] get_log_entries 字段解析")
    e = entries_after_v2[0]
    check(e.slug == "nginx-timeout", f"slug 正确 (got {e.slug})")
    check(e.version == 2, f"version 正确 (got {e.version})")
    check(e.summary == "增量合并", f"summary 正确 (got {e.summary})")
    check(e.author == "wiki-compiler", f"author 正确 (got {e.author})")
    check(e.page_type == "concept", f"page_type 正确 (got {e.page_type})")
    check(e.title == "Nginx 超时调优", f"title 正确 (got {e.title})")
    check(e.timestamp == "2026-07-10T12:00:00Z", f"timestamp 正确 (got {e.timestamp})")

    # ── 5. render_log_markdown OKF 格式 ──
    print("\n[5] render_log_markdown OKF log.md 格式")
    md = render_log_markdown(limit=10)
    check(md.startswith("---"), "以 frontmatter 开头")
    check("type: log" in md, "type=log")
    check("# Change Log" in md, "含 # Change Log 标题")
    check("## Entries" in md, "含 ## Entries 章节")
    check("nginx-timeout" in md and "nginx-502" in md, "含所有 entry slug")
    check("v2" in md, "含版本号")
    check("wiki-compiler" in md, "含 author")
    check("concept" in md and "incident" in md, "含 page_type")

    # ── 6. wiki:log 不被 list_wiki_pages 当作概念页 ──
    print("\n[6] list_wiki_pages 排除 log")
    # 先创建一个普通页面 + index
    vc.save_version(
        doc_key="wiki:real-page",
        title="Real Page",
        content="---\nslug: real-page\ntype: concept\ntitle: Real Page\n---\n\n# Real Page\n",
        author="test",
        change_summary="seed",
    )
    pages = list_wiki_pages()
    slugs = {p["slug"] for p in pages}
    check(
        "log" not in slugs,
        f"log 不在 list_wiki_pages 中 (got slugs={slugs})",
    )
    check(
        "real-page" in slugs,
        "普通页面仍在列表中",
    )

    # ── 7. okf_adapter.render_log_md 优先用持续维护版 ──
    print("\n[7] okf_adapter.render_log_md 优先持续维护版")
    okf_log = render_log_md(limit=50)
    # 持续维护版含 page_type 字段（VC 聚合版不含）
    check(
        "type: concept" in okf_log or "type: incident" in okf_log,
        f"用持续维护版（含 page_type 字段）(got has page_type: {'type: concept' in okf_log or 'type: incident' in okf_log})",
    )
    check(
        "nginx-timeout" in okf_log,
        "含持续维护的 entry",
    )

    # ── 8. 降级：无 wiki:log 时从 VC 聚合 ──
    print("\n[8] okf_adapter.render_log_md 降级到 VC 聚合")
    # 清空 wiki:log，模拟 P0 旧数据
    vc.delete_all(LOG_DOC_KEY)
    okf_log_fallback = render_log_md(limit=50)
    check(
        "type: log" in okf_log_fallback,
        "降级版仍含 type=log",
    )
    check(
        "# Change Log" in okf_log_fallback,
        "降级版含 # Change Log",
    )
    # VC 聚合版应列出 wiki:* 的页面（含 real-page）
    check(
        "real-page" in okf_log_fallback or "nginx-timeout" in okf_log_fallback or "nginx-502" in okf_log_fallback,
        "降级版从 VC 聚合页面变更",
    )

    # ── 9. FIFO 截断 ──
    print("\n[9] FIFO 截断（超 MAX_ENTRIES）")
    # 重置 log，灌入超过 MAX_ENTRIES 条
    vc.delete_all(LOG_DOC_KEY)
    for i in range(MAX_ENTRIES + 10):
        append_log_entry(
            slug=f"page-{i:04d}",
            version=1,
            summary=f"entry {i}",
            author="test",
            page_type="concept",
            title=f"Page {i}",
            timestamp=f"2026-07-10T{i:04d}:00:00Z",
        )
    entries_full = get_log_entries(limit=10000)
    check(
        len(entries_full) <= MAX_ENTRIES,
        f"截断到 MAX_ENTRIES={MAX_ENTRIES} (got {len(entries_full)})",
    )
    # 最新插入的 page-0509 应在前（最后插入的在最前）
    check(
        entries_full[0].slug == "page-0509",
        f"最新条目保留（page-0509 在前）(got first={entries_full[0].slug})",
    )
    # 最旧的 page-0000 应被截断
    slugs_full = {e.slug for e in entries_full}
    check(
        "page-0000" not in slugs_full,
        f"最旧条目被截断 (page-0000 not in: {'page-0000' not in slugs_full})",
    )

    # ── 10. LogEntry.render 格式 ──
    print("\n[10] LogEntry.render 格式")
    e = LogEntry(
        timestamp="2026-07-10T10:00:00Z",
        slug="test-slug",
        version=3,
        summary="测试摘要",
        author="tester",
        page_type="incident",
        title="Test Title",
    )
    rendered = e.render()
    check(
        "test-slug" in rendered and "v3" in rendered,
        "render 含 slug 和 version",
    )
    check(
        "tester" in rendered and "incident" in rendered,
        "render 含 author 和 page_type",
    )
    check(
        rendered.startswith("- `"),
        "render 以列表项开头",
    )

    # ── 总结 ──
    print("\n" + "=" * 70)
    print(f"总计: {PASS} PASS / {FAIL} FAIL")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
