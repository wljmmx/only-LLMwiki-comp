"""[[wikilink]] 双向链接引擎（P0-2）

实现 Karpathy LLM Wiki 范式的核心结构化机制：
- 解析 [[slug]] / [[slug|显示文本]] 语法
- 构建 backlink 反向索引
- 死链检测
- 渲染为 HTML/纯文本

这是 L2 Wiki 层的基础设施，被 wiki_compiler / linter / wiki_api 共用。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "events.db"

# [[slug]] 或 [[slug|显示文本]]
# 不匹配 [[#锚点]]（页面内跳转，不跨页）
WIKILINK_RE = re.compile(r"\[\[([a-zA-Z0-9][a-zA-Z0-9\-_]*)(?:\|([^\]]+))?\]\]")


@dataclass
class WikiLink:
    """解析出的 wikilink"""
    slug: str           # 目标 slug
    display: str        # 显示文本（默认等于 slug）
    raw: str            # 原始文本 [[...]]


@dataclass
class DeadLink:
    """死链"""
    slug: str           # 不存在的目标 slug
    source_slug: str    # 来源页面
    line: int           # 行号


@dataclass
class BacklinkEntry:
    """反向索引条目"""
    source_slug: str    # 来源页面
    target_slug: str    # 目标页面
    display: str
    count: int


def parse_wikilinks(md: str) -> list[WikiLink]:
    """解析 Markdown 中的所有 [[wikilink]]

    Args:
        md: Markdown 文本

    Returns:
        WikiLink 列表（按出现顺序，含重复）

    Example:
        >>> parse_wikilinks("参见 [[nginx-502]] 和 [[nginx-timeout|超时调优]]")
        [WikiLink(slug='nginx-502', display='nginx-502', raw='[[nginx-502]]'),
         WikiLink(slug='nginx-timeout', display='超时调优', raw='[[nginx-timeout|超时调优]]')]
    """
    links: list[WikiLink] = []
    for m in WIKILINK_RE.finditer(md):
        slug = m.group(1)
        display = m.group(2) or slug
        links.append(WikiLink(slug=slug, display=display, raw=m.group(0)))
    return links


def render_wikilinks_html(md: str, slug_to_url: dict[str, str]) -> str:
    """渲染 wikilink 为 HTML <a> 标签

    Args:
        md: 含 [[wikilink]] 的 Markdown
        slug_to_url: slug → URL 映射；不存在则渲染为死链样式

    Returns:
        渲染后的 Markdown（wikilink 替换为 <a>）
    """
    def replace(m: re.Match) -> str:
        slug = m.group(1)
        display = m.group(2) or slug
        if slug in slug_to_url:
            return f'<a href="{slug_to_url[slug]}" class="wikilink">{display}</a>'
        return f'<a class="wikilink deadlink" title="页面不存在: {slug}">{display}</a>'

    return WIKILINK_RE.sub(replace, md)


def render_wikilinks_text(md: str) -> str:
    """渲染 wikilink 为纯文本（去除 [[]]，保留 display）

    用于搜索索引、摘要等场景。
    """
    def replace(m: re.Match) -> str:
        return m.group(2) or m.group(1)
    return WIKILINK_RE.sub(replace, md)


def validate_links(md: str, source_slug: str, existing_slugs: set[str]) -> list[DeadLink]:
    """检测死链

    Args:
        md: 页面 Markdown
        source_slug: 当前页面 slug
        existing_slugs: 已存在的所有 slug 集合

    Returns:
        DeadLink 列表
    """
    dead: list[DeadLink] = []
    for i, line in enumerate(md.splitlines(), 1):
        for m in WIKILINK_RE.finditer(line):
            slug = m.group(1)
            if slug not in existing_slugs:
                dead.append(DeadLink(slug=slug, source_slug=source_slug, line=i))
    return dead


# ────────── 反向索引（backlink）持久化 ──────────

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wiki_backlinks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_slug TEXT NOT NULL,
            target_slug TEXT NOT NULL,
            display TEXT,
            count INTEGER DEFAULT 1,
            updated_at TEXT NOT NULL,
            UNIQUE(source_slug, target_slug)
        );
        CREATE INDEX IF NOT EXISTS idx_backlink_target ON wiki_backlinks(target_slug);
        CREATE INDEX IF NOT EXISTS idx_backlink_source ON wiki_backlinks(source_slug);
    """)


def update_backlinks(source_slug: str, md: str) -> int:
    """更新某页面的出链 backlink 索引

    调用时机：wiki 页面创建/更新时
    流程：删除该 source 的旧 backlink → 重新解析并写入

    Args:
        source_slug: 来源页面 slug
        md: 页面 Markdown 内容

    Returns:
        写入的 backlink 条数
    """
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()

    # 删除旧 backlink
    conn.execute("DELETE FROM wiki_backlinks WHERE source_slug = ?", (source_slug,))

    # 解析当前页面的所有出链
    links = parse_wikilinks(md)
    # 聚合：target → (display, count)
    target_count: dict[str, tuple[str, int]] = {}
    for l in links:
        if l.slug in target_count:
            display, count = target_count[l.slug]
            target_count[l.slug] = (display, count + 1)
        else:
            target_count[l.slug] = (l.display, 1)

    # 写入
    for target, (display, count) in target_count.items():
        conn.execute(
            """INSERT OR REPLACE INTO wiki_backlinks
               (source_slug, target_slug, display, count, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (source_slug, target, display, count, now),
        )
    conn.commit()
    return len(target_count)


def remove_backlinks(source_slug: str) -> int:
    """删除某页面的所有出链 backlink（页面被删除时调用）

    Returns:
        删除的条数
    """
    conn = _get_db()
    cursor = conn.execute(
        "DELETE FROM wiki_backlinks WHERE source_slug = ?", (source_slug,)
    )
    conn.commit()
    return cursor.rowcount


def get_backlinks(target_slug: str) -> list[BacklinkEntry]:
    """查询某页面的所有入链（哪些页面链接到它）

    Args:
        target_slug: 目标页面 slug

    Returns:
        BacklinkEntry 列表
    """
    conn = _get_db()
    rows = conn.execute(
        """SELECT source_slug, target_slug, display, count
           FROM wiki_backlinks WHERE target_slug = ?
           ORDER BY count DESC, source_slug""",
        (target_slug,),
    ).fetchall()
    return [BacklinkEntry(
        source_slug=r["source_slug"],
        target_slug=r["target_slug"],
        display=r["display"],
        count=r["count"],
    ) for r in rows]


def get_outlinks(source_slug: str) -> list[BacklinkEntry]:
    """查询某页面的所有出链（它链接到哪些页面）"""
    conn = _get_db()
    rows = conn.execute(
        """SELECT source_slug, target_slug, display, count
           FROM wiki_backlinks WHERE source_slug = ?
           ORDER BY count DESC, target_slug""",
        (source_slug,),
    ).fetchall()
    return [BacklinkEntry(
        source_slug=r["source_slug"],
        target_slug=r["target_slug"],
        display=r["display"],
        count=r["count"],
    ) for r in rows]


def get_orphan_slugs(known_slugs: set[str]) -> list[str]:
    """查询孤岛页面（无任何入链）

    Args:
        known_slugs: 所有已知 slug 集合

    Returns:
        孤岛 slug 列表
    """
    if not known_slugs:
        return []
    conn = _get_db()
    # 查所有被链接的 target
    rows = conn.execute(
        "SELECT DISTINCT target_slug FROM wiki_backlinks"
    ).fetchall()
    linked_targets = {r["target_slug"] for r in rows}
    # 已知 slug 中不在 linked_targets 的就是孤岛
    return sorted(known_slugs - linked_targets)


def get_all_deadlinks(existing_slugs: set[str]) -> list[DeadLink]:
    """查询所有死链（backlink 表中 target 不存在的）

    Args:
        existing_slugs: 已存在的所有 slug 集合

    Returns:
        DeadLink 列表
    """
    conn = _get_db()
    rows = conn.execute(
        """SELECT DISTINCT source_slug, target_slug
           FROM wiki_backlinks
           WHERE target_slug NOT IN ({})""".format(
               ",".join("?" * len(existing_slugs))
           ) if existing_slugs else
           "SELECT DISTINCT source_slug, target_slug FROM wiki_backlinks",
        tuple(existing_slugs) if existing_slugs else (),
    ).fetchall()
    return [DeadLink(
        slug=r["target_slug"],
        source_slug=r["source_slug"],
        line=0,  # backlink 表不存行号
    ) for r in rows]
