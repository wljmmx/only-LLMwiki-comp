"""KNOW-13: graph→wiki 自动重编译

订阅 graph_event_bus，图谱变更触发受影响 wiki 页面重编译（graph → wiki 方向），
对齐 AGENTS.md §2.1 双向关联模型中"图谱变更触发受影响 Wiki 页面重编译"。

策略：
- **BFS 影响集合**：从变更实体出发，沿 backlink 链找出所有受影响 wiki 页面（深度≤2），
  防止影响集合爆炸（单次事件影响页面上限 MAX_AFFECTED_PAGES）。
- **乐观锁**：in-progress 集合防止同一页面并发重编译；锁带 TTL 防止僵尸锁。
- **异步重编译**：调用 wiki_compiler.recompile_section（基于页面 source doc_id），
  成功后 clear_stale；失败保留 stale 标记，交由 Lint/批量 detect_graph_drift 兜底。
- **降级**：关系级变更（relation_upsert/relation_delete）影响面大，跳过事件驱动，
  交给批量 detect_graph_drift 兜底，避免事件风暴。

与 detect_graph_drift() 的关系：
- detect_graph_drift：批量快照对比，适合定期 Lint / 全量校准
- GraphWikiSync：事件驱动实时重编译，适合图谱写入后立即联动
两者互补，最终一致。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

import structlog
import yaml

from app.knowledge.wiki_drift import clear_stale, mark_pages_stale
from app.knowledge.wiki_index import _key_from_slug, list_wiki_pages
from app.knowledge.wikilink import get_backlinks
from app.realtime.graph_event_bus import GraphEvent, get_graph_event_bus
from app.storage.version_control import get_version_control

logger = structlog.get_logger()

# BFS 最大深度（防止影响集合爆炸）：0=仅直接关联，1=直接关联的 backlink，2=二跳
MAX_BFS_DEPTH = 2
# 单次事件影响页面上限（防止雪崩）
MAX_AFFECTED_PAGES = 20
# 单页重编译锁 TTL（秒），防止僵尸锁
RECOMPILE_LOCK_TTL = 300.0


@dataclass
class RecompileJob:
    """重编译任务（用于日志/监控）"""

    slug: str
    doc_id: str
    entity_id: str
    event_action: str
    queued_at: float = field(default_factory=time.time)


class GraphWikiSync:
    """graph→wiki 自动重编译协调器

    生命周期：app lifespan 启动时调用 start()，停止时调用 stop()。
    线程模型：单 asyncio 任务消费 graph_event_bus，重编译以 create_task 并发执行。
    """

    def __init__(self) -> None:
        # 正在重编译的 slug → 开始时间（乐观锁）
        self._in_progress: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        # wiki slug 集合缓存（避免每次事件都查 DB）
        self._wiki_slug_set: set[str] = set()
        self._slug_set_refreshed_at: float = 0.0

    async def start(self) -> None:
        """启动事件消费后台任务（幂等）"""
        if self._task is not None:
            return
        self._refresh_slug_set()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("graph_wiki_sync.started")

    async def stop(self) -> None:
        """停止后台任务（幂等）"""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("graph_wiki_sync.stop_error", error=str(e))
        self._task = None
        logger.info("graph_wiki_sync.stopped")

    async def _consume_loop(self) -> None:
        """消费 graph_event_bus 事件（主循环）"""
        bus = get_graph_event_bus()
        try:
            async for event in bus.subscribe():
                try:
                    await self._handle_event(event)
                except Exception as e:  # noqa: BLE001
                    logger.error("graph_wiki_sync.handle_error", error=str(e))
        except asyncio.CancelledError:
            logger.info("graph_wiki_sync.loop_cancelled")
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("graph_wiki_sync.loop_error", error=str(e))

    async def _handle_event(self, event: GraphEvent) -> None:
        """处理单个图谱变更事件

        仅处理实体级变更（upsert/delete/merge）；关系级变更影响面大，跳过事件驱动，
        交给批量 detect_graph_drift 兜底。
        """
        if event.action not in ("upsert", "delete", "merge"):
            return
        entity_id = event.entity_id
        if not entity_id:
            return

        # 计算受影响 wiki 页面（BFS）
        affected = self._compute_affected_pages(entity_id)
        if not affected:
            return

        # 标记 stale（让用户/Lint 可感知，也作为重编译失败时的兜底标记）
        try:
            mark_pages_stale(affected, f"graph_entity:{entity_id}")
        except Exception as e:  # noqa: BLE001
            logger.warning("graph_wiki_sync.mark_stale_failed", error=str(e))

        logger.info(
            "graph_wiki_sync.affected",
            entity=entity_id,
            action=event.action,
            pages=len(affected),
        )

        # 异步触发重编译（并发执行，不阻塞事件循环 / 不阻塞下一个事件）
        for slug in affected:
            asyncio.create_task(self._recompile_page(slug, entity_id, event.action))

    # ── BFS 影响集合 ──

    def _compute_affected_pages(self, entity_id: str) -> list[str]:
        """BFS 计算受影响的 wiki 页面 slug 列表

        种子：实体名直接对应的 wiki 页面 + 通过 backlink 引用它的页面
        扩展：沿 backlink 链 BFS（哪些页面链接到受影响页面），深度≤MAX_BFS_DEPTH
        """
        self._refresh_slug_set_if_stale()
        seed = self._find_pages_for_entity(entity_id)
        if not seed:
            return []

        visited: set[str] = set()
        frontier: set[str] = seed
        result: list[str] = []

        for _depth in range(MAX_BFS_DEPTH + 1):
            if not frontier:
                break
            next_frontier: set[str] = set()
            for slug in frontier:
                if slug in visited:
                    continue
                visited.add(slug)
                if slug in self._wiki_slug_set:
                    result.append(slug)
                    if len(result) >= MAX_AFFECTED_PAGES:
                        return result
                # 沿 backlink 扩展（哪些页面链接到 slug）
                try:
                    for bl in get_backlinks(slug):
                        if bl.source_slug not in visited:
                            next_frontier.add(bl.source_slug)
                except Exception:  # noqa: BLE001
                    pass
            frontier = next_frontier

        return result

    def _find_pages_for_entity(self, entity_id: str) -> set[str]:
        """找到与实体直接关联的 wiki 页面 slug 集合（种子节点）

        匹配策略：
        1. 实体名 == slug（kebab-case 归一化）
        2. backlink：实体名作为 target_slug 的反向链接来源页面
        3. backlink：kebab 归一化后作为 target_slug 的反向链接来源页面
        """
        slugs: set[str] = set()
        kebab = _to_kebab(entity_id)

        # 1. 实体名 == slug
        if entity_id in self._wiki_slug_set:
            slugs.add(entity_id)
        if kebab != entity_id and kebab in self._wiki_slug_set:
            slugs.add(kebab)

        # 2/3. backlink 反向链接
        for target in {entity_id, kebab}:
            try:
                for bl in get_backlinks(target):
                    if bl.source_slug in self._wiki_slug_set:
                        slugs.add(bl.source_slug)
            except Exception:  # noqa: BLE001
                pass
        return slugs

    # ── slug 集合缓存 ──

    def _refresh_slug_set(self) -> None:
        """强制刷新 wiki slug 集合缓存"""
        try:
            pages = list_wiki_pages(limit=10000)
            self._wiki_slug_set = {p["slug"] for p in pages}
            self._slug_set_refreshed_at = time.time()
        except Exception as e:  # noqa: BLE001
            logger.warning("graph_wiki_sync.refresh_slug_set_failed", error=str(e))

    def _refresh_slug_set_if_stale(self) -> None:
        """缓存超过 60s 则刷新"""
        if time.time() - self._slug_set_refreshed_at > 60.0 or not self._wiki_slug_set:
            self._refresh_slug_set()

    # ── 异步重编译（带乐观锁）──

    async def _recompile_page(self, slug: str, entity_id: str, action: str) -> None:
        """重编译单个 wiki 页面

        乐观锁：检查 _in_progress，已存在则跳过（避免并发重编译同一页面）。
        锁带 TTL，防止异常退出导致僵尸锁。
        """
        # 乐观锁：检查是否已在重编译
        async with self._lock:
            now = time.time()
            # 清理过期锁
            expired = [s for s, t in self._in_progress.items() if now - t > RECOMPILE_LOCK_TTL]
            for s in expired:
                self._in_progress.pop(s, None)
            if slug in self._in_progress:
                logger.info("graph_wiki_sync.skip_locked", slug=slug)
                return
            self._in_progress[slug] = now

        try:
            doc_id = self._get_source_doc_id(slug)
            if not doc_id:
                logger.info("graph_wiki_sync.no_source_doc", slug=slug)
                return

            from app.knowledge.wiki_compiler import get_wiki_compiler

            compiler = get_wiki_compiler()
            result = await compiler.recompile_section(doc_id, slug)
            if "error" in result:
                logger.warning(
                    "graph_wiki_sync.recompile_failed",
                    slug=slug,
                    error=result["error"],
                )
                return
            # 清除 stale 标记
            try:
                clear_stale(slug)
            except Exception as e:  # noqa: BLE001
                logger.warning("graph_wiki_sync.clear_stale_failed", slug=slug, error=str(e))
            logger.info(
                "graph_wiki_sync.recompiled",
                slug=slug,
                entity=entity_id,
                action=action,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("graph_wiki_sync.recompile_error", slug=slug, error=str(e))
        finally:
            async with self._lock:
                self._in_progress.pop(slug, None)

    def _get_source_doc_id(self, slug: str) -> str | None:
        """从 wiki 页面 frontmatter 提取首个 source doc_id"""
        try:
            vc = get_version_control()
            latest = vc.get_latest(_key_from_slug(slug))
            if not latest:
                return None
            content = latest.get("content", "")
            if content.startswith("---"):
                end = content.find("---", 3)
                if end > 0:
                    meta = yaml.safe_load(content[3:end]) or {}
                    sources = meta.get("sources") or []
                    if sources and isinstance(sources, list):
                        first = sources[0]
                        if isinstance(first, dict):
                            return first.get("doc_id") or None
        except Exception:  # noqa: BLE001
            pass
        return None


def _to_kebab(name: str) -> str:
    """实体名归一化为 kebab-case slug（与 wiki_index 命名约定一致）"""
    return re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", name).strip("-").lower()


# ────────── 全局单例 ──────────

_sync: GraphWikiSync | None = None


def get_graph_wiki_sync() -> GraphWikiSync:
    """获取全局 GraphWikiSync 单例"""
    global _sync
    if _sync is None:
        _sync = GraphWikiSync()
    return _sync
