"""Wiki Index 自动维护（P0-3）

维护 Karpathy LLM Wiki 的 index.md，作为知识网络的导航入口。
按页面类型分组列出所有 wiki 页面 + 最近变更 + orphan 候选。

index.md 保存为特殊 slug `wiki:index`，由本模块自动刷新。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import structlog
import yaml

from app.storage.version_control import get_version_control
from app.knowledge.wikilink import (
    get_orphan_slugs,
    update_backlinks,
)

logger = structlog.get_logger()

INDEX_SLUG = "wiki:index"


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


def rebuild_index() -> dict:
    """重建 index.md

    流程：
    1. 列出所有 wiki 页面
    2. 按类型分组
    3. 检测 orphan（无入链）
    4. 渲染 Markdown
    5. 保存为 wiki:index

    Returns:
        {pages_count, orphans_count, types, saved: bool}
    """
    pages = list_wiki_pages(limit=10000)
    if not pages:
        logger.info("wiki_index_empty")
        return {"pages_count": 0, "orphans_count": 0, "types": {}, "saved": False}

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

    # 渲染 Markdown
    md = _render_index_md(by_type, recent, orphan_slugs, pages)

    # 保存
    vc = get_version_control()
    result = vc.save_version(
        doc_key=_key_from_slug("index"),
        title="Wiki Index",
        content=md,
        author="wiki-index-bot",
        change_summary="自动重建 index",
    )

    # 更新 index 自身的 backlink（index 通常无出链，但清理旧数据）
    update_backlinks("index", md)

    logger.info(
        "wiki_index_rebuilt",
        pages=len(pages),
        orphans=len(orphan_slugs),
        types=len(by_type),
    )
    return {
        "pages_count": len(pages),
        "orphans_count": len(orphan_slugs),
        "types": {t: len(v) for t, v in by_type.items()},
        "saved": True,
        "version": result.get("version"),
    }


def _render_index_md(
    by_type: dict[str, list[dict]],
    recent: list[dict],
    orphans: list[str],
    all_pages: list[dict],
) -> str:
    """渲染 index.md 内容"""
    now = datetime.now(timezone.utc).isoformat()

    type_label = {
        "entity": "实体",
        "concept": "概念",
        "incident": "故障",
        "runbook": "操作手册",
        "service": "服务",
        "host": "主机",
    }

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
    for t in ["incident", "runbook", "service", "host", "concept", "entity"]:
        if t not in by_type:
            continue
        label = type_label.get(t, t)
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


def get_index() -> dict | None:
    """获取 index.md 内容"""
    vc = get_version_control()
    latest = vc.get_latest(_key_from_slug("index"))
    if not latest:
        return None
    return latest


# 全局单例不需要，所有方法都是无状态的模块函数
