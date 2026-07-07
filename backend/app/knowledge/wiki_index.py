"""Wiki Index 自动维护（P0-3）

维护 Karpathy LLM Wiki 的 index.md，作为知识网络的导航入口。
按页面类型分组列出所有 wiki 页面 + 最近变更 + orphan 候选。

index.md 保存为特殊 slug `wiki:index`，由本模块自动刷新。

S12-4 分片支持：当 wiki 规模 > SHARD_THRESHOLD 时自动按类型拆分，
生成 `wiki:index-{type}` 分片页面 + `wiki:index` 作为导航中枢。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import structlog
import yaml

from app.knowledge.wikilink import (
    get_orphan_slugs,
    update_backlinks,
)
from app.storage.version_control import get_version_control

logger = structlog.get_logger()

INDEX_SLUG = "wiki:index"

# S12-4: 超过此阈值时自动启用分片（AGENTS.md §十：> 50 页必须分片）
SHARD_THRESHOLD = 50

# 支持的类型分片顺序（与 index.md 中的展示顺序一致）
SHARD_TYPES: list[str] = ["incident", "runbook", "service", "host", "concept", "entity"]

# 类型中文标签（与 _render_index_md 保持一致）
TYPE_LABEL: dict[str, str] = {
    "entity": "实体",
    "concept": "概念",
    "incident": "故障",
    "runbook": "操作手册",
    "service": "服务",
    "host": "主机",
}


def _slug_from_key(doc_key: str) -> str:
    """wiki:nginx-502 → nginx-502"""
    return doc_key[len("wiki:") :] if doc_key.startswith("wiki:") else doc_key


def _key_from_slug(slug: str) -> str:
    return f"wiki:{slug}"


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 (meta, body)"""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].lstrip("\n")


def list_wiki_pages(limit: int = 500) -> list[dict]:
    """列出所有 wiki 页面（最新版本，不含 content）

    Returns:
        [{slug, title, type, tags, created_at, updated_at, review_status, doc_key, version}]
    """
    vc = get_version_control()
    rows = vc.list_by_prefix("wiki:", limit)
    pages = []
    for r in rows:
        # 排除 index 自身
        slug = _slug_from_key(r["doc_key"])
        if slug == "index":
            continue
        # 读取最新版本内容解析 frontmatter
        latest = vc.get_latest(r["doc_key"])
        if not latest:
            continue
        meta, _ = _parse_frontmatter(latest["content"])
        pages.append(
            {
                "slug": slug,
                "doc_key": r["doc_key"],
                "version": r["version"],
                "title": meta.get("title") or r.get("title") or slug,
                "type": meta.get("type", "concept"),
                "tags": meta.get("tags", []),
                "created_at": meta.get("created_at") or r["created_at"],
                "updated_at": meta.get("updated_at") or r["created_at"],
                "review_status": meta.get("review_status", "auto"),
            }
        )
    return pages


def get_all_slugs() -> set[str]:
    """获取所有 wiki 页面的 slug 集合（含 index）"""
    pages = list_wiki_pages(limit=10000)
    slugs = {p["slug"] for p in pages}
    slugs.add("index")
    return slugs


def rebuild_index(
    *,
    shard_by_type: bool | None = None,
) -> dict:
    """重建 index.md

    流程：
    1. 列出所有 wiki 页面
    2. 按类型分组
    3. 检测 orphan（无入链）
    4. 渲染 Markdown
    5. 保存为 wiki:index

    S12-4 分片支持：
    - shard_by_type=None（默认）: 自动判断，pages > SHARD_THRESHOLD 时分片
    - shard_by_type=True: 强制分片
    - shard_by_type=False: 强制不分片（旧行为）

    分片模式下：
    - 生成 wiki:index-{type} 分片页面（含该类型所有页面详情）
    - wiki:index 成为导航中枢，仅含分片链接 + 最近变更 + 孤岛

    Returns:
        {pages_count, orphans_count, types, saved: bool,
         sharded: bool, shards: [{slug, type, count}]}
    """
    pages = list_wiki_pages(limit=10000)
    if not pages:
        logger.info("wiki_index_empty")
        return {
            "pages_count": 0,
            "orphans_count": 0,
            "types": {},
            "saved": False,
            "sharded": False,
            "shards": [],
        }

    # 按类型分组
    by_type: dict[str, list[dict]] = defaultdict(list)
    for p in pages:
        by_type[p["type"]].append(p)
    # 每组内按 updated_at 降序
    for t in by_type:
        by_type[t].sort(key=lambda x: x["updated_at"], reverse=True)

    # 最近变更 Top 10
    recent = sorted(pages, key=lambda x: x["updated_at"], reverse=True)[:10]

    # 孤岛检测
    known_slugs = {p["slug"] for p in pages}
    orphan_slugs = get_orphan_slugs(known_slugs)

    # S12-4: 决定是否分片
    if shard_by_type is None:
        sharded = len(pages) > SHARD_THRESHOLD
    else:
        sharded = bool(shard_by_type)

    vc = get_version_control()
    shards_info: list[dict] = []

    if sharded:
        # 分片模式：先生成各类型分片，再生成 hub
        for page_type in SHARD_TYPES:
            type_pages = by_type.get(page_type, [])
            if not type_pages:
                continue
            shard_slug = f"index-{page_type}"
            shard_md = _render_shard_md(shard_slug, page_type, type_pages)
            vc.save_version(
                doc_key=_key_from_slug(shard_slug),
                title=f"Wiki Index - {TYPE_LABEL.get(page_type, page_type)}",
                content=shard_md,
                author="wiki-index-bot",
                change_summary=f"自动重建分片 index-{page_type}",
            )
            update_backlinks(shard_slug, shard_md)
            shards_info.append(
                {
                    "slug": shard_slug,
                    "type": page_type,
                    "count": len(type_pages),
                }
            )

        # 处理 SHARD_TYPES 之外的类型（兜底）
        extra_types = set(by_type.keys()) - set(SHARD_TYPES)
        for page_type in sorted(extra_types):
            type_pages = by_type[page_type]
            shard_slug = f"index-{page_type}"
            shard_md = _render_shard_md(shard_slug, page_type, type_pages)
            vc.save_version(
                doc_key=_key_from_slug(shard_slug),
                title=f"Wiki Index - {page_type}",
                content=shard_md,
                author="wiki-index-bot",
                change_summary=f"自动重建分片 index-{page_type}",
            )
            update_backlinks(shard_slug, shard_md)
            shards_info.append(
                {
                    "slug": shard_slug,
                    "type": page_type,
                    "count": len(type_pages),
                }
            )

        # hub index
        hub_md = _render_hub_md(shards_info, recent, orphan_slugs, pages)
        result = vc.save_version(
            doc_key=_key_from_slug("index"),
            title="Wiki Index",
            content=hub_md,
            author="wiki-index-bot",
            change_summary="自动重建 index（分片模式）",
        )
        update_backlinks("index", hub_md)
    else:
        # 非分片模式（旧行为）
        md = _render_index_md(by_type, recent, orphan_slugs, pages)
        result = vc.save_version(
            doc_key=_key_from_slug("index"),
            title="Wiki Index",
            content=md,
            author="wiki-index-bot",
            change_summary="自动重建 index",
        )
        update_backlinks("index", md)

    logger.info(
        "wiki_index_rebuilt",
        pages=len(pages),
        orphans=len(orphan_slugs),
        types=len(by_type),
        sharded=sharded,
        shards=len(shards_info),
    )
    return {
        "pages_count": len(pages),
        "orphans_count": len(orphan_slugs),
        "types": {t: len(v) for t, v in by_type.items()},
        "saved": True,
        "version": result.get("version"),
        "sharded": sharded,
        "shards": shards_info,
    }


def _render_index_md(
    by_type: dict[str, list[dict]],
    recent: list[dict],
    orphans: list[str],
    all_pages: list[dict],
) -> str:
    """渲染 index.md 内容（非分片模式）"""
    now = datetime.now(timezone.utc).isoformat()

    lines: list[str] = []
    lines.append("---")
    lines.append("slug: index")
    lines.append("title: Wiki Index")
    lines.append("type: index")
    lines.append(f"updated_at: {now}")
    lines.append("---")
    lines.append("")
    lines.append("# Wiki Index")
    lines.append("")
    lines.append(
        f"> 共 {len(all_pages)} 个页面，{len(orphans)} 个孤岛。最后更新：{now}"
    )
    lines.append("")

    # 按类型分组
    lines.append("## 按类型浏览")
    lines.append("")
    for t in SHARD_TYPES:
        if t not in by_type:
            continue
        label = TYPE_LABEL.get(t, t)
        pages = by_type[t]
        lines.append(f"### {label}（{len(pages)}）")
        lines.append("")
        for p in pages:
            tags_str = " ".join(f"`{tag}`" for tag in p.get("tags", [])[:3])
            lines.append(f"- [[{p['slug']}|{p['title']}]] {tags_str}")
        lines.append("")

    # 兜底：处理 SHARD_TYPES 之外的类型
    extra_types = set(by_type.keys()) - set(SHARD_TYPES)
    for t in sorted(extra_types):
        label = TYPE_LABEL.get(t, t)
        pages = by_type[t]
        lines.append(f"### {label}（{len(pages)}）")
        lines.append("")
        for p in pages:
            tags_str = " ".join(f"`{tag}`" for tag in p.get("tags", [])[:3])
            lines.append(f"- [[{p['slug']}|{p['title']}]] {tags_str}")
        lines.append("")

    # 最近变更
    if recent:
        lines.append("## 最近变更")
        lines.append("")
        for p in recent:
            lines.append(f"- [[{p['slug']}|{p['title']}]] - {p['updated_at']}")
        lines.append("")

    # 孤岛候选
    if orphans:
        lines.append(f"## 孤岛候选（{len(orphans)}）")
        lines.append("")
        lines.append("> 以下页面无任何入链，请评估是否应建立链接或归档。")
        lines.append("")
        for slug in orphans[:20]:
            lines.append(f"- [[{slug}]]")
        if len(orphans) > 20:
            lines.append(f"- ... 共 {len(orphans)} 个")
        lines.append("")

    return "\n".join(lines)


# ────────── S12-4 分片渲染 ──────────


def _render_shard_md(
    shard_slug: str,
    page_type: str,
    type_pages: list[dict],
) -> str:
    """渲染类型分片页面（index-{type}）

    含该类型所有页面的详细列表 + 标签 + 更新时间。
    """
    now = datetime.now(timezone.utc).isoformat()
    label = TYPE_LABEL.get(page_type, page_type)

    lines: list[str] = []
    lines.append("---")
    lines.append(f"slug: {shard_slug}")
    lines.append(f"title: Wiki Index - {label}")
    lines.append("type: index-shard")
    lines.append(f"shard_type: {page_type}")
    lines.append(f"updated_at: {now}")
    lines.append("---")
    lines.append("")
    lines.append(f"# Wiki Index - {label}")
    lines.append("")
    lines.append(f"> 共 {len(type_pages)} 个{label}页面。最后更新：{now}")
    lines.append("")
    lines.append("返回 [[index|Wiki Index 主页]]")
    lines.append("")

    # 页面列表
    lines.append(f"## {label}页面（{len(type_pages)}）")
    lines.append("")
    for p in type_pages:
        tags_str = " ".join(f"`{tag}`" for tag in p.get("tags", [])[:3])
        review_badge = ""
        if p.get("review_status") == "review_needed":
            review_badge = " `需审查`"
        lines.append(
            f"- [[{p['slug']}|{p['title']}]] {tags_str}{review_badge} - {p['updated_at']}"
        )
    lines.append("")

    return "\n".join(lines)


def _render_hub_md(
    shards: list[dict],
    recent: list[dict],
    orphans: list[str],
    all_pages: list[dict],
) -> str:
    """渲染分片模式的 hub index.md

    仅含分片链接 + 最近变更 + 孤岛（不再展开各类型详情）
    """
    now = datetime.now(timezone.utc).isoformat()

    lines: list[str] = []
    lines.append("---")
    lines.append("slug: index")
    lines.append("title: Wiki Index")
    lines.append("type: index-hub")
    lines.append(f"updated_at: {now}")
    lines.append(f"shard_count: {len(shards)}")
    lines.append("---")
    lines.append("")
    lines.append("# Wiki Index")
    lines.append("")
    lines.append(
        f"> 共 {len(all_pages)} 个页面，分 {len(shards)} 个分片，"
        f"{len(orphans)} 个孤岛。最后更新：{now}"
    )
    lines.append("")

    # 分片链接
    lines.append("## 按类型浏览（分片）")
    lines.append("")
    lines.append("> 页面规模较大，已按类型分片。点击进入对应分片查看详情。")
    lines.append("")
    for s in shards:
        label = TYPE_LABEL.get(s["type"], s["type"])
        lines.append(f"- [[{s['slug']}|{label}（{s['count']}）]]")
    lines.append("")

    # 最近变更
    if recent:
        lines.append("## 最近变更")
        lines.append("")
        for p in recent:
            lines.append(f"- [[{p['slug']}|{p['title']}]] - {p['updated_at']}")
        lines.append("")

    # 孤岛候选
    if orphans:
        lines.append(f"## 孤岛候选（{len(orphans)}）")
        lines.append("")
        lines.append("> 以下页面无任何入链，请评估是否应建立链接或归档。")
        lines.append("")
        for slug in orphans[:20]:
            lines.append(f"- [[{slug}]]")
        if len(orphans) > 20:
            lines.append(f"- ... 共 {len(orphans)} 个")
        lines.append("")

    return "\n".join(lines)


def list_index_shards() -> list[dict]:
    """列出所有已存在的 index 分片

    Returns:
        [{slug, type, version, updated_at}]（按 type 排序）
    """
    vc = get_version_control()
    rows = vc.list_by_prefix("wiki:index-", limit=100)
    shards: list[dict] = []
    for r in rows:
        # slug 形如 "index-incident"
        slug = _slug_from_key(r["doc_key"])
        shard_type = slug[len("index-") :] if slug.startswith("index-") else slug
        shards.append(
            {
                "slug": slug,
                "type": shard_type,
                "version": r["version"],
                "updated_at": r["created_at"],
                "title": r["title"],
            }
        )
    shards.sort(key=lambda x: x["type"])
    return shards


def get_index_shard(page_type: str) -> dict | None:
    """获取指定类型的分片内容

    Args:
        page_type: wiki 页面类型（incident/concept/service/...）

    Returns:
        分片最新版本 dict，或 None
    """
    vc = get_version_control()
    return vc.get_latest(_key_from_slug(f"index-{page_type}"))


def get_index() -> dict | None:
    """获取 index.md 内容"""
    vc = get_version_control()
    latest = vc.get_latest(_key_from_slug("index"))
    if not latest:
        return None
    return latest


# 全局单例不需要，所有方法都是无状态的模块函数
