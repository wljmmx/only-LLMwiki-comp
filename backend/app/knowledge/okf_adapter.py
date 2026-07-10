"""OKF (Open Knowledge Format) v0.1 适配层（P0-1）

把 OpsKG 内部的 wiki（DB 存储 + [[wikilink]]）适配为符合 OKF v0.1 规范的
Markdown bundle（文件目录树 + 标准 MD 链接），并支持反向导入。

OKF 三硬性约束（https://openknowledgeformat.com/what-is-okf）：
1. 每个非保留概念文件含可解析 YAML frontmatter
2. frontmatter 含非空 `type` 字段
3. 保留文件 `index.md` / `log.md` 守职责

设计要点：
- 不迁移内部存储：DB → 适配层导出为文件目录树
- 双轨链接：内部 [[slug]] → 导出 [display](/{type_dir}/{slug}.md)
- permissive consumption：导入侧容忍未知 type / 缺失字段 / 断链
- producer/consumer 独立：导出物可被任意 OKF 消费者解析

Bundle 目录结构：
    okf-bundle/
    ├── index.md
    ├── log.md
    ├── incidents/{slug}.md
    ├── runbooks/{slug}.md
    ├── services/{slug}.md
    ├── hosts/{slug}.md
    ├── concepts/{slug}.md
    └── entities/{slug}.md
"""

from __future__ import annotations

import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

from app.knowledge.wiki_index import (
    INDEX_SLUG,
    _key_from_slug,
    _parse_frontmatter,
    list_wiki_pages,
)
from app.knowledge.wikilink import WIKILINK_RE, parse_wikilinks
from app.storage.version_control import get_version_control

logger = structlog.get_logger()

# OKF v0.1 规范版本
OKF_VERSION = "0.1"

# 保留文件名（OKF 规范）
RESERVED_FILES = {"index.md", "log.md"}

# 页面类型 → bundle 目录名映射
TYPE_TO_DIR: dict[str, str] = {
    "incident": "incidents",
    "runbook": "runbooks",
    "service": "services",
    "host": "hosts",
    "concept": "concepts",
    "entity": "entities",
}

# 目录名 → 页面类型（反向映射，导入用）
DIR_TO_TYPE: dict[str, str] = {v: k for k, v in TYPE_TO_DIR.items()}

# 兜底目录（未知 type）
FALLBACK_DIR = "concepts"

# OKF 推荐字段（除必填 type 外）
OKF_RECOMMENDED_FIELDS = ("title", "description", "resource", "tags", "timestamp")


# ────────── 数据模型 ──────────


@dataclass
class OKFConcept:
    """OKF bundle 中的单个概念文件"""

    rel_path: str  # 相对 bundle 根的路径，如 "incidents/nginx-502.md"
    frontmatter: dict[str, Any]
    body: str

    @property
    def concept_id(self) -> str:
        """OKF concept ID = 文件路径去 .md 后缀"""
        return self.rel_path[:-3] if self.rel_path.endswith(".md") else self.rel_path

    @property
    def type(self) -> str:
        return str(self.frontmatter.get("type", ""))


@dataclass
class OKFBundle:
    """OKF bundle 内存表示"""

    root: Path
    concepts: list[OKFConcept] = field(default_factory=list)
    index_md: str = ""
    log_md: str = ""


@dataclass
class ExportResult:
    """导出结果"""

    bundle_dir: Path
    pages_exported: int = 0
    index_written: bool = False
    log_written: bool = False
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    """导入结果"""

    pages_imported: int = 0
    pages_skipped: int = 0
    slugs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ────────── 双链转换：[[wikilink]] ↔ 标准 MD 链接 ──────────


def type_dir_for(page_type: str) -> str:
    """页面类型 → bundle 目录名"""
    return TYPE_TO_DIR.get(page_type, FALLBACK_DIR)


def slug_from_concept_id(concept_id: str) -> str:
    """从 OKF concept ID（如 "incidents/nginx-502"）反推 slug（"nginx-502"）"""
    # 取最后一段路径作为 slug
    return concept_id.rsplit("/", 1)[-1]


def build_okf_link(target_slug: str, display: str, slug_to_type: dict[str, str]) -> str:
    """构造 bundle-relative 标准 MD 链接

    Args:
        target_slug: 目标页面 slug
        display: 显示文本
        slug_to_type: slug → page_type 映射，用于确定目标目录

    Returns:
        标准 MD 链接 [display](/{type_dir}/{slug}.md)
        若 slug 未知（断链），返回纯 display（permissive：不破坏文档）
    """
    page_type = slug_to_type.get(target_slug)
    if page_type is None:
        # 断链：保留显示文本，不生成链接（permissive consumption）
        return display
    dir_name = type_dir_for(page_type)
    # bundle-relative 绝对路径（OKF 推荐形式）
    return f"[{display}](/{dir_name}/{target_slug}.md)"


def wikilink_to_okf(md: str, slug_to_type: dict[str, str]) -> str:
    """把 [[wikilink]] 全部转换为 OKF 标准 MD 链接

    支持：
    - [[slug]] → [slug](/{dir}/{slug}.md)
    - [[slug|显示文本]] → [显示文本](/{dir}/{slug}.md)
    - [[#锚点]] 保留不变（页面内跳转）
    - 断链 → 纯显示文本（不破坏文档）
    """

    def replace(m: re.Match) -> str:
        slug = m.group(1)
        display = m.group(2) or slug
        return build_okf_link(slug, display, slug_to_type)

    return WIKILINK_RE.sub(replace, md)


def okf_link_to_wikilink(md: str) -> str:
    """把 OKF 标准 MD 链接转换为内部 [[wikilink]]

    导入时使用。匹配形如 [text](/{dir}/{slug}.md) 的链接。
    非概念文件链接（如外部 URL、非 .md 链接）保留不变（permissive）。
    """
    # 匹配 bundle-relative 链接：[display](/{dir}/{slug}.md)
    pattern = re.compile(
        r"\[([^\]]+)\]\(/([a-zA-Z0-9_\-]+)\/([a-zA-Z0-9_\-]+)\.md\)"
    )

    def replace(m: re.Match) -> str:
        display = m.group(1)
        slug = m.group(3)
        if display == slug:
            return f"[[{slug}]]"
        return f"[[{slug}|{display}]]"

    return pattern.sub(replace, md)


# ────────── frontmatter 规范化 ──────────


def _split_frontmatter(md: str) -> tuple[dict, str]:
    """拆分 frontmatter 与正文"""
    if not md.startswith("---"):
        return {}, md
    parts = md.split("---", 2)
    if len(parts) < 3:
        return {}, md
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, parts[2].lstrip("\n")


def _assemble_frontmatter(meta: dict, body: str) -> str:
    """组装 frontmatter + 正文"""
    clean = {k: v for k, v in meta.items() if v is not None}
    fm = yaml.safe_dump(clean, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body.strip()}\n"


def extract_description(body: str, max_len: int = 160) -> str:
    """从正文抽取一句话描述（用于 OKF description 字段）

    策略：取「## 概述」章节首段；若无，取首段非标题文本。
    """
    lines = body.splitlines()
    in_overview = False
    in_any_section = False
    first_para: list[str] = []
    overview_para: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_any_section = True
            in_overview = "概述" in stripped or "overview" in stripped.lower()
            continue
        if not in_any_section and stripped and not stripped.startswith("#"):
            # H1 标题后、首个 ## 前的内容
            if not stripped.startswith("---"):
                first_para.append(stripped)
        elif in_overview and stripped:
            overview_para.append(stripped)
        elif in_overview and not stripped and overview_para:
            break  # 概述首段结束

    candidate = " ".join(overview_para) if overview_para else " ".join(first_para)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if len(candidate) > max_len:
        candidate = candidate[:max_len].rstrip() + "..."
    return candidate


def derive_resource(meta: dict) -> str:
    """从现有 frontmatter 字段推导 OKF resource URI

    优先级：
    1. sources[0].doc_id → opskg://doc/{doc_id}
    2. properties 中的 IP/host → host://{ip}
    3. properties 中的 service_id → service://{service_id}
    4. 兜底 → opskg://wiki/{slug}
    """
    slug = meta.get("slug", "")
    sources = meta.get("sources", []) or []
    if sources and isinstance(sources[0], dict):
        doc_id = sources[0].get("doc_id", "")
        if doc_id:
            return f"opskg://doc/{doc_id}"

    # 从 properties / stale_reason 等扩展字段尝试推导
    props = meta.get("properties", {})
    if isinstance(props, dict):
        ip = props.get("ip") or props.get("hostname")
        if ip:
            return f"host://{ip}"
        sid = props.get("service_id")
        if sid:
            return f"service://{sid}"

    return f"opskg://wiki/{slug}" if slug else ""


def normalize_frontmatter_for_okf(
    meta: dict, body: str, slug: str
) -> dict:
    """把内部 frontmatter 规范化为 OKF 兼容形式

    - 必填 type 保留
    - 补全推荐字段：description / resource / timestamp
    - 保留扩展字段（sources / review_status / stale / created_at / updated_at）
      OKF 容忍未知字段（permissive）
    """
    normalized = dict(meta)  # 浅拷贝

    # type 必须非空（OKF 硬性约束）
    page_type = normalized.get("type") or "concept"
    normalized["type"] = page_type

    # title 兜底
    if not normalized.get("title"):
        normalized["title"] = slug

    # description 补全
    if not normalized.get("description"):
        desc = extract_description(body)
        if desc:
            normalized["description"] = desc

    # resource 补全
    if not normalized.get("resource"):
        res = derive_resource(normalized)
        if res:
            normalized["resource"] = res

    # timestamp = updated_at（OKF 推荐字段，用 ISO 时间戳）
    if not normalized.get("timestamp"):
        ts = normalized.get("updated_at") or normalized.get("created_at")
        if ts:
            normalized["timestamp"] = ts

    return normalized


# ────────── 导出：DB wiki → OKF bundle ──────────


def export_bundle(out_dir: Path | str, *, include_log: bool = True) -> ExportResult:
    """把整个 wiki 导出为 OKF bundle 目录树

    流程：
    1. 列出所有 wiki 页面（list_wiki_pages）
    2. 构建 slug → type 映射（用于双链转换）
    3. 逐页导出为 {type_dir}/{slug}.md（frontmatter 规范化 + 链接转换）
    4. 生成根 index.md
    5. 生成 log.md（从 VersionControl 变更历史聚合）

    Args:
        out_dir: bundle 输出目录
        include_log: 是否生成 log.md

    Returns:
        ExportResult
    """
    out_dir = Path(out_dir)
    result = ExportResult(bundle_dir=out_dir)

    # 清理/创建输出目录
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    vc = get_version_control()
    pages = list_wiki_pages(limit=100000)

    if not pages:
        logger.info("okf_export_empty")
        # 仍生成空 index.md（OKF 容忍空 bundle）
        (out_dir / "index.md").write_text(
            _render_empty_index(), encoding="utf-8"
        )
        result.index_written = True
        return result

    # 构建 slug → type 映射
    slug_to_type: dict[str, str] = {p["slug"]: p["type"] for p in pages}

    # 按类型分组（用于 index 渲染）
    by_type: dict[str, list[dict]] = {}
    for p in pages:
        by_type.setdefault(p["type"], []).append(p)

    # 逐页导出
    for p in pages:
        slug = p["slug"]
        try:
            latest = vc.get_latest(_key_from_slug(slug))
            if not latest:
                result.skipped.append(slug)
                continue

            meta, body = _split_frontmatter(latest["content"])
            # 规范化 frontmatter
            okf_meta = normalize_frontmatter_for_okf(meta, body, slug)
            # 转换双链
            okf_body = wikilink_to_okf(body, slug_to_type)

            # 确定目录
            dir_name = type_dir_for(okf_meta.get("type", "concept"))
            type_dir = out_dir / dir_name
            type_dir.mkdir(parents=True, exist_ok=True)

            # 写文件
            file_path = type_dir / f"{slug}.md"
            file_path.write_text(
                _assemble_frontmatter(okf_meta, okf_body), encoding="utf-8"
            )
            result.pages_exported += 1
        except Exception as e:
            logger.exception("okf_export_page_failed", slug=slug)
            result.errors.append(f"{slug}: {e}")

    # 生成 index.md
    try:
        index_md = _render_okf_index(pages, by_type)
        (out_dir / "index.md").write_text(index_md, encoding="utf-8")
        result.index_written = True
    except Exception as e:
        result.errors.append(f"index.md: {e}")

    # 生成 log.md
    if include_log:
        try:
            log_md = render_log_md(limit=100)
            (out_dir / "log.md").write_text(log_md, encoding="utf-8")
            result.log_written = True
        except Exception as e:
            result.errors.append(f"log.md: {e}")

    logger.info(
        "okf_export_done",
        pages=result.pages_exported,
        errors=len(result.errors),
        dir=str(out_dir),
    )
    return result


def export_bundle_tarball(out_path: Path | str) -> tuple[Path, ExportResult]:
    """导出为 tarball（.tar.gz）

    Args:
        out_path: tarball 输出路径

    Returns:
        (tarball_path, ExportResult)
    """
    out_path = Path(out_path)
    with tempfile.TemporaryDirectory() as tmp:
        bundle_dir = Path(tmp) / "okf-bundle"
        result = export_bundle(bundle_dir)
        with tarfile.open(out_path, "w:gz") as tar:
            tar.add(bundle_dir, arcname="okf-bundle")
    return out_path, result


def _render_okf_index(
    pages: list[dict], by_type: dict[str, list[dict]]
) -> str:
    """渲染 OKF 根 index.md（渐进披露）

    按类型分组列出概念链接（标准 MD 链接，bundle-relative）。
    """
    now = datetime.now(timezone.utc).isoformat()
    lines: list[str] = []
    lines.append("---")
    lines.append("type: index")
    lines.append(f"title: OpsKG Wiki Index")
    lines.append(f"description: 导航中枢，共 {len(pages)} 个概念")
    lines.append(f"timestamp: {now}")
    lines.append("---")
    lines.append("")
    lines.append("# OpsKG Wiki Index")
    lines.append("")
    lines.append(
        f"> OKF v{OKF_VERSION} bundle. 共 {len(pages)} 个概念。"
        f"最后更新：{now}"
    )
    lines.append("")

    # 按类型分组（标准顺序）
    type_order = ["incident", "runbook", "service", "host", "concept", "entity"]
    for page_type in type_order:
        type_pages = by_type.get(page_type, [])
        if not type_pages:
            continue
        dir_name = type_dir_for(page_type)
        lines.append(f"## {page_type}（{len(type_pages)}）")
        lines.append("")
        for p in sorted(type_pages, key=lambda x: x.get("updated_at", ""), reverse=True):
            slug = p["slug"]
            title = p.get("title") or slug
            tags = p.get("tags", []) or []
            tags_str = " ".join(f"`{t}`" for t in tags[:3])
            lines.append(f"- [{title}](/{dir_name}/{slug}.md) {tags_str}")
        lines.append("")

    # 兜底：未知类型
    extra_types = set(by_type.keys()) - set(type_order)
    for page_type in sorted(extra_types):
        type_pages = by_type[page_type]
        dir_name = type_dir_for(page_type)
        lines.append(f"## {page_type}（{len(type_pages)}）")
        lines.append("")
        for p in type_pages:
            slug = p["slug"]
            title = p.get("title") or slug
            lines.append(f"- [{title}](/{dir_name}/{slug}.md)")
        lines.append("")

    return "\n".join(lines)


def _render_empty_index() -> str:
    now = datetime.now(timezone.utc).isoformat()
    return (
        f"---\n"
        f"type: index\n"
        f"title: OpsKG Wiki Index\n"
        f"description: 空 bundle\n"
        f"timestamp: {now}\n"
        f"---\n\n"
        f"# OpsKG Wiki Index\n\n"
        f"> OKF v{OKF_VERSION} bundle. 暂无概念。\n"
    )


# ────────── log.md 渲染 ──────────


def render_log_md(limit: int = 100) -> str:
    """从 VersionControl 聚合变更历史，渲染为 OKF log.md

    Args:
        limit: 最多记录条数（按时间倒序）

    Returns:
        log.md 内容
    """
    vc = get_version_control()
    # list_by_prefix 返回 wiki:* 的最新版本
    rows = vc.list_by_prefix("wiki:", limit=limit)

    now = datetime.now(timezone.utc).isoformat()
    lines: list[str] = []
    lines.append("---")
    lines.append("type: log")
    lines.append("title: OpsKG Wiki Change Log")
    lines.append(f"description: 最近 {len(rows)} 条变更记录")
    lines.append(f"timestamp: {now}")
    lines.append("---")
    lines.append("")
    lines.append("# Change Log")
    lines.append("")
    lines.append(f"> 自动生成自 VersionControl，最近 {len(rows)} 条变更。")
    lines.append("")

    # 按时间倒序
    rows_sorted = sorted(
        rows, key=lambda r: r.get("created_at", ""), reverse=True
    )
    for r in rows_sorted:
        doc_key = r.get("doc_key", "")
        slug = doc_key[len("wiki:") :] if doc_key.startswith("wiki:") else doc_key
        version = r.get("version", 0)
        title = r.get("title", slug)
        author = r.get("author", "unknown")
        summary = r.get("change_summary", "")
        created = r.get("created_at", "")
        lines.append(
            f"- `{created}` v{version} **{slug}** — {summary}  \n"
            f"  author: {author} | title: {title}"
        )

    return "\n".join(lines)


# ────────── 导入：OKF bundle → DB wiki ──────────


def import_bundle(
    in_dir: Path | str, *, overwrite: bool = False, author: str = "okf-import"
) -> ImportResult:
    """把 OKF bundle 目录树导入为 OpsKG wiki

    permissive consumption：
    - 容忍未知 type（映射为 concept）
    - 容忍缺失推荐字段（补默认值）
    - 容忍断链（标准 MD 链接转换时若目标未知，保留纯文本）
    - 容忍 index.md / log.md 缺失

    Args:
        in_dir: bundle 输入目录
        overwrite: 是否覆盖已存在的 wiki 页面
        author: 导入作者标识

    Returns:
        ImportResult
    """
    in_dir = Path(in_dir)
    result = ImportResult()

    if not in_dir.exists() or not in_dir.is_dir():
        result.errors.append(f"bundle 目录不存在: {in_dir}")
        return result

    vc = get_version_control()

    # 收集所有 .md 文件（排除保留文件）
    md_files = sorted(in_dir.rglob("*.md"))
    concept_files = [
        f for f in md_files if f.name not in RESERVED_FILES
    ]

    if not concept_files:
        result.warnings.append("bundle 中无概念文件")
        return result

    # 第一遍：建立 slug → type 映射（用于链接转换）
    slug_to_type: dict[str, str] = {}
    for f in concept_files:
        slug = f.stem
        try:
            content = f.read_text(encoding="utf-8")
            meta, _ = _split_frontmatter(content)
            slug_to_type[slug] = str(meta.get("type", "concept"))
        except Exception:
            slug_to_type[slug] = "concept"

    # 第二遍：逐文件导入
    for f in concept_files:
        slug = f.stem
        try:
            content = f.read_text(encoding="utf-8")
            meta, body = _split_frontmatter(content)

            # 校验 OKF 硬性约束：type 非空
            page_type = str(meta.get("type", "")).strip()
            if not page_type:
                # permissive：不拒绝，补默认 type
                page_type = "concept"
                result.warnings.append(f"{slug}: type 缺失，已默认为 concept")
                meta["type"] = page_type

            # 反向链接转换：OKF 标准 MD 链接 → [[wikilink]]
            wiki_body = okf_link_to_wikilink(body)

            # 补 slug（OKF 不要求 slug 字段，但 OpsKG 内部需要）
            meta["slug"] = slug
            # 补 review_status
            if not meta.get("review_status"):
                meta["review_status"] = "review_needed"  # 导入内容需审查

            # 组装完整内容
            full_md = _assemble_frontmatter(meta, wiki_body)

            # 检查是否已存在
            doc_key = _key_from_slug(slug)
            existing = vc.get_latest(doc_key)
            if existing and not overwrite:
                result.pages_skipped += 1
                continue

            title = str(meta.get("title", slug))
            vc.save_version(
                doc_key=doc_key,
                title=title,
                content=full_md,
                author=author,
                change_summary=f"OKF bundle 导入（type={page_type}）",
            )
            result.pages_imported += 1
            result.slugs.append(slug)

        except Exception as e:
            logger.exception("okf_import_page_failed", slug=slug)
            result.errors.append(f"{slug}: {e}")

    logger.info(
        "okf_import_done",
        imported=result.pages_imported,
        skipped=result.pages_skipped,
        errors=len(result.errors),
    )
    return result


def import_bundle_tarball(
    tarball_path: Path | str,
    *,
    overwrite: bool = False,
    author: str = "okf-import",
) -> ImportResult:
    """从 tarball 导入 OKF bundle

    Args:
        tarball_path: tarball 路径
        overwrite: 是否覆盖
        author: 作者

    Returns:
        ImportResult
    """
    tarball_path = Path(tarball_path)
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(tmp)
        # 查找 bundle 根目录（含 index.md 或任意 .md 的顶层目录）
        tmp_path = Path(tmp)
        bundle_dirs = [
            d for d in tmp_path.iterdir() if d.is_dir()
        ]
        if not bundle_dirs:
            return ImportResult(errors=["tarball 中无目录"])
        # 取第一个目录作为 bundle 根
        bundle_dir = bundle_dirs[0]
        return import_bundle(bundle_dir, overwrite=overwrite, author=author)


# ────────── Bundle 探测/列举 ──────────


def list_bundle_concepts(bundle_dir: Path | str) -> list[OKFConcept]:
    """列举 bundle 中的所有概念（不解析保留文件）

    Args:
        bundle_dir: bundle 根目录

    Returns:
        OKFConcept 列表
    """
    bundle_dir = Path(bundle_dir)
    concepts: list[OKFConcept] = []

    if not bundle_dir.exists():
        return concepts

    for f in sorted(bundle_dir.rglob("*.md")):
        if f.name in RESERVED_FILES:
            continue
        try:
            content = f.read_text(encoding="utf-8")
            meta, body = _split_frontmatter(content)
            rel = str(f.relative_to(bundle_dir))
            concepts.append(
                OKFConcept(rel_path=rel, frontmatter=meta, body=body)
            )
        except Exception as e:
            logger.warning("okf_list_concept_failed", path=str(f), error=str(e))

    return concepts


def bundle_summary(bundle_dir: Path | str) -> dict:
    """生成 bundle 摘要统计

    Returns:
        {total, by_type, has_index, has_log, with_description, with_resource}
    """
    concepts = list_bundle_concepts(bundle_dir)
    bundle_dir = Path(bundle_dir)

    by_type: dict[str, int] = {}
    with_desc = 0
    with_resource = 0
    for c in concepts:
        t = c.type or "(missing)"
        by_type[t] = by_type.get(t, 0) + 1
        if c.frontmatter.get("description"):
            with_desc += 1
        if c.frontmatter.get("resource"):
            with_resource += 1

    return {
        "total": len(concepts),
        "by_type": by_type,
        "has_index": (bundle_dir / "index.md").exists(),
        "has_log": (bundle_dir / "log.md").exists(),
        "with_description": with_desc,
        "with_resource": with_resource,
        "okf_version": OKF_VERSION,
    }


# ────────── 单例（无状态，保留以兼容惯例）──────────


_adapter = None


def get_okf_adapter():
    """获取 OKF 适配器单例（无状态，主要为依赖注入一致性）"""
    global _adapter
    if _adapter is None:
        _adapter = _OKFAdapter()
    return _adapter


class _OKFAdapter:
    """OKF 适配器门面（封装模块函数，便于 mock/注入）"""

    export_bundle = staticmethod(export_bundle)
    export_bundle_tarball = staticmethod(export_bundle_tarball)
    import_bundle = staticmethod(import_bundle)
    import_bundle_tarball = staticmethod(import_bundle_tarball)
    list_bundle_concepts = staticmethod(list_bundle_concepts)
    bundle_summary = staticmethod(bundle_summary)
    wikilink_to_okf = staticmethod(wikilink_to_okf)
    okf_link_to_wikilink = staticmethod(okf_link_to_wikilink)
    normalize_frontmatter_for_okf = staticmethod(normalize_frontmatter_for_okf)
    render_log_md = staticmethod(render_log_md)
