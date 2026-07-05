"""Wiki 漂移检测与 stale 标注（P1-1 / P1-4 共用基础设施）

Karpathy LLM Wiki 的核心防御机制：raw 文档（L1）变化 → 标注关联 wiki 页面 stale
→ 触发重编译 → diff → ReviewQueue 人工裁定。

LLM 编译的 wiki 是有损压缩（"知识漂移"），漂移会沿引用链累积。stale 标注是第一道防线。

核心职责：
- 检测 raw 文档 checksum 变化（与上次编译时记录的 checksum 对比）
- 找到所有引用该 doc 的 wiki 页面，置 stale=true
- 列出所有 stale 页面，供 Lint / 重编译流程消费
- 重编译完成后清除 stale 标记

数据存储：wiki 页面 frontmatter 中 `stale: true/false` 已由 wiki_compiler 维护；
本模块额外用 SQLite 维护「doc_id → 影响的 slugs」反向索引以加速查询。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import structlog
import yaml

from app.knowledge.wiki_index import list_wiki_pages, _key_from_slug
from app.storage import get_document_store
from app.storage.version_control import get_version_control

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "events.db"


# ────────── 数据模型 ──────────

@dataclass
class StalePage:
    """stale wiki 页面"""
    slug: str
    title: str
    type: str
    source_doc_id: str           # 触发 stale 的 raw 文档
    old_checksum: str            # 编译时记录的 checksum
    new_checksum: str            # 当前 raw 文档 checksum
    last_compiled_at: str


@dataclass
class DriftReport:
    """一次漂移检测报告"""
    doc_id: str
    changed: bool                # raw 是否发生变化
    affected_slugs: list[str] = field(default_factory=list)
    new_checksum: str = ""
    old_checksum: str = ""


# ────────── DB 初始化 ──────────

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wiki_doc_checksums (
            doc_id TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            compiled_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_wdc_checksum ON wiki_doc_checksums(checksum);
    """)


# ────────── 公共 API ──────────

def record_compiled_checksum(doc_id: str, checksum: str) -> None:
    """记录 raw 文档编译时的 checksum（编译成功后调用）

    Args:
        doc_id: raw 文档 ID
        checksum: 编译时的 raw checksum
    """
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO wiki_doc_checksums
           (doc_id, checksum, compiled_at, updated_at)
           VALUES (?, ?, ?, ?)""",
        (doc_id, checksum, now, now),
    )
    conn.commit()
    logger.info("wiki_checksum_recorded", doc_id=doc_id, checksum=checksum[:12])


def get_compiled_checksum(doc_id: str) -> str | None:
    """获取 raw 文档上次编译时记录的 checksum"""
    conn = _get_db()
    row = conn.execute(
        "SELECT checksum FROM wiki_doc_checksums WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    return row["checksum"] if row else None


def detect_drift(doc_id: str) -> DriftReport:
    """检测 raw 文档是否相对上次编译发生了变化

    Args:
        doc_id: raw 文档 ID

    Returns:
        DriftReport，含 changed 标志与受影响的 wiki slugs
    """
    store = get_document_store()
    meta = store.get(doc_id)
    if not meta:
        return DriftReport(doc_id=doc_id, changed=False)

    new_checksum = meta.get("checksum", "")
    old_checksum = get_compiled_checksum(doc_id)

    if not old_checksum:
        # 首次记录：不算 changed，但记录 checksum
        record_compiled_checksum(doc_id, new_checksum)
        return DriftReport(
            doc_id=doc_id, changed=False,
            new_checksum=new_checksum, old_checksum="",
        )

    if old_checksum == new_checksum:
        return DriftReport(
            doc_id=doc_id, changed=False,
            new_checksum=new_checksum, old_checksum=old_checksum,
        )

    # 发生变化：找到所有引用该 doc 的 wiki 页面
    affected = _find_pages_citing_doc(doc_id)
    logger.info(
        "wiki_drift_detected",
        doc_id=doc_id,
        old=old_checksum[:12], new=new_checksum[:12],
        affected=len(affected),
    )
    return DriftReport(
        doc_id=doc_id, changed=True,
        affected_slugs=affected,
        new_checksum=new_checksum, old_checksum=old_checksum,
    )


def mark_pages_stale(slugs: list[str], source_doc_id: str) -> int:
    """把指定 wiki 页面标记为 stale（更新 frontmatter）

    Args:
        slugs: 要标记的 wiki slug 列表
        source_doc_id: 触发 stale 的 raw 文档

    Returns:
        成功标记的页面数
    """
    vc = get_version_control()
    count = 0
    for slug in slugs:
        doc_key = _key_from_slug(slug)
        latest = vc.get_latest(doc_key)
        if not latest:
            continue
        meta, body = _split_frontmatter(latest["content"])
        if meta.get("stale") is True:
            continue
        meta["stale"] = True
        meta["stale_reason"] = f"source doc {source_doc_id} changed"
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        new_md = _assemble_md(meta, body)
        vc.save_version(
            doc_key=doc_key,
            title=latest["title"],
            content=new_md,
            author="wiki-drift-detector",
            change_summary=f"标记 stale（source {source_doc_id} 变化）",
        )
        count += 1
    logger.info("wiki_pages_marked_stale", count=count, source=source_doc_id)
    return count


def clear_stale(slug: str) -> bool:
    """清除 wiki 页面的 stale 标记（重编译成功后调用）

    Args:
        slug: wiki slug

    Returns:
        是否成功清除
    """
    vc = get_version_control()
    doc_key = _key_from_slug(slug)
    latest = vc.get_latest(doc_key)
    if not latest:
        return False
    meta, body = _split_frontmatter(latest["content"])
    if not meta.get("stale"):
        return True  # 已是非 stale
    meta["stale"] = False
    meta.pop("stale_reason", None)
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    new_md = _assemble_md(meta, body)
    vc.save_version(
        doc_key=doc_key,
        title=latest["title"],
        content=new_md,
        author="wiki-drift-detector",
        change_summary="清除 stale（已重编译）",
    )
    return True


def list_stale_pages() -> list[StalePage]:
    """列出所有 stale 的 wiki 页面"""
    pages = list_wiki_pages(limit=10000)
    stale: list[StalePage] = []
    vc = get_version_control()
    for p in pages:
        latest = vc.get_latest(_key_from_slug(p["slug"]))
        if not latest:
            continue
        meta, _ = _split_frontmatter(latest["content"])
        if not meta.get("stale"):
            continue
        # 找触发 stale 的 doc（取 sources 第一个或 stale_reason 中的 doc_id）
        source_doc = ""
        reason = meta.get("stale_reason", "")
        if "source doc" in reason:
            try:
                source_doc = reason.split("source doc")[1].split(" ")[1].strip()
            except Exception:
                pass
        if not source_doc:
            sources = meta.get("sources", []) or []
            if sources and isinstance(sources[0], dict):
                source_doc = sources[0].get("doc_id", "")
        # 取 checksum
        old_cs = ""
        new_cs = ""
        store_meta = get_document_store().get(source_doc) if source_doc else None
        if store_meta:
            new_cs = store_meta.get("checksum", "")
        old_cs = get_compiled_checksum(source_doc) or ""
        stale.append(StalePage(
            slug=p["slug"],
            title=p["title"],
            type=p["type"],
            source_doc_id=source_doc,
            old_checksum=old_cs,
            new_checksum=new_cs,
            last_compiled_at=p.get("updated_at", ""),
        ))
    return stale


# ────────── 内部工具 ──────────

def _find_pages_citing_doc(doc_id: str) -> list[str]:
    """找到所有引用该 doc_id 的 wiki 页面 slug

    通过解析每个 wiki 页面 frontmatter 中的 sources 字段。
    """
    pages = list_wiki_pages(limit=10000)
    vc = get_version_control()
    affected: list[str] = []
    for p in pages:
        latest = vc.get_latest(_key_from_slug(p["slug"]))
        if not latest:
            continue
        meta, _ = _split_frontmatter(latest["content"])
        sources = meta.get("sources", []) or []
        for s in sources:
            if isinstance(s, dict) and s.get("doc_id") == doc_id:
                affected.append(p["slug"])
                break
    return affected


def _split_frontmatter(md: str) -> tuple[dict, str]:
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


def _assemble_md(meta: dict, body: str) -> str:
    clean = {k: v for k, v in meta.items() if v is not None}
    fm = yaml.safe_dump(clean, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body.strip()}\n"


# ────────── P1-4 自动重编译闭环 ──────────

@dataclass
class RecompileJob:
    """单文档重编译任务记录"""
    doc_id: str
    slugs_affected: list[str]
    pages_created: int = 0
    pages_updated: int = 0
    pages_unchanged: int = 0
    diff_summary: dict = field(default_factory=dict)   # {slug: {added, removed, v1, v2}}
    review_queued: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class RecompileBatchResult:
    """批量重编译结果"""
    jobs: list[RecompileJob] = field(default_factory=list)
    total_recompiled: int = 0
    total_review_queued: int = 0
    total_errors: int = 0
    skipped: int = 0  # 无 stale 的文档数


async def auto_recompile_stale(*, push_review: bool = True) -> RecompileBatchResult:
    """扫描所有 stale wiki 页面 → 自动触发对应 raw 文档重编译 → diff → ReviewQueue

    流程（AGENTS.md §八 8.1）：
        1. list_stale_pages() 找出所有 stale
        2. 按 source_doc_id 分组
        3. 对每个 doc_id 调 wiki_compiler.compile_raw_to_wiki(force=True)
        4. 对比新旧版本 diff，若内容变化 → 推 ReviewQueue 人工裁定
        5. 重编译成功后 clear_stale

    Args:
        push_review: 是否把 diff 推送到 ReviewQueue

    Returns:
        RecompileBatchResult
    """
    # 延迟导入避免循环依赖
    from app.knowledge.wiki_compiler import get_wiki_compiler
    from app.knowledge.review_queue import get_review_queue

    stale_pages = list_stale_pages()
    if not stale_pages:
        return RecompileBatchResult()

    # 按 doc_id 分组
    by_doc: dict[str, list[str]] = {}
    for p in stale_pages:
        if not p.source_doc_id:
            continue
        by_doc.setdefault(p.source_doc_id, []).append(p.slug)

    batch = RecompileBatchResult()
    compiler = get_wiki_compiler()
    vc = get_version_control()

    for doc_id, slugs in by_doc.items():
        job = RecompileJob(doc_id=doc_id, slugs_affected=slugs)

        # 记录重编译前的版本号
        pre_versions: dict[str, int] = {}
        for slug in slugs:
            latest = vc.get_latest(_key_from_slug(slug))
            if latest:
                pre_versions[slug] = latest["version"]

        # 触发重编译
        try:
            result = await compiler.compile_raw_to_wiki(doc_id, force=True)
            job.pages_created = result.pages_created
            job.pages_updated = result.pages_updated
            job.pages_unchanged = result.pages_unchanged
            job.errors = result.errors
            batch.total_recompiled += result.pages_created + result.pages_updated
            batch.total_errors += len(result.errors)
        except Exception as e:
            job.errors.append(f"重编译失败: {e}")
            batch.total_errors += 1
            batch.jobs.append(job)
            continue

        # diff 新旧版本 + 推 ReviewQueue
        review_queue = get_review_queue() if push_review else None
        for slug in slugs:
            new_latest = vc.get_latest(_key_from_slug(slug))
            if not new_latest:
                continue
            old_v = pre_versions.get(slug, 0)
            new_v = new_latest["version"]
            if new_v <= old_v:
                # 内容未变（compiler 判定 unchanged）
                continue
            # 计算 diff
            if old_v > 0:
                try:
                    diff = vc.diff(_key_from_slug(slug), old_v, new_v)
                    job.diff_summary[slug] = {
                        "v1": old_v, "v2": new_v,
                        "added_lines": diff.get("added_lines", 0),
                        "removed_lines": diff.get("removed_lines", 0),
                    }
                except Exception as e:
                    job.diff_summary[slug] = {"v1": old_v, "v2": new_v, "error": str(e)}
            else:
                job.diff_summary[slug] = {"v1": 0, "v2": new_v, "added_lines": 0, "removed_lines": 0}

            # 推 ReviewQueue
            if review_queue and (job.diff_summary[slug].get("added_lines", 0) > 0
                                  or job.diff_summary[slug].get("removed_lines", 0) > 0):
                try:
                    review_queue.add_entity(
                        entity_type="WikiDrift",
                        name=slug,
                        properties={
                            "doc_id": doc_id,
                            "old_version": old_v,
                            "new_version": new_v,
                            "diff": job.diff_summary[slug],
                        },
                        confidence=0.8,
                        evidence=f"raw {doc_id} 变化触发 {slug} 重编译",
                        source_doc_id=doc_id,
                    )
                    job.review_queued += 1
                    batch.total_review_queued += 1
                except Exception as e:
                    job.errors.append(f"ReviewQueue 推送失败 {slug}: {e}")

        batch.jobs.append(job)

    logger.info(
        "wiki_auto_recompile_done",
        jobs=len(batch.jobs),
        recompiled=batch.total_recompiled,
        review_queued=batch.total_review_queued,
        errors=batch.total_errors,
    )
    return batch
