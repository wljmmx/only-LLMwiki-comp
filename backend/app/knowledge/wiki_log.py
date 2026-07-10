"""Wiki Change Log 持续维护（P1-2）

维护一个特殊的 wiki 页面 `wiki:log`，作为 OKF `log.md` 保留文件的内部对应物。
每次 wiki 页面创建/更新时，调用 `append_log_entry()` 追加一条变更记录，
保证变更审计日志持续维护（而非仅在导出时从 VersionControl 聚合）。

与 okf_adapter.render_log_md() 的关系：
- render_log_md() 优先读取 wiki:log 页面内容（持续维护版）
- 若 wiki:log 不存在，则降级为从 VersionControl 聚合（兼容旧数据）

OKF 保留文件职责（log.md）：
- 记录 bundle 版本演进
- 每个 entry 含时间戳、页面 slug、变更类型、摘要
- 作为审计文件，不作为概念页参与图谱
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
import yaml

from app.storage.version_control import get_version_control

logger = structlog.get_logger()

# 特殊页面键（与 INDEX_SLUG 同一命名空间）
LOG_SLUG = "wiki:log"
LOG_DOC_KEY = "wiki:log"

# 单条 log entry 的格式模板
ENTRY_TEMPLATE = "- `{timestamp}` v{version} **{slug}** — {summary}  \n  author: {author} | type: {page_type} | title: {title}"

# log.md 最大保留条数（FIFO，超出截断旧条目）
MAX_ENTRIES = 500


@dataclass
class LogEntry:
    """单条变更记录"""

    timestamp: str
    slug: str
    version: int
    summary: str
    author: str
    page_type: str = ""
    title: str = ""

    def render(self) -> str:
        return ENTRY_TEMPLATE.format(
            timestamp=self.timestamp,
            version=self.version,
            slug=self.slug,
            summary=self.summary or "(无摘要)",
            author=self.author or "unknown",
            page_type=self.page_type or "-",
            title=self.title or self.slug,
        )


def append_log_entry(
    slug: str,
    version: int,
    summary: str,
    author: str,
    *,
    page_type: str = "",
    title: str = "",
    timestamp: str | None = None,
) -> None:
    """追加一条变更记录到 wiki:log

    幂等设计：若最近一条 entry 与本次完全相同（同 slug+version），则跳过。
    这避免重复编译导致 log 膨胀。

    Args:
        slug: 变更页面的 slug
        version: 新版本号
        summary: 变更摘要
        author: 作者
        page_type: 页面类型（incident/concept/...）
        title: 页面标题
        timestamp: 时间戳（缺省取当前 UTC）
    """
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    entry = LogEntry(
        timestamp=ts,
        slug=slug,
        version=version,
        summary=summary,
        author=author,
        page_type=page_type,
        title=title,
    )

    vc = get_version_control()
    existing = vc.get_latest(LOG_DOC_KEY)

    if existing:
        # 幂等检查：解析已有 entries，若最近一条匹配则跳过
        entries = _parse_entries(existing["content"])
        if entries:
            last = entries[0]  # 最新在顶部
            if (
                last.slug == slug
                and last.version == version
                and last.summary == summary
            ):
                logger.debug("wiki_log_skip_duplicate", slug=slug, version=version)
                return

        # 在顶部插入新 entry（倒序排列，最新在最前）
        new_entry_line = entry.render()
        body = _extract_body(existing["content"])
        # 在 body 的 entries 区域顶部插入
        new_body = _insert_entry_at_top(body, new_entry_line)
        # 截断超限条目
        new_body = _truncate_entries(new_body, MAX_ENTRIES)

        new_content = _assemble_log_md(new_body, entry_count=_count_entries(new_body))
        vc.save_version(
            doc_key=LOG_DOC_KEY,
            title="OpsKG Wiki Change Log",
            content=new_content,
            author=author,
            change_summary=f"log append: {slug} v{version}",
        )
    else:
        # 首次创建
        body = entry.render() + "\n"
        content = _assemble_log_md(body, entry_count=1)
        vc.save_version(
            doc_key=LOG_DOC_KEY,
            title="OpsKG Wiki Change Log",
            content=content,
            author=author,
            change_summary="log init",
        )

    logger.info("wiki_log_appended", slug=slug, version=version)


def get_log() -> dict | None:
    """读取 wiki:log 最新版本

    Returns:
        {doc_key, version, content, created_at, author, ...} 或 None
    """
    vc = get_version_control()
    return vc.get_latest(LOG_DOC_KEY)


def get_log_entries(limit: int = 100) -> list[LogEntry]:
    """解析 wiki:log，返回 LogEntry 列表（最新在前）

    Args:
        limit: 最多返回条数

    Returns:
        LogEntry 列表
    """
    log = get_log()
    if not log:
        return []
    entries = _parse_entries(log["content"])
    return entries[:limit]


def render_log_markdown(limit: int = 100) -> str:
    """渲染 wiki:log 为 markdown（OKF log.md 格式）

    优先用持续维护的 wiki:log 内容；若不存在则返回空骨架。

    Args:
        limit: 最多包含条目数

    Returns:
        log.md 内容
    """
    log = get_log()
    if log:
        # 复用已维护的内容，但限制条目数
        entries = _parse_entries(log["content"])[:limit]
        body = "\n".join(e.render() for e in entries)
        return _assemble_log_md(body, entry_count=len(entries))

    # 无 wiki:log，返回空骨架
    return _assemble_log_md("", entry_count=0)


# ────────── 内部工具 ──────────


_ENTRY_RE = re.compile(
    r"^- `(.*?)` v(\d+) \*\*(.*?)\*\* — (.*?)  \n  author: (.*?) \| type: (.*?) \| title: (.*?)$",
    re.MULTILINE,
)


def _parse_entries(content: str) -> list[LogEntry]:
    """从 log.md 内容解析 LogEntry 列表"""
    entries: list[LogEntry] = []
    for m in _ENTRY_RE.finditer(content):
        entries.append(
            LogEntry(
                timestamp=m.group(1),
                version=int(m.group(2)),
                slug=m.group(3),
                summary=m.group(4),
                author=m.group(5),
                page_type=m.group(6),
                title=m.group(7),
            )
        )
    return entries


def _extract_body(content: str) -> str:
    """从完整 markdown 提取正文（去 frontmatter）"""
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content
    return parts[2].lstrip("\n")


def _insert_entry_at_top(body: str, entry_line: str) -> str:
    """在 body 的 entries 区域顶部插入新条目

    entries 区域以「## Entries」章节标识；若无该章节，插在文件最前。
    """
    if "## Entries" in body:
        # 在 ## Entries 标题后插入
        idx = body.index("## Entries")
        # 找到标题行结尾
        line_end = body.index("\n", idx) + 1
        return body[:line_end] + entry_line + "\n" + body[line_end:]
    # 无 Entries 章节，在首个 ## 章节前插入
    lines = body.splitlines()
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("## "):
            insert_at = i
            break
    else:
        insert_at = 0
    new_lines = lines[:insert_at] + ["## Entries", "", entry_line, ""] + lines[insert_at:]
    return "\n".join(new_lines)


def _truncate_entries(body: str, max_entries: int) -> str:
    """截断超限条目（FIFO，保留最新）"""
    entries = _parse_entries(body)
    if len(entries) <= max_entries:
        return body
    kept = entries[:max_entries]
    kept_lines = [e.render() for e in kept]
    # 重建 body：保留非 entry 行 + 截断后的 entries
    non_entry_lines = [
        line for line in body.splitlines() if not _ENTRY_RE.match(line)
    ]
    # 找到 Entries 章节位置插入
    entries_section_idx = -1
    for i, line in enumerate(non_entry_lines):
        if line.strip() == "## Entries":
            entries_section_idx = i
            break

    if entries_section_idx >= 0:
        # 替换 Entries 章节内容
        before = non_entry_lines[: entries_section_idx + 1]
        after_header = non_entry_lines[entries_section_idx + 1 :]
        # 找到下一个 ## 章节作为 after
        next_section_idx = -1
        for i, line in enumerate(after_header):
            if line.startswith("## "):
                next_section_idx = i
                break
        if next_section_idx >= 0:
            after = after_header[next_section_idx:]
        else:
            after = []
        rebuilt = before + [""] + kept_lines + [""] + after
    else:
        rebuilt = non_entry_lines + ["", "## Entries", ""] + kept_lines + [""]

    return "\n".join(rebuilt)


def _count_entries(body: str) -> int:
    """统计 body 中的 entry 条数"""
    return len(_ENTRY_RE.findall(body))


def _assemble_log_md(body: str, *, entry_count: int) -> str:
    """组装 log.md（frontmatter + body）"""
    now = datetime.now(timezone.utc).isoformat()
    meta = {
        "type": "log",
        "title": "OpsKG Wiki Change Log",
        "description": f"最近 {entry_count} 条变更记录",
        "timestamp": now,
    }
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()

    if not body.strip():
        body = "## Entries\n\n_暂无变更记录_\n"

    # 确保有 Entries 章节
    if "## Entries" not in body:
        body = "## Entries\n\n" + body

    return f"---\n{fm}\n---\n\n# Change Log\n\n> OKF 保留文件 log.md。自动维护，最近变更在前。\n\n{body}\n"
