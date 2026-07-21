"""Wiki 编译器（P0-4）— LLM-as-Compiler

实现 Karpathy LLM Wiki 范式的"知识编译"核心：把 raw 文档（L1）编译为
结构化 Markdown Wiki 页面（L2），每页遵循 AGENTS.md 定义骨架，并自动建立
[[wikilink]] 双向链接。

核心职责（与 RAG 的关键区别）：
- 编译一次，持续保持最新（不每次检索原文）
- 增量合并：raw 更新 → 合并新事实 → 标注 stale 项 → 触发重编译
- 输出物是 wiki 页面（持久化），不是临时检索片段

输入：doc_id（来自 DocumentStore）
输出：list[WikiPage]，已写回 VersionControl（doc_key = wiki:{slug}）

调用关系：
    DocumentStore (raw)  ──┐
    KnowledgeExtractor   ──┼──► WikiCompiler ──► VersionControl (wiki:*)
    GraphStore (可选辅助) ──┘                   └─► update_backlinks / rebuild_index
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import structlog
import yaml

from app.config import get_settings
from app.core.llm import ChatMessage, get_llm_client
from app.extraction import KnowledgeExtractor
from app.extraction.types import ExtractedEntity, ExtractionResult
from app.knowledge.wiki_compiler_types import (
    _MAX_LLM_RETRIES,
    _QUALITY_REVIEW_THRESHOLD,
    _REQUIRED_SECTIONS,
    _RETRY_BASE_DELAY,
    _TEMPLATE_PLACEHOLDER_RE,
    ENTITY_TYPE_TO_PAGE_TYPE,
    PipelineTrace,
    ProgressCallback,
    ProgressEventType,
    SectionTrace,
    WikiCompileResult,
    WikiPage,
)
from app.knowledge.wiki_compiler_utils import (
    _CJK_RE,
    _cosine_similarity,
    _entity_to_wiki_slugs,
    _parse_json_response,
    _slugify,
    _tokenize,
    generate_slug_for_heading_tree,
    iter_tree_nodes,
    make_hierarchical_slug,  # noqa: F401  # re-exported for external consumers
    make_slug,
)
from app.knowledge.wiki_drift import clear_stale, record_compiled_checksum
from app.knowledge.wiki_index import _key_from_slug, list_wiki_pages, rebuild_index
from app.knowledge.wikilink import WIKILINK_RE, update_backlinks
from app.observability import record_business_histogram, record_business_metric, span
from app.parsers import get_parser
from app.parsers.base import ParsedDocument
from app.storage import get_document_store
from app.storage.version_control import get_version_control

logger = structlog.get_logger()

# ────────── 编译器主体 ──────────


class WikiCompiler:
    """把 raw 文档编译为 wiki 页面

    使用方式：
        compiler = get_wiki_compiler()
        result = await compiler.compile_raw_to_wiki(doc_id)
    """

    # P1: 类级别持久化 LLM 缓存，跨编译复用
    _persistent_llm_cache: dict[str, str] = {}
    _CACHE_MAX_SIZE = 500  # 最多缓存 500 条

    def __init__(self) -> None:
        self.llm = get_llm_client()
        self.settings = get_settings()
        self.extractor = KnowledgeExtractor()
        self.vc = get_version_control()
        self.store = get_document_store()
        self._llm_cache = WikiCompiler._persistent_llm_cache  # P1: 引用类级别持久化缓存

    # ── LLM 包装 ──

    @staticmethod
    def _get_llm_cache_key(*args: str) -> str:
        """计算输入参数的 SHA256 哈希，作为 LLM 缓存键"""
        combined = "||".join(args)
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    async def _llm_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        """统一 LLM 调用入口（S3: 带重试机制，最多 3 次）

        使用 LLMConcurrencyController 全局限流，防止本地部署过载。
        """
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt))

        # P2-1: LLM 并发控制
        from app.core.llm.concurrency import TaskPriority, get_llm_concurrency_controller

        controller = get_llm_concurrency_controller()
        for attempt in range(1, _MAX_LLM_RETRIES + 1):
            try:
                async with controller.acquire(
                    stage="section_compile",
                    priority=TaskPriority.MEDIUM,
                ):
                    resp = await self.llm.chat(
                        messages=messages,
                        temperature=temperature,
                        max_tokens=self.settings.llm_max_tokens,
                    )
                # ── 编译指标埋点：LLM 调用成功 ──
                try:
                    record_business_metric("llm_calls_total", backend=self.settings.llm_backend, status="success")
                except Exception:  # noqa: BLE001
                    pass
                return resp.text or ""
            except Exception as e:
                _ = str(e)  # 记录最后一次错误
                if attempt < _MAX_LLM_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "wiki_compiler_llm_retry",
                        attempt=attempt,
                        max_retries=_MAX_LLM_RETRIES,
                        delay=delay,
                        error=str(e),
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning(
                        "wiki_compiler_llm_failed",
                        error=str(e),
                        attempts=attempt,
                    )
        # ── 编译指标埋点：LLM 调用失败（所有重试耗尽）──
        try:
            record_business_metric("llm_calls_total", backend=self.settings.llm_backend, status="error")
        except Exception:  # noqa: BLE001
            pass
        return ""

    # ── S2: 页面质量校验 ──

    @staticmethod
    def _validate_page_quality(body_md: str, page_type: str) -> dict:
        """校验 wiki 页面质量，返回校验结果

        Returns:
            {"valid": bool, "issues": [str], "score": float}
            score: 1.0 = 完美, 0.0 = 完全不合格
        """
        issues: list[str] = []
        checks_passed = 0
        checks_total = 0

        # 1. 必含章节检查
        required = _REQUIRED_SECTIONS.get(page_type, ["概述", "来源"])
        checks_total += len(required)
        for sec in required:
            if re.search(rf"^##\s+{sec}", body_md, re.MULTILINE):
                checks_passed += 1
            else:
                issues.append(f"缺少必含章节：{sec}")

        # 2. 模板兜底标记检查
        checks_total += 1
        placeholders = _TEMPLATE_PLACEHOLDER_RE.findall(body_md)
        if placeholders:
            issues.append(f"含 {len(placeholders)} 处模板兜底标记")
        else:
            checks_passed += 1

        # 3. 内容长度检查（至少含 100 字符正文）
        checks_total += 1
        if len(body_md.strip()) >= 100:
            checks_passed += 1
        else:
            issues.append("正文内容过短（<100 字符）")

        score = checks_passed / max(checks_total, 1)
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "score": round(score, 2),
        }

    # ── 主入口 ──

    async def compile_from_sections(
        self,
        compiled_sections: list[Any],  # list[CompiledSection]
        *,
        doc_id: str = "",
        force: bool = False,
        rebuild_index_after: bool = True,
        on_progress: ProgressCallback | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> WikiCompileResult:
        """从编译后的章节合成 Wiki 页面（新架构入口）

        将多个 CompiledSection 合成为一个 Wiki 页面。
        这是新架构的核心入口，替代了旧的从 raw doc → wiki 的流程。

        流程:
            1. 按 semantic_role 分组章节
            2. 确定 Wiki 页面类型和 slug
            3. LLM 合成 Wiki 页面（或模板兜底）
            4. 合并已有页面
            5. 保存 + 回链 + 图同步

        Args:
            compiled_sections: 编译后的章节列表
            doc_id: 来源文档 ID
            force: 强制重编译
            rebuild_index_after: 编译后是否重建索引
            on_progress: 进度回调
            is_cancelled: 中断检查回调

        Returns:
            WikiCompileResult
        """
        result = WikiCompileResult(doc_id=doc_id or compiled_sections[0].source_doc_id if compiled_sections else '')

        if not compiled_sections:
            result.errors.append("no compiled sections provided")
            return result

        await self._report_progress(on_progress, "wiki_synthesizing", "正在合成 Wiki 页面...")

        # 1. 按 semantic_role 分组
        role_groups = self._group_sections_by_role(compiled_sections)

        # 2. 确定 Wiki 页面类型
        page_type = self._determine_page_type(role_groups)
        slug = self._determine_slug(compiled_sections, page_type)

        # 3. LLM 合成
        body = await self._synthesize_wiki_body(
            role_groups, page_type, slug, compiled_sections,
        )

        # 4. 质量校验
        validation = self._validate_page_quality(body, page_type)
        if not validation.get('valid', False):
            result.warnings.append(f'质量校验未通过: {validation.get("issues", [])}')

        # 5. 构建 frontmatter
        title = self._extract_title_from_sections(compiled_sections)
        tags = self._extract_tags_from_sections(compiled_sections)
        sources = self._build_sources_from_sections(compiled_sections, doc_id)

        page_md = self._render_wiki_page(
            slug=slug,
            title=title,
            page_type=page_type,
            tags=tags,
            sources=sources,
            body_md=body,
            review_status='auto',
        )

        # 6. 保存页面
        page_result = await self._save_page(
            slug=slug,
            page_md=page_md,
            page_type=page_type,
            force=force,
            compiled_sections=compiled_sections,
        )

        if page_result == 'created':
            result.pages_created = 1
        elif page_result == 'updated':
            result.pages_updated = 1
        else:
            result.pages_unchanged = 1

        result.slugs = [slug]

        # 7. 重建索引
        if rebuild_index_after and page_result != 'unchanged':
            await self._report_progress(on_progress, "indexing", "正在重建搜索索引...")
            try:
                from app.search.search_engine import get_search_engine
                se = get_search_engine()
                se.rebuild_index()
            except Exception as e:
                result.warnings.append(f'索引重建失败: {e}')

        return result

    async def compile_raw_to_wiki(
        self,
        doc_id: str,
        *,
        force: bool = False,
        rebuild_index_after: bool = True,
        also_compile_graph: bool = False,
        # M3: 进度回调
        on_progress: ProgressCallback | None = None,
        # L1: 中断检查
        is_cancelled: Callable[[], bool] | None = None,
        # L1: 任务状态
        task_state: dict | None = None,
    ) -> WikiCompileResult:
        """把一份 raw 文档编译为 wiki 页面

        流程（AGENTS.md §五）：
            1. 读取 raw 文档 → ParsedDocument
            2. LLM 抽取实体/概念
            3. 对每个实体/概念生成 wiki 页面（合并/新建）
            4. 写回 VersionControl（doc_key=wiki:{slug}）
            5. 更新 backlink
            6. 重建 index.md

        Args:
            doc_id: DocumentStore 中的文档 ID
            force: 强制重编译（即使内容未变）
            rebuild_index_after: 编译后是否重建 index.md
            also_compile_graph: P3-4 统一编译 — 同时写入知识图谱（Neo4j）
            on_progress: M3 SSE 进度回调
            is_cancelled: L1 中断检查回调
            task_state: L1 任务状态字典（用于断点恢复）
        """
        result = WikiCompileResult(doc_id=doc_id)

        # ── 编译指标埋点：记录编译开始 ──
        try:
            from app.observability.metrics import record_business_metric  # noqa: F811
            record_business_metric("compile_total", compile_type="full")
        except Exception:  # noqa: BLE001
            pass
        _t_compile_start = time.monotonic()

        # P1: 仅强制重编译时清空缓存，普通编译复用持久化缓存
        if force:
            self._llm_cache.clear()

        # L1: 初始化任务状态
        if task_state is not None:
            task_state["status"] = "running"
            task_state["started_at"] = datetime.now(timezone.utc).isoformat()
            task_state["steps_completed"] = task_state.get("steps_completed", [])
            task_state["last_entity_idx"] = task_state.get("last_entity_idx", -1)

        # M3: 进度回调包装
        def _emit(etype: ProgressEventType, data: dict[str, Any]) -> None:
            if on_progress:
                try:
                    on_progress(etype, data)
                except Exception:
                    pass

        # L1: 中断检查包装
        def _check_cancel() -> bool:
            if is_cancelled and is_cancelled():
                if task_state is not None:
                    task_state["status"] = "cancelled"
                return True
            return False

        # S15-1c: 知识编译 span 埋点，覆盖整个编译流程
        with span("wiki.compile", doc_id=doc_id) as _sp:
            # 1. 读取 raw
            meta = self.store.get(doc_id)
            if not meta:
                result.errors.append(f"文档不存在: {doc_id}")
                return result

            # 设置 format 属性（span 对象可能为 None，需容错）
            try:
                if _sp is not None:
                    _sp.set_attribute("format", meta.get("format", ""))
            except Exception:  # noqa: BLE001
                pass

            raw_bytes = self.store.read_content(doc_id)
            if not raw_bytes:
                result.errors.append(f"原始文件读取失败: {doc_id}")
                return result

            # 2. 解析 + 抽取
            _emit(ProgressEventType.STEP_START, {"step": "parse", "message": "开始解析文档..."})
            try:
                parser = get_parser(meta["format"])
                with span(
                    "document.parse",
                    doc_id=doc_id,
                    format=meta.get("format", ""),
                ):
                    doc = parser.parse(meta["stored_path"], doc_id)
            except Exception as e:
                result.errors.append(f"解析失败: {e}")
                _emit(ProgressEventType.STEP_DONE, {"step": "parse", "error": str(e)})
                return result
            _emit(ProgressEventType.STEP_DONE, {"step": "parse", "elements": len(doc.elements), "heading_tree_count": len(doc.heading_tree)})
            if _check_cancel():
                _emit(ProgressEventType.STEP_DONE, {"step": "cancelled", "message": "编译已取消"})
                return result

            _emit(ProgressEventType.STEP_START, {"step": "extract", "message": "开始知识抽取..."})
            try:
                extraction = await self.extractor.extract(doc)
            except Exception as e:
                result.errors.append(f"抽取失败: {e}")
                _emit(ProgressEventType.STEP_DONE, {"step": "extract", "error": str(e)})
                return result
            _emit(ProgressEventType.STEP_DONE, {"step": "extract", "entities": len(extraction.auto_accepted_entities) + len(extraction.review_entities)})
            if _check_cancel():
                _emit(ProgressEventType.STEP_DONE, {"step": "cancelled", "message": "编译已取消"})
                return result

            # S1: 段落级 LLM 归类
            _emit(ProgressEventType.STEP_START, {"step": "classify", "message": "段落分类中..."})
            paragraph_classifications: list[dict] = []
            try:
                paragraph_classifications = await self.extractor.classify_paragraphs(doc)
                result.paragraph_count = len(paragraph_classifications)
                logger.info(
                    "paragraph_classification_integrated",
                    doc_id=doc_id,
                    count=result.paragraph_count,
                )
            except Exception as e:
                logger.warning(
                    "paragraph_classification_failed",
                    doc_id=doc_id,
                    error=str(e),
                )
                # 非致命错误，继续编译流程

            # 构建段落标签映射：段落索引 → 层级标签列表
            para_labels_map: dict[int, list[str]] = {}
            # S1: 收集所有段落分类的唯一标签（文档级标签池）
            doc_labels: list[str] = []
            for pc in paragraph_classifications:
                idx = pc.get("index")
                label = pc.get("label", "")
                if idx is not None and label:
                    levels = [lvl.strip() for lvl in label.split(">") if lvl.strip()]
                    para_labels_map[idx] = levels
                    for lvl in levels:
                        if lvl not in doc_labels:
                            doc_labels.append(lvl)

            entities = list(extraction.auto_accepted_entities) + list(
                extraction.review_entities
            )
            if not entities:
                logger.info("wiki_compiler_no_entities", doc_id=doc_id)
                # 无实体也更新状态
                self.store.update_status(doc_id, "compiled")
                return result

            # P3-4: 统一编译 — 复用已有 extraction 写入知识图谱（避免重复 parse+extract）
            if also_compile_graph:
                try:
                    self._compile_to_graph(doc_id, extraction)
                    result.graph_compiled = True
                except Exception as e:
                    result.errors.append(f"图谱编译失败: {e}")
                    # P0-3: 记录图谱编译失败指标
                    try:
                        from app.observability.metrics import record_business_metric
                        record_business_metric("graph_sync_failures_total", 1.0, operation="compile_to_graph")
                    except Exception:
                        pass

            # 3. 逐个编译（实体抽取方式）
            source_entry = {
                "doc_id": doc_id,
                "title": meta.get("title") or meta.get("filename", doc_id),
                "checksum": meta.get("checksum", ""),
            }

            _emit(ProgressEventType.STEP_START, {"step": "compile", "total": len(entities)})
            total_entities = len(entities)

            # P1 (K4): 预取所有实体的图谱关系，避免逐实体查询
            entity_names = [e.name for e in entities]
            relations_map = self._fetch_graph_relations_batch(entity_names)

            # P0-1: 实体编译并行化 — Phase 1: 并行执行 LLM 调用（信号量限制并发数）
            max_concurrency = max(getattr(self.settings, 'compile_concurrency', 3), 1)
            sem = asyncio.Semaphore(max_concurrency)

            async def _compile_page_parallel(
                idx: int, entity: ExtractedEntity,
            ) -> tuple[int, ExtractedEntity, WikiPage | None, Exception | None]:
                async with sem:
                    if _check_cancel():
                        return (idx, entity, None, None)
                    # L1: 断点恢复 — 跳过已处理的实体
                    if task_state is not None and idx <= task_state.get("last_entity_idx", -1):
                        return (idx, entity, None, None)
                    try:
                        _emit(ProgressEventType.PAGE_START, {
                            "entity": entity.name,
                            "index": idx + 1,
                            "total": total_entities,
                        })
                        _emit(ProgressEventType.PROGRESS, {
                            "percent": round((idx + 1) / max(total_entities, 1) * 100),
                            "current": idx + 1,
                            "total": total_entities,
                        })
                        page = await self._compile_entity_page(
                            entity, source_entry, para_labels=doc_labels,
                            relations_map=relations_map,
                        )
                        return (idx, entity, page, None)
                    except Exception as e:
                        return (idx, entity, None, e)

            tasks = [_compile_page_parallel(i, entity) for i, entity in enumerate(entities)]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)

            # P0-1: Phase 2 — 串行保存和后续处理（SQLite 写入安全）
            for item in results_list:
                if item is None:
                    continue
                if isinstance(item, Exception):
                    result.errors.append(f"gather_failed: {item}")
                    continue

                idx, entity, page, error = item
                if error:
                    logger.exception("wiki_compiler_page_failed", slug=entity.name)
                    result.errors.append(f"{entity.name}: {error}")
                    continue
                if page is None:
                    continue

                if _check_cancel():
                    if task_state is not None:
                        task_state["last_entity_idx"] = idx
                        task_state["steps_completed"].append("compile_partial")
                    _emit(ProgressEventType.STEP_DONE, {"step": "cancelled", "message": f"编译已取消（已完成 {idx}/{total_entities}）"})
                    return result

                try:
                    outcome = self._save_page(page, force=force)
                    # GS-6: 双向同步 — 页面保存后同步更新图谱
                    self._sync_page_to_graph(page)
                    result.slugs.append(page.slug)
                    if outcome == "created":
                        result.pages_created += 1
                    elif outcome == "updated":
                        result.pages_updated += 1
                        if page.stale_items:
                            result.stale_marked.append(page.slug)
                    else:
                        result.pages_unchanged += 1
                    # S2 + L3: 质量校验 — 根据阈值决定状态
                    if page.review_status != "review_needed":
                        quality = self._validate_page_quality(page.body_md, page.type)
                        if not quality["valid"]:
                            page.review_status = "review_needed"
                            logger.info(
                                "wiki_page_quality_fail",
                                slug=page.slug,
                                issues=quality["issues"],
                                score=quality["score"],
                            )
                        # L3: 质量阈值控制
                        if quality["score"] < _QUALITY_REVIEW_THRESHOLD:
                            page.review_status = "review_needed"
                            logger.warning(
                                "wiki_page_quality_rejected",
                                slug=page.slug,
                                score=quality["score"],
                                threshold=_QUALITY_REVIEW_THRESHOLD,
                            )
                    if page.review_status == "review_needed":
                        result.review_needed.append(page.slug)
                    _emit(ProgressEventType.PAGE_DONE, {
                        "entity": entity.name,
                        "slug": page.slug,
                        "outcome": outcome,
                        "review_status": page.review_status,
                    })
                    # M2: 冲突检测 — 合并后检测语义冲突
                    if outcome == "updated":
                        try:
                            # 获取旧版本内容用于冲突检测
                            doc_key = _key_from_slug(page.slug)
                            old_version = self.vc.get_latest(doc_key)
                            if old_version:
                                conflicts = await self._detect_conflicts_with_llm(
                                    old_version["content"], page.body_md
                                )
                                if conflicts:
                                    page.stale_items.extend(conflicts)
                                    if page.slug not in result.stale_marked:
                                        result.stale_marked.append(page.slug)
                                    logger.info(
                                        "wiki_conflict_detected",
                                        slug=page.slug,
                                        conflict_count=len(conflicts),
                                    )
                        except Exception as e:
                            logger.warning(
                                "wiki_conflict_detection_failed",
                                slug=page.slug,
                                error=str(e),
                            )
                except Exception as e:
                    logger.exception("wiki_compiler_page_failed", slug=entity.name)
                    result.errors.append(f"{entity.name}: {e}")

            # 4. 结构编译（基于标题层级树）
            if doc.heading_tree:
                _emit(ProgressEventType.STEP_START, {"step": "struct_compile", "message": f"开始结构编译，共 {len(doc.heading_tree)} 个章节..."})
                try:
                    struct_result = await self._compile_heading_tree_to_wiki(
                        doc, source_entry, force=force, on_progress=_emit
                    )
                    result.pages_created += struct_result.pages_created
                    result.pages_updated += struct_result.pages_updated
                    result.pages_unchanged += struct_result.pages_unchanged
                    result.slugs.extend(struct_result.slugs)
                    result.review_needed.extend(struct_result.review_needed)
                    result.stale_marked.extend(struct_result.stale_marked)
                    result.errors.extend(struct_result.errors)
                    result.pipeline_trace = struct_result.pipeline_trace
                    _emit(ProgressEventType.STEP_DONE, {"step": "struct_compile", "sections": len(doc.heading_tree), "pages_created": struct_result.pages_created, "pages_updated": struct_result.pages_updated})
                except Exception as e:
                    logger.exception("wiki_compiler_struct_failed", doc_id=doc_id)
                    result.errors.append(f"结构编译失败: {e}")
                    _emit(ProgressEventType.STEP_DONE, {"step": "struct_compile", "error": str(e)})

            # 4. 状态推进
            self.store.update_status(doc_id, "compiled")

            # 5. 记录编译时 checksum（供 P1-1 漂移检测使用），清除已重编译页面的 stale
            try:
                record_compiled_checksum(doc_id, meta.get("checksum", ""))
                for slug in result.slugs:
                    clear_stale(slug)
            except Exception as e:
                result.errors.append(f"checksum/stale 同步失败: {e}")

            # 6. 重建 index
            if rebuild_index_after and result.pages_created + result.pages_updated > 0:
                try:
                    rebuild_index()
                    result.index_rebuilt = True
                except Exception as e:
                    result.errors.append(f"index 重建失败: {e}")

            # 6b. 同步搜索 FTS5 索引（B2 修复）
            if result.pages_created + result.pages_updated > 0:
                try:
                    from app.search.search_engine import get_search_engine
                    se = get_search_engine()
                    se.rebuild_index()
                    logger.info("search_index_rebuilt", pages=result.pages_created + result.pages_updated)
                except Exception as e:
                    logger.warning("search_index_rebuild_failed", error=str(e))

            # 设置 page_count 属性（编译完成后）
            try:
                if _sp is not None:
                    _sp.set_attribute(
                        "page_count",
                        result.pages_created
                        + result.pages_updated
                        + result.pages_unchanged,
                    )
            except Exception:  # noqa: BLE001
                pass

            logger.info(
                "wiki_compiled",
                doc_id=doc_id,
                created=result.pages_created,
                updated=result.pages_updated,
                unchanged=result.pages_unchanged,
                errors=len(result.errors),
            )
            # L1: 任务状态完成
            if task_state is not None:
                task_state["status"] = "completed"
                task_state["completed_at"] = datetime.now(timezone.utc).isoformat()
                task_state["steps_completed"].append("done")
            # M3: 编译完成事件
            _emit(ProgressEventType.STEP_DONE, {
                "step": "compile",
                "pages_created": result.pages_created,
                "pages_updated": result.pages_updated,
                "pages_unchanged": result.pages_unchanged,
                "review_needed": len(result.review_needed),
                "errors": len(result.errors),
            })

            # ── 编译指标埋点：记录编译耗时与统计 ──
            try:
                _elapsed = time.monotonic() - _t_compile_start
                record_business_histogram("compile_duration_seconds", _elapsed, compile_type="full")
                record_business_metric("compile_sections_total", float(len(entities)), compile_type="full")
                record_business_metric("compile_sections_error_total", float(len(result.errors)), compile_type="full")
                record_business_metric("wiki_pages_created_total", float(result.pages_created))
                record_business_metric("wiki_pages_updated_total", float(result.pages_updated))
            except Exception:  # noqa: BLE001
                pass

            return result

    # ── P3-4: 统一编译 ──

    async def compile_raw_to_all(
        self,
        doc_id: str,
        *,
        force: bool = False,
        rebuild_index_after: bool = True,
    ) -> WikiCompileResult:
        """P3-4: 统一编译 — 一次调用同时编译 wiki 页面 + 知识图谱

        对齐审计报告 P3-4: 合并 compiler.py 与 wiki_compiler.py 编排，
        消除 /graph/upload 与 /llm-wiki/ingest 的重复 parse+extract。

        流程：
            1. parse + extract（只做一次）
            2. 写入知识图谱（KnowledgeCompiler.compile_and_store → Neo4j）
            3. 生成 wiki 页面（LLM 编译 → VersionControl）
            4. 返回统一结果（graph_compiled 标记图谱写入状态）

        GraphStore 不可用时优雅降级（graph_compiled=False，errors 记录原因）。
        """
        return await self.compile_raw_to_wiki(
            doc_id,
            force=force,
            rebuild_index_after=rebuild_index_after,
            also_compile_graph=True,
        )

    async def recompile_section(
        self,
        doc_id: str,
        slug: str,
        *,
        temperature: float | None = None,
        system_prompt: str | None = None,
        user_prompt: str | None = None,
    ) -> dict:
        """重新编译单个章节并保存为 wiki 页面

        Args:
            doc_id: 原始文档 ID
            slug: 章节 slug
            temperature: 自定义 LLM temperature（None 使用默认 0.2）
            system_prompt: 自定义系统提示词（None 使用默认）
            user_prompt: 自定义用户提示词（None 使用默认）

        Returns:
            { slug, compiled_content, raw_chars, compiled_chars, outcome }
        """
        # ── 编译指标埋点：增量编译开始 ──
        try:
            record_business_metric("compile_total", compile_type="incremental")
        except Exception:  # noqa: BLE001
            pass
        _t_compile_start = time.monotonic()

        # P1: recompile_section 总是强制重编译，清空缓存以获取最新结果
        self._llm_cache.clear()

        # 1. 加载原始文档
        meta = self.store.get(doc_id)
        if not meta:
            return {"error": f"文档不存在: {doc_id}"}

        raw_bytes = self.store.read_content(doc_id)
        if not raw_bytes:
            return {"error": "原始文件读取失败"}

        # 2. 解析文档
        try:
            parser = get_parser(meta.get("format", "markdown"))
            doc = parser.parse(meta.get("stored_path", ""), doc_id)
        except Exception as e:
            return {"error": f"解析失败: {e}"}

        if not doc or not doc.heading_tree:
            return {"error": "文档无法解析或标题树为空"}

        # 3. 从 heading_tree 中查找 slug 对应的节点
        slug_tree = generate_slug_for_heading_tree(doc.get_heading_tree_dict())
        node = self._find_node_by_slug(slug_tree, slug)
        if not node:
            return {"error": f"未找到章节: {slug}"}

        raw_content = self._render_elements_to_text(node.get("elements", []))
        raw_chars = len(raw_content)

        parent_slug = None
        parent_node = self._find_parent_node(slug_tree, slug, node)
        if parent_node:
            parent_slug = parent_node.get("slug")

        # 4. 使用自定义或默认参数编译
        try:
            if user_prompt or system_prompt:
                # 自定义 prompt 绕过 _llm_compile_section，直接调用 _llm_complete
                compiled = await self._llm_complete(
                    user_prompt or f"请编译以下章节：\n{raw_content[:4000]}",
                    system=system_prompt or "",
                    temperature=temperature if temperature is not None else 0.2,
                )
                compiled = self._strip_codefence(compiled).strip()
            else:
                compiled = await self._llm_compile_section(node, parent_slug)
                if temperature is not None:
                    # 仅 temperature 覆盖：重新调用 _llm_complete
                    compiled = await self._llm_complete(
                        user_prompt=self._build_section_prompt(node, parent_slug),
                        system=self._SECTION_SYSTEM_PROMPT,
                        temperature=temperature,
                    )
                    compiled = self._strip_codefence(compiled).strip()
        except Exception as e:
            logger.error("recompile_section_llm_error", slug=slug, error=str(e))
            return {"error": f"LLM 编译失败: {e}"}

        compiled_chars = len(compiled)

        # 5. 保存为 wiki 页面
        page_type = "concept"
        if slug.startswith("runbook-"):
            page_type = "runbook"
        elif slug.startswith("incident-"):
            page_type = "incident"

        page = WikiPage(
            slug=slug,
            title=node.get("title", slug),
            type=page_type,
            tags=[],
            sources=[{"doc_id": doc_id, "title": meta.get("filename", doc_id), "checksum": ""}],
            body_md=compiled,
            review_status="auto",
            source_doc_id=doc_id,
        )
        outcome = self._save_page(page, force=True)

        # ── 编译指标埋点：增量编译耗时 ──
        try:
            _elapsed = time.monotonic() - _t_compile_start
            record_business_histogram("compile_duration_seconds", _elapsed, compile_type="incremental")
        except Exception:  # noqa: BLE001
            pass

        return {
            "slug": slug,
            "compiled_content": compiled,
            "raw_chars": raw_chars,
            "compiled_chars": compiled_chars,
            "outcome": outcome,
        }

    @staticmethod
    def _find_node_by_slug(tree: list[dict], slug: str) -> dict | None:
        """递归在标题树中查找 slug 匹配的节点"""
        for node in tree:
            if node.get("slug") == slug:
                return node
            if node.get("children"):
                found = WikiCompiler._find_node_by_slug(node["children"], slug)
                if found:
                    return found
        return None

    @staticmethod
    def _find_parent_node(tree: list[dict], slug: str, target: dict) -> dict | None:
        """查找目标节点的父节点"""
        for node in tree:
            if node.get("children"):
                for child in node["children"]:
                    if child.get("slug") == slug:
                        return node
                found = WikiCompiler._find_parent_node(node["children"], slug, target)
                if found:
                    return found
        return None

    _SECTION_SYSTEM_PROMPT = """你是 OpsKG Wiki 管理员。把文档章节编译为结构化 Markdown wiki 页面。

严格遵循 AGENTS.md 规定的页面骨架。使用 [[slug]] 双向链接到相关概念。

页面类型：概念页（concept）
必含章节：概述、原理、应用场景、来源

注意：
1. 只输出 Markdown 正文，不要 YAML frontmatter，不要 ```md 包裹
2. 在首次提及相关概念/服务/主机时，用 [[kebab-case-slug]] 形式建链
3. 不要编造未在原文中出现的具体数值
4. 保留原文的表格和代码块格式
5. 使用合适的标题层级（从 ## 开始）"""

    def _build_section_prompt(self, node: dict, parent_slug: str | None = None) -> str:
        """构建章节编译 prompt"""
        title = node.get("title", "")
        level = node.get("level", 1)
        elements = node.get("elements", [])
        content_text = self._render_elements_to_text(elements)

        children_info = []
        for child in node.get("children", []):
            child_slug = child.get("slug")
            child_title = child.get("title", "")
            if child_slug:
                children_info.append(f"- [[{child_slug}|{child_title}]]")
            else:
                children_info.append(f"- {child_title}")

        parent_info = f"父级章节：[[{parent_slug}]]" if parent_slug else ""

        return f"""请把以下文档章节编译为一个 wiki 页面。

# 章节标题
{title}

# 章节层级
H{level}

# 父级章节
{parent_info}

# 子章节
{chr(10).join(children_info) if children_info else "（无）"}

# 原文内容
{content_text[:4000]}

# 编译要求
1. 严格按概念页骨架输出 Markdown 章节（## 概述、## 原理、## 应用场景、## 来源）
2. 在首次提及相关概念时，用 [[kebab-case-slug]] 形式建链
3. 保留原文中的表格和代码块
4. 「## 来源」章节引用本页来源即可
5. 标题用 `# {title}` 起首"""

    @staticmethod
    def _compile_to_graph(doc_id: str, extraction: ExtractionResult) -> None:
        """P3-4: 把抽取结果写入知识图谱（复用已有 extraction，避免重复 parse+extract）

        将 ExtractedEntity/ExtractedRelation 转换为 GraphEntity/GraphRelation，
        调用 KnowledgeCompiler.compile_and_store 写入 Neo4j。

        GraphStore 不可用时抛异常（由调用方捕获降级）。
        """
        from app.knowledge.compiler import get_compiler
        from app.knowledge.graph_store import GraphEntity, GraphRelation

        all_entities = list(extraction.auto_accepted_entities) + list(
            extraction.review_entities
        )
        all_relations = list(extraction.auto_accepted_relations) + list(
            extraction.review_relations
        )

        graph_entities = [
            GraphEntity(
                entity_type=e.entity_type,
                name=e.name,
                properties=e.properties,
                source_doc_id=doc_id,
                confidence=e.confidence,
            )
            for e in all_entities
        ]
        graph_relations = [
            GraphRelation(
                relation_type=r.relation_type,
                from_entity=r.from_entity,
                to_entity=r.to_entity,
                properties=r.properties,
                source_doc_id=doc_id,
                confidence=r.confidence,
            )
            for r in all_relations
        ]

        compiler = get_compiler()
        compiler.compile_and_store(graph_entities, graph_relations)

    # ── 单实体编译 ──

    async def _compile_entity_page(
        self,
        entity: ExtractedEntity,
        source_entry: dict,
        para_labels: list[str] | None = None,
        relations_map: dict[str, str] | None = None,
    ) -> WikiPage | None:
        """把单个实体编译为 wiki 页面

        - 用 LLM 生成正文（按 AGENTS.md 骨架）
        - LLM 不可用时退化为模板化正文（基于 evidence_span）
        - S1: 段落分类标签作为页面标签
        - relations_map: P1 (K4) 预取的关系映射
        """
        slug = make_slug(entity.entity_type, entity.name)
        page_type = ENTITY_TYPE_TO_PAGE_TYPE.get(entity.entity_type, "concept")
        title = entity.name

        # 标签：实体类型 + properties 中的关键字段 + S1: 段落分类标签
        tags = [entity.entity_type.lower()]
        for k in ("category", "service", "host", "env", "level"):
            v = entity.properties.get(k)
            if isinstance(v, str) and v:
                tags.append(_slugify(v))
        # S1: 合并段落分类标签（去重，取前 2 层标签）
        if para_labels:
            for label in para_labels:
                slugified = _slugify(label)
                if slugified and slugified not in tags:
                    tags.append(slugified)
            tags = tags[:8]  # 限制标签总数
        else:
            tags = tags[:5]

        # 调 LLM 写正文
        # P0: 增强 fallback — LLM 不可用时从源文档提取内容生成结构化 Wiki
        try:
            body_md = await self._llm_write_body(entity, page_type, relations_map=relations_map)
        except Exception:
            body_md = ""
        if not body_md:
            body_md = self._build_template_fallback(
                entity.entity_type,
                entity.name,
                entity.properties or {},
                source_content=(entity.evidence_span or "").strip(),
                paragraph_classifications=None,
            )

        review_status = (
            "review_needed"
            if entity.confidence < self.settings.confidence_review
            else "auto"
        )

        return WikiPage(
            slug=slug,
            title=title,
            type=page_type,
            tags=tags,
            sources=[source_entry],
            body_md=body_md,
            review_status=review_status,
            source_doc_id=source_entry.get("doc_id", ""),
            paragraph_labels=para_labels or [],
        )

    async def _compile_heading_tree_to_wiki(
        self,
        doc: ParsedDocument,
        source_entry: dict,
        *,
        force: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> WikiCompileResult:
        """基于标题层级树生成结构化 wiki 页面

        策略：
        - H1：文档主标题，生成概念页（concept）
        - H2：一级章节，生成概念页，slug 包含父级前缀
        - H3：二级章节，生成概念页或作为内容段落
        - H4-H6：深层章节，作为内容段落

        每个章节使用 LLM 生成结构化内容，遵循 AGENTS.md 骨架。

        Args:
            doc: ParsedDocument（含 heading_tree）
            source_entry: 来源信息
            force: 是否强制更新
            on_progress: 进度回调

        Returns:
            WikiCompileResult
        """
        result = WikiCompileResult(doc_id=doc.doc_id)

        heading_tree_dicts = doc.get_heading_tree_dict()
        if not heading_tree_dicts:
            return result

        tree_with_slugs = generate_slug_for_heading_tree(heading_tree_dicts)
        total_sections = sum(1 for _ in iter_tree_nodes(tree_with_slugs))

        t_start = time.monotonic()
        trace_buffer: list[SectionTrace] = []
        current_section = 0

        def _emit_section_progress(title: str, level: int, status: str) -> None:
            nonlocal current_section
            current_section += 1
            if on_progress:
                try:
                    on_progress(ProgressEventType.SECTION_PROGRESS, {
                        "title": title,
                        "level": level,
                        "status": status,
                        "current": current_section,
                        "total": total_sections,
                        "percent": round(current_section / max(total_sections, 1) * 100),
                    })
                except Exception:
                    pass

        page_count = await self._compile_tree_node_with_llm(
            tree_with_slugs,
            doc,
            source_entry,
            result,
            force=force,
            trace_buffer=trace_buffer,
            on_section_progress=_emit_section_progress,
            on_progress=on_progress,
            total_sections=total_sections,
        )
        duration_ms = (time.monotonic() - t_start) * 1000

        # 构建 PipelineTrace
        pt = PipelineTrace(
            doc_id=doc.doc_id,
            doc_title=getattr(doc, "title", None) or doc.doc_id,
            duration_ms=round(duration_ms, 1),
            sections=trace_buffer,
        )
        pt.total_sections = len(trace_buffer)
        pt.total_raw_chars = sum(s.raw_chars for s in trace_buffer)
        pt.total_compiled_chars = sum(s.compiled_chars for s in trace_buffer)
        pt.sections_with_children = sum(1 for s in trace_buffer if s.children_count > 0)
        pt.llm_success_count = sum(1 for s in trace_buffer if s.llm_success)
        pt.llm_fail_count = sum(1 for s in trace_buffer if not s.llm_success)
        result.pipeline_trace = pt

        logger.info(
            "wiki_compiler_struct_done",
            doc_id=doc.doc_id,
            pages=page_count,
            sections=pt.total_sections,
            raw_chars=pt.total_raw_chars,
            compiled_chars=pt.total_compiled_chars,
        )
        return result

    async def _compile_tree_node_with_llm(
        self,
        nodes: list[dict],
        doc: ParsedDocument,
        source_entry: dict,
        result: WikiCompileResult,
        *,
        force: bool = False,
        parent_slug: str | None = None,
        trace_buffer: list[SectionTrace] | None = None,
        on_section_progress: Callable[[str, int, str], None] | None = None,
        on_progress: ProgressCallback | None = None,
        total_sections: int = 0,
        _section_index: list[int] | None = None,
    ) -> int:
        """递归编译标题树节点为 wiki 页面（使用 LLM 生成内容）

        trace_buffer: 可选列表，收集章节级管道追踪数据
        on_section_progress: 章节处理进度回调
        on_progress: M3 SSE 进度回调（用于发射 section_start/section_done 事件）
        total_sections: 总章节数（用于进度计算）
        _section_index: 可变列表 [0] 用于跨递归调用跟踪当前章节索引
        """
        if _section_index is None:
            _section_index = [0]

        def _emit(etype: ProgressEventType, data: dict[str, Any]) -> None:
            if on_progress:
                try:
                    on_progress(etype, data)
                except Exception:
                    pass

        count = 0

        # P1 (K5): Phase 1 — 同级节点并行 LLM 编译
        # 收集需要 LLM 编译的节点，预分配 section_index 避免并发争用
        compile_nodes: list[dict] = []
        node_section_indices: dict[str, int] = {}
        for node in nodes:
            slug = node.get("slug")
            if slug and node.get("level", 1) <= 3:
                _section_index[0] += 1
                node_section_indices[slug] = _section_index[0]
                compile_nodes.append(node)

        # 并行编译所有同级节点（LLM 调用是主要瓶颈）
        compiled_results: dict[str, tuple[str, bool, float]] = {}  # slug -> (body_md, llm_success, processing_time_ms)
        if compile_nodes:
            async def _compile_one_parallel(node: dict) -> tuple[str, str, bool, float]:
                slug = node.get("slug")
                t_start = time.monotonic()
                try:
                    body_md = await self._llm_compile_section(node, parent_slug)
                    processing_time_ms = (time.monotonic() - t_start) * 1000
                    return (slug, body_md, True, processing_time_ms)
                except Exception:
                    processing_time_ms = (time.monotonic() - t_start) * 1000
                    return (slug, self._build_section_body(node, parent_slug), False, processing_time_ms)

            tasks = [_compile_one_parallel(n) for n in compile_nodes]
            parallel_results = await asyncio.gather(*tasks)
            for slug_key, body, success, elapsed in parallel_results:
                compiled_results[slug_key] = (body, success, elapsed)

        # P1 (K5): Phase 2 — 串行保存和子节点递归（SQLite 写入安全）
        for node in nodes:
            slug = node.get("slug")
            title = node.get("title", "")
            level = node.get("level", 1)

            if not slug or level > 3:
                if node.get("children"):
                    child_count = await self._compile_tree_node_with_llm(
                        node["children"], doc, source_entry, result, force=force, parent_slug=slug, trace_buffer=trace_buffer,
                        on_section_progress=on_section_progress,
                        on_progress=on_progress,
                        total_sections=total_sections,
                        _section_index=_section_index,
                    )
                    count += child_count
                continue

            section_idx = node_section_indices.get(slug, _section_index[0])

            # 发射 section_start 事件
            _emit(ProgressEventType.SECTION_START, {
                "slug": slug,
                "title": title,
                "level": level,
                "index": section_idx,
                "total": total_sections,
                "children_count": len(node.get("children", [])),
            })

            if on_section_progress:
                on_section_progress(title, level, "processing")

            # 准备原始内容
            elements = node.get("elements", [])
            raw_content = self._render_elements_to_text(elements)
            raw_chars = len(raw_content)

            # 使用 Phase 1 预编译的结果
            body_md, llm_success, processing_time_ms = compiled_results.get(
                slug, (self._build_section_body(node, parent_slug), False, 0.0)
            )

            if on_section_progress:
                on_section_progress(title, level, "done" if llm_success else "failed")

            # 收集管道追踪数据
            if trace_buffer is not None:
                trace_buffer.append(SectionTrace(
                    title=title,
                    level=level,
                    slug=slug,
                    raw_content=raw_content,
                    raw_chars=raw_chars,
                    compiled_content=body_md,
                    compiled_chars=len(body_md),
                    llm_success=llm_success,
                    processing_time_ms=round(processing_time_ms, 1),
                    children_count=len(node.get("children", [])),
                ))

            page = WikiPage(
                slug=slug,
                title=title,
                type="concept",
                tags=[f"section-level-{level}", "document-structure"],
                sources=[source_entry],
                body_md=body_md,
                review_status="auto",
                source_doc_id=source_entry.get("doc_id", ""),
            )

            try:
                outcome = self._save_page(page, force=force)
                # GS-6: 双向同步
                self._sync_page_to_graph(page)
                result.slugs.append(slug)
                if outcome == "created":
                    result.pages_created += 1
                elif outcome == "updated":
                    result.pages_updated += 1
                else:
                    result.pages_unchanged += 1
                # S2: 质量校验
                quality = self._validate_page_quality(page.body_md, page.type)
                if not quality["valid"]:
                    page.review_status = "review_needed"
                    result.review_needed.append(slug)
                    logger.info(
                        "wiki_struct_quality_fail",
                        slug=slug,
                        issues=quality["issues"],
                        score=quality["score"],
                    )
                count += 1
                # 发射 section_done 事件
                _emit(ProgressEventType.SECTION_DONE, {
                    "slug": slug,
                    "title": title,
                    "level": level,
                    "outcome": outcome,
                    "raw_chars": raw_chars,
                    "compiled_chars": len(body_md),
                    "llm_success": llm_success,
                    "processing_time_ms": round(processing_time_ms, 1),
                    "children_count": len(node.get("children", [])),
                    "index": section_idx,
                    "total": total_sections,
                })
            except Exception as e:
                logger.exception("wiki_compiler_struct_node_failed", slug=slug)
                result.errors.append(f"{slug}: {e}")
                # 发射 section_done 错误事件
                _emit(ProgressEventType.SECTION_DONE, {
                    "slug": slug,
                    "title": title,
                    "level": level,
                    "outcome": "error",
                    "error": str(e),
                    "llm_success": False,
                    "index": section_idx,
                    "total": total_sections,
                })

            if node.get("children"):
                count += await self._compile_tree_node_with_llm(
                    node["children"], doc, source_entry, result, force=force, parent_slug=slug, trace_buffer=trace_buffer
                )

        return count

    async def _llm_compile_section(self, node: dict, parent_slug: str | None = None) -> str:
        """使用 LLM 将章节内容编译为结构化 wiki 页面

        Args:
            node: 章节节点字典（含 title, elements, children）
            parent_slug: 父章节 slug

        Returns:
            结构化 wiki 页面正文（不含 frontmatter）
        """
        title = node.get("title", "")
        level = node.get("level", 1)
        elements = node.get("elements", [])

        content_text = self._render_elements_to_text(elements)

        children_info = []
        for child in node.get("children", []):
            child_slug = child.get("slug")
            child_title = child.get("title", "")
            if child_slug:
                children_info.append(f"- [[{child_slug}|{child_title}]]")
            else:
                children_info.append(f"- {child_title}")

        parent_info = f"父级章节：[[{parent_slug}]]" if parent_slug else ""

        system_prompt = """你是 OpsKG Wiki 管理员。把文档章节编译为结构化 Markdown wiki 页面。

严格遵循 AGENTS.md 规定的页面骨架。使用 [[slug]] 双向链接到相关概念。

页面类型：概念页（concept）
必含章节：概述、原理、应用场景、来源

注意：
1. 只输出 Markdown 正文，不要 YAML frontmatter，不要 ```md 包裹
2. 在首次提及相关概念/服务/主机时，用 [[kebab-case-slug]] 形式建链
3. 不要编造未在原文中出现的具体数值
4. 保留原文的表格和代码块格式
5. 使用合适的标题层级（从 ## 开始）"""

        user_prompt = f"""请把以下文档章节编译为一个 wiki 页面。

# 章节标题
{title}

# 章节层级
H{level}

# 父级章节
{parent_info}

# 子章节
{chr(10).join(children_info) if children_info else "（无）"}

# 原文内容
{content_text[:4000]}

# 编译要求
1. 严格按概念页骨架输出 Markdown 章节（## 概述、## 原理、## 应用场景、## 来源）
2. 在首次提及相关概念时，用 [[kebab-case-slug]] 形式建链
3. 保留原文中的表格和代码块
4. 「## 来源」章节引用本页来源即可
5. 标题用 `# {title}` 起首"""

        cache_key = self._get_llm_cache_key(content_text, system_prompt, user_prompt)
        if cache_key in self._llm_cache:
            logger.info("llm_cache_hit", method="compile_section", title=title)
            try:
                record_business_metric("llm_cache_hits_total", cache_type="compile_section")
            except Exception:  # noqa: BLE001
                pass
            return self._llm_cache[cache_key]

        logger.info("llm_cache_miss", method="compile_section", title=title)
        try:
            text = await self._llm_complete(user_prompt, system=system_prompt, temperature=0.2)
            result = self._strip_codefence(text).strip()
            # P1: LRU 淘汰 — 缓存超限时移除最旧条目
            if len(self._llm_cache) >= self._CACHE_MAX_SIZE:
                remove_count = max(1, self._CACHE_MAX_SIZE // 10)
                for _ in range(remove_count):
                    self._llm_cache.pop(next(iter(self._llm_cache)), None)
            self._llm_cache[cache_key] = result
            return result
        except Exception as e:
            logger.error("llm_compile_section_llm_error", title=title, error=str(e))
            return self._build_section_body(node, parent_slug)

    def _render_elements_to_text(self, elements: list[dict]) -> str:
        """将元素列表渲染为纯文本"""
        lines = []
        for elem in elements:
            etype = elem.get("type", "")
            content = elem.get("content", "")
            if not content.strip():
                continue

            if etype == "paragraph":
                lines.append(content)
            elif etype == "code":
                lines.append("```")
                lines.append(content)
                lines.append("```")
            elif etype == "table":
                lines.append(content)
            elif etype == "list":
                lines.append(content)
            elif etype == "heading":
                h_level = elem.get("metadata", {}).get("level", 1)
                lines.append("#" * h_level + " " + content)
        return "\n\n".join(lines)

    def _build_section_body(self, node: dict, parent_slug: str | None = None) -> str:
        """为章节节点构建 wiki 正文（包含实际内容）"""
        level = node.get("level", 1)
        elements = node.get("elements", [])

        lines = []

        lines.append("## 概述")
        lines.append(f"本章节为文档结构的一部分，包含 {len(elements)} 个内容元素。")
        lines.append("")

        if level > 1:
            lines.append("## 父级章节")
            if parent_slug:
                lines.append(f"- [[{parent_slug}]]")
            lines.append("")

        children = node.get("children", [])
        if children:
            lines.append("## 子章节")
            for child in children:
                child_slug = child.get("slug")
                child_title = child.get("title", "")
                if child_slug:
                    lines.append(f"- [[{child_slug}|{child_title}]]")
                else:
                    lines.append(f"- {child_title}")
            lines.append("")

        lines.append("## 内容")
        lines.append("")

        for elem in elements:
            etype = elem.get("type", "")
            content = elem.get("content", "")
            if not content.strip():
                continue

            if etype == "paragraph":
                lines.append(content)
                lines.append("")
            elif etype == "code":
                lines.append(f"```\n{content}\n```")
                lines.append("")
            elif etype == "table":
                lines.append(content)
                lines.append("")
            elif etype == "list":
                lines.append(content)
                lines.append("")
            elif etype == "heading":
                h_level = elem.get("metadata", {}).get("level", level + 1)
                lines.append("#" * h_level + " " + content)
                lines.append("")

        lines.append("## 来源")
        lines.append("- 文档结构自动生成")

        return "\n".join(lines)

    async def _llm_write_body(self, entity: ExtractedEntity, page_type: str, relations_map: dict[str, str] | None = None) -> str:
        """让 LLM 按 AGENTS.md 骨架写页面正文

        Args:
            entity: 抽取实体
            page_type: wiki 页面类型
            relations_map: P1 (K4) 预取的关系映射

        返回不含 frontmatter 的 Markdown 正文。
        """
        raw_content = (entity.evidence_span or "").strip()
        cache_key = self._get_llm_cache_key(entity.entity_type, entity.name, raw_content)
        if cache_key in self._llm_cache:
            logger.info("llm_cache_hit", method="write_body", entity=entity.name, type=entity.entity_type)
            try:
                record_business_metric("llm_cache_hits_total", cache_type="write_body")
            except Exception:  # noqa: BLE001
                pass
            return self._llm_cache[cache_key]

        logger.info("llm_cache_miss", method="write_body", entity=entity.name, type=entity.entity_type)
        system = (
            "你是 OpsKG Wiki 管理员。把运维知识编译为结构化 Markdown wiki 页面。"
            "严格遵循 AGENTS.md 规定的页面骨架。"
            "使用 [[slug]] 双向链接到相关概念。"
            "只输出 Markdown 正文，不要 YAML frontmatter，不要 ```md 包裹。"
        )
        prompt = self._build_writing_prompt(entity, page_type, relations_map=relations_map)
        text = await self._llm_complete(prompt, system=system, temperature=0.2)
        # 防御：剥离可能误加的代码块围栏
        text = self._strip_codefence(text)
        result = text.strip()
        # P1: LRU 淘汰 — 缓存超限时移除最旧条目
        if len(self._llm_cache) >= self._CACHE_MAX_SIZE:
            remove_count = max(1, self._CACHE_MAX_SIZE // 10)
            for _ in range(remove_count):
                self._llm_cache.pop(next(iter(self._llm_cache)), None)
        self._llm_cache[cache_key] = result
        return result

    def _build_writing_prompt(self, entity: ExtractedEntity, page_type: str, relations_map: dict[str, str] | None = None) -> str:
        """构造写作 prompt（P3-1: 融合图谱关系作为编译上下文）

        Args:
            entity: 抽取实体
            page_type: wiki 页面类型
            relations_map: P1 (K4) 预取的关系映射，避免逐实体查询
        """
        props_str = (
            "\n".join(f"- {k}: {v}" for k, v in entity.properties.items() if v)
            or "（无）"
        )
        evidence = (entity.evidence_span or "").strip()[:4000]  # P0-2: 从 1200 提升到 4000
        type_label = {
            "incident": "故障页（必含：概述/成因分析/排查步骤/处置方案/来源）",
            "runbook": "操作手册页（必含：概述/影响分析/排查步骤/处置方案/来源）",
            "service": "服务页（必含：概述/架构/依赖/配置参数/来源）",
            "host": "主机页（必含：概述/角色/运行服务/来源）",
            "entity": "实体页（必含：概述/属性/关系/来源）",
            "concept": "概念页（必含：概述/原理/应用场景/来源）",
        }.get(page_type, "概念页（必含：概述/原理/应用场景/来源）")

        # P1 (K4): 优先使用预取的关系映射，避免逐实体查询
        if relations_map is not None and entity.name in relations_map:
            relations_str = relations_map[entity.name]
        else:
            relations_str = self._fetch_graph_relations(entity.name)

        return f"""请把以下运维知识编译为一个 wiki 页面。

# 编译目标
- 名称：{entity.name}
- 实体类型：{entity.entity_type}
- 页面类型：{page_type}
- 页面骨架：{type_label}

# 已知属性
{props_str}

# 知识图谱中的已知关系
{relations_str}

# 原文证据片段
{evidence}

# 编译要求
1. 严格按上述骨架输出 Markdown 章节
2. 在首次提及相关概念/服务/主机时，用 [[kebab-case-slug]] 形式建链
3. 不要编造未在证据中出现的具体数值
4. 「## 来源」章节引用本页来源即可
5. 标题用 `# {entity.name}` 起首
6. P3-1: 如果"已知关系"中有相关实体，在"关系/依赖"章节中引用并用 [[slug]] 建链
"""

    @staticmethod
    def _fetch_graph_relations(entity_name: str) -> str:
        """P3-1: 查询图谱中该实体的一跳邻居关系，用于编译上下文增强

        GraphStore 不可用时优雅降级（返回"无"），不影响编译流程。
        """
        try:
            from app.knowledge.graph_store import get_graph_store

            store = get_graph_store()
            relations = store.query_related(entity_name, depth=1)
            if not relations:
                return "（无图谱关系）"
            lines: list[str] = []
            for rel in relations[:20]:  # 最多 20 条，避免 prompt 过长
                target = rel.get("target", "")
                relation = rel.get("relation", "")
                target_type = rel.get("target_type", "")
                confidence = rel.get("confidence", 0)
                lines.append(
                    f"- [{relation}] → {target}（类型: {target_type}, 置信度: {confidence:.2f}）"
                )
            return "\n".join(lines)
        except Exception:  # noqa: BLE001
            # GraphStore 不可用（Neo4j 未配置）→ 优雅降级
            return "（图谱不可用）"

    @staticmethod
    def _fetch_graph_relations_batch(entity_names: list[str]) -> dict[str, str]:
        """P1 (K4): 批量查询图谱关系，减少网络往返

        TODO: graph_store 应提供原生 query_related_batch() 方法，
        当前实现逐个调用 query_related()，但集中收集避免了逐实体
        初始化 graph_store 连接的开销。

        Args:
            entity_names: 实体名称列表

        Returns:
            {entity_name: formatted_relations_str} 映射
        """
        if not entity_names:
            return {}
        try:
            from app.knowledge.graph_store import get_graph_store

            store = get_graph_store()
            result: dict[str, str] = {}
            for name in entity_names:
                try:
                    relations = store.query_related(name, depth=1)
                    if not relations:
                        result[name] = "（无图谱关系）"
                        continue
                    lines: list[str] = []
                    for rel in relations[:20]:
                        target = rel.get("target", "")
                        relation = rel.get("relation", "")
                        target_type = rel.get("target_type", "")
                        confidence = rel.get("confidence", 0)
                        lines.append(
                            f"- [{relation}] → {target}（类型: {target_type}, 置信度: {confidence:.2f}）"
                        )
                    result[name] = "\n".join(lines)
                except Exception:
                    result[name] = "（图谱不可用）"
            return result
        except Exception:  # noqa: BLE001
            return {name: "（图谱不可用）" for name in entity_names}

    @staticmethod
    def _strip_codefence(text: str) -> str:
        """剥离误加的 ```md ... ``` 围栏"""
        t = text.strip()
        if t.startswith("```"):
            # 去首行（可能含语言标记）
            lines = t.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            return "\n".join(lines)
        return t

    # P0: 增强模板兜底 — 当 LLM 不可用时，从源文档提取内容生成结构化 Wiki 页面
    def _build_template_fallback(
        self,
        entity_type: str,
        entity_name: str,
        properties: dict,
        source_content: str = "",
        paragraph_classifications: list[dict] | None = None,
    ) -> str:
        """构建增强的模板兜底正文（当 LLM 不可用时）

        从源文档中提取相关段落，生成结构化 Wiki 页面。
        """
        sections: list[str] = []

        # 1. 概述
        overview = self._extract_overview(entity_name, source_content, paragraph_classifications)
        sections.append(f"## 概述\n\n{overview}")

        # 2. 根据实体类型生成相应章节
        if entity_type == "incident":
            sections.append(self._build_cause_section(entity_name, source_content))
            sections.append(self._build_troubleshoot_section(entity_name, source_content))
            sections.append(self._build_resolution_section(entity_name, source_content))
        elif entity_type == "service":
            sections.append(self._build_architecture_section(entity_name, source_content, properties))
            sections.append(self._build_config_section(entity_name, source_content, properties))
        elif entity_type == "concept":
            sections.append(self._build_explanation_section(entity_name, source_content))
            sections.append(self._build_usage_section(entity_name, source_content))
        elif entity_type == "runbook":
            sections.append(self._build_impact_section(entity_name, source_content))
            sections.append(self._build_steps_section(entity_name, source_content))
        elif entity_type == "host":
            sections.append(self._build_role_section(entity_name, source_content, properties))
            sections.append(self._build_services_section(entity_name, source_content, properties))
        else:
            # Generic: extract body paragraphs
            sections.append(self._build_body_section(entity_name, source_content))

        # 3. 来源引用
        sections.append("## 来源\n\n" + self._build_source_section(properties))

        return "\n\n".join(sections)

    def _extract_overview(
        self, name: str, content: str, classifications: list[dict] | None = None
    ) -> str:
        """从源文档提取概述段落"""
        if not content:
            return f"{name} 的相关信息。"

        # 按段落分割源文档
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]

        # 优先使用分类为 overview/summary 的段落
        if classifications:
            for pc in classifications:
                if pc.get('label') in ('overview', 'summary', '介绍'):
                    body = pc.get('body', '')
                    if body:
                        return body

        # 使用前几个段落作为概述
        overview_paras = paragraphs[:min(3, len(paragraphs))]
        overview = ' '.join(overview_paras)

        # 截断过长概述
        if len(overview) > 500:
            overview = overview[:497] + '...'

        return overview if overview else f"{name} 的相关信息。"

    def _build_cause_section(self, name: str, content: str) -> str:
        """构建成因分析章节"""
        causes = self._extract_list_items(content, ['原因', '导致', '引起', '因为', 'cause'])
        if causes:
            items = '\n'.join(f'- {c}' for c in causes[:8])
            return f"## 成因分析\n\n{items}"
        return "## 成因分析\n\n> 待补充。请参考原始文档了解详细原因。"

    def _build_troubleshoot_section(self, name: str, content: str) -> str:
        """构建排查步骤章节"""
        steps = self._extract_list_items(content, ['检查', '排查', '查看', '验证', '确认', 'check', 'verify'])
        if steps:
            items = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(steps[:10]))
            return f"## 排查步骤\n\n{items}"
        return "## 排查步骤\n\n> 待补充。"

    def _build_resolution_section(self, name: str, content: str) -> str:
        """构建处置方案章节"""
        solutions = self._extract_list_items(
            content, ['解决', '修复', '方案', '处理', '重启', '修改', '调整', '配置', 'fix', 'resolve']
        )
        if solutions:
            items = '\n'.join(f"- {s}" for s in solutions[:8])
            return f"## 处置方案\n\n{items}"
        return "## 处置方案\n\n> 待补充。"

    def _build_architecture_section(
        self, name: str, content: str, properties: dict
    ) -> str:
        """构建架构章节（service 类型）"""
        arch_lines = self._extract_list_items(content, ['架构', '依赖', '调用', '上游', '下游', 'architecture'])
        if arch_lines:
            items = '\n'.join(f"- {a}" for a in arch_lines[:8])
            return f"## 架构\n\n{items}"
        return "## 架构\n\n> 待补充。"

    def _build_config_section(
        self, name: str, content: str, properties: dict
    ) -> str:
        """构建配置参数章节（service 类型）"""
        config_lines = self._extract_list_items(content, ['配置', '参数', '端口', 'config', 'port', 'timeout'])
        if config_lines:
            items = '\n'.join(f"- {c}" for c in config_lines[:8])
            return f"## 配置参数\n\n{items}"
        return "## 配置参数\n\n> 待补充。"

    def _build_explanation_section(self, name: str, content: str) -> str:
        """构建原理章节（concept 类型）"""
        explanation = self._extract_list_items(content, ['原理', '定义', '概念', '机制', '原理'])
        if explanation:
            items = '\n'.join(f"- {e}" for e in explanation[:8])
            return f"## 原理\n\n{items}"
        return "## 原理\n\n> 待补充。"

    def _build_usage_section(self, name: str, content: str) -> str:
        """构建应用场景章节（concept 类型）"""
        usage = self._extract_list_items(content, ['场景', '应用', '使用', '示例', '例子', '场景', 'usage', 'example'])
        if usage:
            items = '\n'.join(f"- {u}" for u in usage[:8])
            return f"## 应用场景\n\n{items}"
        return "## 应用场景\n\n> 待补充。"

    def _build_impact_section(self, name: str, content: str) -> str:
        """构建影响分析章节（runbook 类型）"""
        impact = self._extract_list_items(content, ['影响', '风险', '范围', '影响范围', 'impact'])
        if impact:
            items = '\n'.join(f"- {i}" for i in impact[:8])
            return f"## 影响分析\n\n{items}"
        return "## 影响分析\n\n> 待补充。"

    def _build_steps_section(self, name: str, content: str) -> str:
        """构建操作步骤章节（runbook 类型）"""
        steps = self._extract_list_items(content, ['步骤', '操作', '执行', '运行', '启动', '停止', 'step', 'run'])
        if steps:
            items = '\n'.join(f"{i+1}. {s}" for i, s in enumerate(steps[:10]))
            return f"## 操作步骤\n\n{items}"
        return "## 操作步骤\n\n> 待补充。"

    def _build_role_section(
        self, name: str, content: str, properties: dict
    ) -> str:
        """构建角色章节（host 类型）"""
        role = properties.get('role', '') or properties.get('功能', '')
        if role:
            return f"## 角色\n\n{role}"
        return "## 角色\n\n> 待补充。"

    def _build_services_section(
        self, name: str, content: str, properties: dict
    ) -> str:
        """构建运行服务章节（host 类型）"""
        services = self._extract_list_items(content, ['服务', '进程', 'service', 'process', 'nginx', 'tomcat', 'docker'])
        if services:
            items = '\n'.join(f"- {s}" for s in services[:8])
            return f"## 运行服务\n\n{items}"
        return "## 运行服务\n\n> 待补充。"

    def _build_body_section(self, name: str, content: str) -> str:
        """构建通用正文章节（兜底类型）"""
        if not content:
            return "## 内容\n\n> 待补充。"
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
        if paragraphs:
            body = '\n\n'.join(paragraphs[:5])
            return f"## 内容\n\n{body}"
        return "## 内容\n\n> 待补充。"

    @staticmethod
    def _build_source_section(properties: dict) -> str:
        """构建来源章节"""
        source_doc_id = properties.get('source_doc_id', '')
        if source_doc_id:
            return f"- doc_id: `{source_doc_id}`\n"
        return "- （暂无来源信息）\n"

    @staticmethod
    def _extract_list_items(content: str, keywords: list[str]) -> list[str]:
        """从内容中提取包含关键词的列表项"""
        items: list[str] = []
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 去除列表标记
            cleaned = re.sub(r'^[\s]*[-*•\d]+[\.\)、]\s*', '', line)
            if any(kw in cleaned.lower() for kw in keywords):
                if len(cleaned) > 10 and cleaned not in items:
                    items.append(cleaned)
        return items

    # ── GS-6: 双向同步 — Wiki 页面保存后同步更新图谱 ──

    def _sync_page_to_graph(self, page: WikiPage) -> None:
        """GS-6: Wiki 页面保存后，将实体信息同步到 Neo4j 知识图谱

        Wiki → Graph 双向同步：
        - 创建/更新对应的 GraphEntity 节点
        - 图不可用时静默降级，不影响编译流程
        - P0-3: 失败时记录 Prometheus 指标 + 限频警告日志
        """
        try:
            from app.knowledge.graph_store import GraphEntity, get_graph_store

            store = get_graph_store()
            entity = GraphEntity(
                entity_type=page.type,
                name=page.title,
                properties={
                    "slug": page.slug,
                    "tags": page.tags,
                    "review_status": page.review_status,
                    "source_doc_id": page.source_doc_id,
                    "paragraph_labels": page.paragraph_labels,
                },
                source_doc_id=page.source_doc_id,
                confidence=1.0,  # wiki 页面已确认
            )
            store.upsert_entity(entity)
            logger.info(
                "wiki_graph_synced",
                slug=page.slug,
                entity_type=page.type,
            )
        except Exception as e:
            # P0-3: 记录 Prometheus 指标
            try:
                from app.observability.metrics import record_business_metric
                record_business_metric("graph_sync_failures_total", 1.0, operation="sync_entity")
            except Exception:
                pass
            # 限频警告：每 60 秒最多输出一次警告日志
            _now = time.monotonic()
            _last = getattr(self, '_last_graph_sync_warning', 0.0)
            if _now - _last > 60:
                logger.warning(
                    "wiki_graph_sync_failed",
                    slug=page.slug,
                    error=str(e),
                )
                self._last_graph_sync_warning = _now  # type: ignore[attr-defined]

    # M1: 相似度阈值
    _SIMILARITY_THRESHOLD = 0.8

    def _find_similar_page(self, page: WikiPage) -> str | None:
        """M1: 查找与当前页面语义相似的已有页面

        使用 TF-IDF 风格的词频余弦相似度，比较标题和正文。
        返回相似度最高的已有页面 slug，如果都不超过阈值则返回 None。
        """
        try:
            existing_pages = list_wiki_pages()
        except Exception:
            return None

        if not existing_pages:
            return None

        # 收集已有 slug 集合（GS-2: 图谱相似度交叉引用）
        existing_slugs = {ep.get("slug", "") for ep in existing_pages}

        # 构建新页面的词袋（标题权重 ×3）
        new_bow = self._build_bow(page.title, page.body_md)

        # P0: 批量预加载所有同类型已有页面的内容（单次 SQL 查询替代 N 次 get_latest）
        same_type_keys = [
            _key_from_slug(ep["slug"])
            for ep in existing_pages
            if ep.get("type", "") == page.type and ep.get("slug", "") != page.slug
        ]
        if not same_type_keys:
            return None

        batch_data = self.vc.get_latest_batch(same_type_keys)

        best_slug: str | None = None
        best_score = 0.0

        for ep in existing_pages:
            ep_slug = ep.get("slug", "")
            if not ep_slug or ep_slug == page.slug:
                continue
            # 只比较同类型页面
            if ep.get("type", "") != page.type:
                continue

            ep_key = _key_from_slug(ep_slug)
            ep_data = batch_data.get(ep_key)
            if not ep_data:
                continue
            ep_title = ep.get("title", "")
            ep_body = ep_data.get("content", "")
            ep_bow = self._build_bow(ep_title, ep_body)
            score = _cosine_similarity(new_bow, ep_bow)
            if score > best_score:
                best_score = score
                best_slug = ep_slug

        if best_score >= self._SIMILARITY_THRESHOLD and best_slug:
            logger.info(
                "wiki_similarity_match",
                new_slug=page.slug,
                similar_slug=best_slug,
                score=round(best_score, 3),
            )
            return best_slug
        # GS-2: 图谱相似度增强 — 基于图结构的相似性得分
        try:
            from app.knowledge.graph_store import get_graph_store
            graph_store = get_graph_store()
            # 使用当前实体名称查询相似实体
            similar_graph = graph_store.node_similarity(page.title, limit=3)
            for sg in similar_graph:
                # 映射 graph 名称到 wiki slug 候选
                sg_candidates = _entity_to_wiki_slugs(sg["name"])
                for sg_candidate in sg_candidates:
                    if sg_candidate in existing_slugs and sg_candidate != page.slug:
                        # 图谱结构相似性提升总分 (权重 0.3)
                        boosted_score = best_score + sg["score"] * 0.3
                        if boosted_score >= self._SIMILARITY_THRESHOLD:
                            logger.info(
                                "wiki_graph_similarity_boost",
                                new_slug=page.slug,
                                similar_slug=sg_candidate,
                                base_score=round(best_score, 3),
                                boosted_score=round(boosted_score, 3),
                            )
                            return sg_candidate
        except Exception:
            # 图不可用时不影响，继续返回 best_slug 还是 None
            pass

        return None

    @staticmethod
    def _build_bow(title: str, body: str) -> dict[str, float]:
        """构建加权词袋（标题权重 ×3）"""
        bow: dict[str, float] = {}
        # 标题 token（权重 ×3）
        for token in _tokenize(title):
            bow[token] = bow.get(token, 0.0) + 3.0
        # 正文 token（权重 ×1）
        for token in _tokenize(body[:2000]):  # 限制长度
            bow[token] = bow.get(token, 0.0) + 1.0
        return bow

    def _save_page(self, page: WikiPage, *, force: bool) -> str:
        """保存页面到 VersionControl

        Returns:
            "created" | "updated" | "unchanged"
        """
        doc_key = _key_from_slug(page.slug)
        existing = self.vc.get_latest(doc_key)

        # 合并：已有页面 → 增量合并；否则新建
        if existing:
            new_md, stale_items = self._merge_existing(existing["content"], page)
            page.stale_items = stale_items
            # 内容未变 → 跳过
            if not force and self._content_equal(existing["content"], new_md):
                # 仍然刷新 backlink 以保证一致
                update_backlinks(page.slug, existing["content"])
                return "unchanged"
            md_to_save = new_md
            outcome = "updated"
        else:
            # M1: 相似度检测 — 查找语义相似但 slug 不同的已有页面
            similar_slug = self._find_similar_page(page)
            if similar_slug:
                similar_key = _key_from_slug(similar_slug)
                similar_existing = self.vc.get_latest(similar_key)
                if similar_existing:
                    logger.info(
                        "wiki_similar_page_found",
                        new_slug=page.slug,
                        similar_slug=similar_slug,
                    )
                    # 合并到相似页面（使用 similarity_slug）
                    merged_page = WikiPage(
                        slug=similar_slug,
                        title=page.title,
                        type=page.type,
                        tags=list(set(page.tags + (similar_existing.get("tags", [])))),
                        sources=page.sources,
                        body_md=page.body_md,
                        review_status=page.review_status,
                        source_doc_id=page.source_doc_id,
                        paragraph_labels=page.paragraph_labels,
                    )
                    new_md, stale_items = self._merge_existing(
                        similar_existing["content"], merged_page
                    )
                    merged_page.stale_items = stale_items
                    if not force and self._content_equal(similar_existing["content"], new_md):
                        update_backlinks(similar_slug, similar_existing["content"])
                        return "unchanged"
                    md_to_save = new_md
                    page.slug = similar_slug  # 更新 slug 为实际保存的
                    doc_key = similar_key
                    outcome = "updated"
                else:
                    md_to_save = self._render_page_md(page, is_new=True)
                    outcome = "created"
            else:
                md_to_save = self._render_page_md(page, is_new=True)
                outcome = "created"

        save_result = self.vc.save_version(
            doc_key=doc_key,
            title=page.title,
            content=md_to_save,
            author="wiki-compiler",
            change_summary=self._change_summary(page, outcome),
        )
        # 维护 backlink
        update_backlinks(page.slug, md_to_save)

        # P1-2: 持续维护 wiki:log（OKF log.md 保留文件）
        # 仅在实际写入新版本时追加 log entry（skipped 时不追加）
        if not save_result.get("skipped"):
            try:
                from app.knowledge.wiki_log import append_log_entry

                append_log_entry(
                    slug=page.slug,
                    version=save_result.get("version", 1),
                    summary=self._change_summary(page, outcome),
                    author="wiki-compiler",
                    page_type=page.type,
                    title=page.title,
                )
            except Exception as e:
                logger.warning(
                    "wiki_log_append_failed", slug=page.slug, error=str(e)
                )

        # S12-2 反向回链：新建页面时，扫描已有页面正文，
        # 在提及新概念处插入 [[new_slug]]（AGENTS.md §五 5.b）
        if outcome == "created":
            try:
                back = self._backlink_existing_pages(page.slug, page.title)
                if back > 0:
                    logger.info(
                        "wiki_backlink_retrofitted",
                        slug=page.slug,
                        updated=back,
                    )
            except Exception as e:
                logger.warning(
                    "wiki_backlink_retrofit_failed", slug=page.slug, error=str(e)
                )
        return outcome

    # M2: 冲突检测提示
    _CONFLICT_DETECTION_PROMPT = """你是一个运维知识文档审查专家。请分析以下两个版本的 wiki 页面内容，检测是否存在信息冲突。

## 旧版本内容
{old_body}

## 新版本内容
{new_body}

## 检测规则
1. 对比同一参数/配置项的值，如果新旧版本不同 → 标记为冲突
2. 对比同一概念的定义/描述，如果存在矛盾 → 标记为冲突
3. 对比同一故障的排查步骤/处置方案，如果存在差异 → 标记为冲突
4. 如果仅仅是新增内容（旧版本没有），不要标记为冲突
5. 如果内容一致或仅为表述差异，不要标记为冲突

## 输出格式（严格 JSON）
{{
  "has_conflict": true/false,
  "conflicts": [
    {{
      "item": "冲突项名称",
      "old_value": "旧版本中的值",
      "new_value": "新版本中的值",
      "severity": "high/medium/low",
      "resolution": "建议的消解方案"
    }}
  ]
}}

如果无冲突，返回 {{"has_conflict": false, "conflicts": []}}"""

    async def _detect_conflicts_with_llm(
        self, old_body: str, new_body: str
    ) -> list[str]:
        """M2: 使用 LLM 检测合并时的语义冲突

        Returns:
            冲突描述列表，格式: ["{severity}: {item} (旧: {old} → 新: {new})"]
        """
        if not old_body.strip() or not new_body.strip():
            return []

        prompt = self._CONFLICT_DETECTION_PROMPT.format(
            old_body=old_body[:3000],
            new_body=new_body[:3000],
        )
        try:
            resp = await self._llm_complete(prompt, temperature=0.1)
            if not resp:
                return []
            data = _parse_json_response(resp)
            if not data or not data.get("has_conflict"):
                return []
            conflict_descriptions = []
            for c in data.get("conflicts", []):
                desc = (
                    f"{c.get('severity', 'medium')}: {c.get('item', '未知')} "
                    f"(旧: {c.get('old_value', '?')} → 新: {c.get('new_value', '?')})"
                )
                conflict_descriptions.append(desc)
            return conflict_descriptions
        except Exception:
            return []

    def _merge_existing(
        self, existing_md: str, new_page: WikiPage
    ) -> tuple[str, list[str]]:
        """把新事实合并到已有页面（P3-3: 智能整合，避免碎片化）

        策略升级（从"仅追加"到"智能整合"）：
        - 保留已有 frontmatter，合并 sources（去重 by doc_id）
        - P3-3: 按章节智能合并正文（同名章节→段落去重追加，新章节→直接追加）
        - 仅当新内容无章节结构时，使用"## 增量补充"兜底
        - 标注 stale：若新页面有但旧页面没有的属性 → 标 stale

        Returns:
            (merged_md, stale_items)
        """
        # 解析旧 frontmatter
        meta, body = self._split_frontmatter(existing_md)
        new_meta = self._build_frontmatter_meta(new_page, is_new=False)

        # 合并 sources（去重 by doc_id）
        old_sources = meta.get("sources", []) or []
        old_doc_ids = {s.get("doc_id") for s in old_sources if isinstance(s, dict)}
        for s in new_page.sources:
            if s.get("doc_id") not in old_doc_ids:
                old_sources.append(s)
        new_meta["sources"] = old_sources

        # stale 检测：新页面属性在旧正文中是否出现过
        stale_items: list[str] = []
        new_props = self._extract_property_lines(new_page.body_md)
        for line in new_props:
            # 简化：如果该属性键在旧正文中未出现，记为 stale
            key = line.split(":", 1)[0].strip().strip("*").strip()
            if key and key not in body:
                stale_items.append(line)

        # P3-3: 智能整合 — 按章节合并正文，避免"增量补充"章节堆积
        body = self._merge_body_sections(body, new_page.body_md, new_page.source_doc_id)

        merged_md = self._assemble_md(new_meta, body)
        return merged_md, stale_items

    # ────────── P3-3: 智能合并 ──────────

    @staticmethod
    def _parse_sections(body: str) -> list[tuple[str, str]]:
        """解析正文为 [(section_header, section_content)] 列表

        - section_header 不含 ## 前缀（如 "概述", "成因分析"）
        - 第一个 section 的 header 为 "" 表示 ## 之前的内容（preamble）
        - 三级标题（###）归入所属二级标题的 content
        """
        sections: list[tuple[str, str]] = []
        current_header = ""
        current_lines: list[str] = []

        for line in body.splitlines():
            if line.startswith("## "):
                # 保存上一个 section
                sections.append((current_header, "\n".join(current_lines)))
                current_header = line[3:].strip()
                current_lines = []
            else:
                current_lines.append(line)
        # 保存最后一个 section
        sections.append((current_header, "\n".join(current_lines)))
        return sections

    def _merge_body_sections(
        self,
        existing_body: str,
        new_body: str,
        source_doc_id: str,
    ) -> str:
        """P3-3: 按章节智能合并正文，避免碎片化

        策略：
        1. 解析已有正文和新正文为章节列表
        2. 对新正文的每个 ## 章节：
           a. 已有同名章节 → 追加新段落（去重，token Jaccard >= 0.7 跳过）
           b. 无同名章节 → 追加为新章节
        3. 新正文无 ## 章节结构 → 使用"## 增量补充"兜底（向后兼容）
        """
        new_body = new_body.strip()
        if not new_body:
            return existing_body

        existing_sections = self._parse_sections(existing_body)
        new_sections = self._parse_sections(new_body)

        # 新内容无 ## 章节 → 兜底追加（向后兼容旧行为）
        has_section_header = any(h for h, _ in new_sections)
        if not has_section_header:
            append = (
                f"## 增量补充（来自 `{source_doc_id}`）\n\n"
                f"> 此章节由 wiki_compiler 增量合并，可能需要人工整合到上文。\n\n"
                f"{new_body}\n"
            )
            return existing_body.rstrip() + "\n\n" + append

        # 构建 existing header → index 映射（小写匹配）
        existing_map: dict[str, int] = {}
        for i, (h, _) in enumerate(existing_sections):
            if h:
                existing_map[h.lower()] = i

        # 合并：对每个新章节，找匹配的已有章节
        merged_sections = [(h, c) for h, c in existing_sections]  # 浅拷贝
        for new_header, new_content in new_sections:
            if not new_header:
                continue  # 跳过 preamble（已有正文的 preamble 保留）

            idx = existing_map.get(new_header.lower())
            if idx is not None:
                # 同名章节 → 追加新段落（去重）
                old_header, old_content = merged_sections[idx]
                merged_content = self._merge_section_content(old_content, new_content)
                merged_sections[idx] = (old_header, merged_content)
            else:
                # 无同名章节 → 追加为新章节
                merged_sections.append((new_header, new_content.strip()))

        # 重建正文
        return self._render_sections(merged_sections)

    @staticmethod
    def _merge_section_content(old_content: str, new_content: str) -> str:
        """合并同名章节的段落，去重（token Jaccard >= 0.7 视为重复）

        - 按 \\n\\n 分段
        - 新段落与已有段落 token Jaccard >= 0.7 → 跳过
        - 否则追加到已有段落末尾
        """
        old_paras = [p.strip() for p in old_content.split("\n\n") if p.strip()]
        new_paras = [p.strip() for p in new_content.split("\n\n") if p.strip()]

        for new_para in new_paras:
            new_tokens = set(re.findall(r"[\w]+", new_para.lower()))
            if not new_tokens:
                continue
            is_dup = False
            for old_para in old_paras:
                old_tokens = set(re.findall(r"[\w]+", old_para.lower()))
                if not old_tokens:
                    continue
                jaccard = len(new_tokens & old_tokens) / len(new_tokens | old_tokens)
                if jaccard >= 0.7:
                    is_dup = True
                    break
            if not is_dup:
                old_paras.append(new_para)

        return "\n\n".join(old_paras)

    @staticmethod
    def _render_sections(sections: list[tuple[str, str]]) -> str:
        """把 [(header, content)] 列表重建为完整正文"""
        parts: list[str] = []
        for header, content in sections:
            content = content.strip()
            if not header:
                # preamble（## 之前的内容）
                if content:
                    parts.append(content)
            else:
                parts.append(f"## {header}\n\n{content}" if content else f"## {header}")
        return "\n\n".join(parts) + "\n"

    # P0: 智能章节合并 — 保留结构，按章节去重合并
    def _merge_body_sections_smart(self, existing: str, new: str) -> str:
        """智能合并章节，保留结构

        策略：
        1. 解析已有和新正文为章节字典
        2. 新章节优先，旧章节中不冲突的部分保留
        3. 同章节合并：按段落去重（Jaccard >= 0.7 跳过）
        """
        # 解析章节为 dict（保留顺序）
        existing_sections = self._parse_sections_to_dict(existing)
        new_sections = self._parse_sections_to_dict(new)

        # 合并：新章节优先，保留旧章节中不冲突的部分
        merged: dict[str, str] = {}
        for title, body in existing_sections.items():
            merged[title] = body
        for title, body in new_sections.items():
            if title in merged:
                merged[title] = self._merge_section_content_smart(merged[title], body)
            else:
                merged[title] = body

        # 按章节顺序重建
        result: list[str] = []
        for title, body in merged.items():
            if body.strip():
                result.append(f"## {title}\n\n{body.strip()}")

        return '\n\n'.join(result)

    @staticmethod
    def _parse_sections_to_dict(text: str) -> dict[str, str]:
        """解析 Markdown 章节为有序字典 {section_title: section_body}"""
        sections: dict[str, str] = {}
        current_title = '概述'
        current_body: list[str] = []

        for line in text.split('\n'):
            m = re.match(r'^##\s+(.+)$', line)
            if m:
                if current_body:
                    sections[current_title] = '\n'.join(current_body)
                current_title = m.group(1).strip()
                current_body = []
            else:
                current_body.append(line)

        if current_body:
            sections[current_title] = '\n'.join(current_body)

        return sections

    def _merge_section_content_smart(self, old: str, new: str) -> str:
        """合并同一章节的内容，按段落去重"""
        old_paras = [p.strip() for p in old.split('\n\n') if p.strip()]
        new_paras = [p.strip() for p in new.split('\n\n') if p.strip()]

        merged = list(old_paras)
        for np_para in new_paras:
            is_dup = False
            for op_para in merged:
                if self._jaccard_similarity(op_para[:100], np_para[:100]) > 0.7:
                    is_dup = True
                    break
            if not is_dup:
                merged.append(np_para)

        return '\n\n'.join(merged)

    @staticmethod
    def _jaccard_similarity(text1: str, text2: str) -> float:
        """计算两个文本的 Jaccard 相似度（基于字符级 bigram）"""
        if not text1 or not text2:
            return 0.0
        set1 = set(text1[i:i+2] for i in range(len(text1) - 1))
        set2 = set(text2[i:i+2] for i in range(len(text2) - 1))
        if not set1 or not set2:
            return 0.0
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _content_equal(a: str, b: str) -> bool:
        return a.strip() == b.strip()

    @staticmethod
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

    @staticmethod
    def _extract_property_lines(body_md: str) -> list[str]:
        """从「## 属性」章节抽 `- **key**: value` 行"""
        lines = body_md.splitlines()
        in_section = False
        out: list[str] = []
        for ln in lines:
            s = ln.strip()
            if s.startswith("## "):
                in_section = s.lower().startswith("## 属性") or s.lower().startswith(
                    "## properties"
                )
                continue
            if in_section and s.startswith("- "):
                out.append(s[2:])
        return out

    # ── Markdown 渲染 ──

    def _render_page_md(self, page: WikiPage, *, is_new: bool) -> str:
        """渲染整页 Markdown（frontmatter + body + OKF Citations）"""
        meta = self._build_frontmatter_meta(page, is_new=is_new)
        # P3-3: 追加 OKF 兼容的 ## Citations 章节
        body_with_citations = self._append_okf_citations(page.body_md, page)
        return self._assemble_md(meta, body_with_citations)

    @staticmethod
    def _append_okf_citations(body: str, page: WikiPage) -> str:
        """P3-3: 在 body 末尾追加 OKF Citations 章节

        OKF 用 # Citations / ## Citations 章节做来源引用，格式 [n] [text](uri)。
        与中文 ## 来源 章节共存（来源是显式引用，Citations 是 OKF 标准化形式）。

        若 body 已含 ## Citations 章节则不重复追加。
        """
        if not page.sources:
            return body
        if "## Citations" in body or "# Citations" in body:
            return body  # 已有，不重复

        lines = ["", "## Citations", ""]
        for i, src in enumerate(page.sources, 1):
            doc_id = src.get("doc_id", "")
            title = src.get("title", doc_id)
            checksum = src.get("checksum", "")
            # OKF resource URI 形式
            uri = f"opskg://doc/{doc_id}" if doc_id else ""
            citation_line = f"[{i}] {title}"
            if uri:
                citation_line += f" ([{doc_id}]({uri}))"
            if checksum:
                citation_line += f"  \n  checksum: `{checksum}`"
            lines.append(citation_line)
        lines.append("")
        return body.rstrip() + "\n" + "\n".join(lines)

    @staticmethod
    def _build_frontmatter_meta(page: WikiPage, *, is_new: bool) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        # P1-1: 生成 OKF v0.1 推荐字段（description/resource/timestamp）
        # 复用 okf_adapter 工具函数，保证编译期与导出期字段语义一致
        from app.knowledge.okf_adapter import (
            derive_resource,
            extract_description,
        )

        meta = {
            "slug": page.slug,
            "title": page.title,
            "type": page.type,
            "tags": page.tags,
            "sources": page.sources,
            "created_at": now if is_new else None,
            "updated_at": now,
            # OKF 推荐字段（编译期生成，导出期无需补全）
            "description": extract_description(page.body_md),
            "resource": derive_resource(
                {"slug": page.slug, "sources": page.sources}
            ),
            "timestamp": now,  # OKF 推荐字段，= updated_at
            "review_status": page.review_status,
            "stale": bool(page.stale_items),
        }
        # 移除空值，避免 frontmatter 噪音
        if not meta["description"]:
            meta.pop("description", None)
        if not meta["resource"]:
            meta.pop("resource", None)
        return meta

    @staticmethod
    def _assemble_md(meta: dict, body: str) -> str:
        """拼装 frontmatter + 正文"""
        # 移除 None 值
        clean = {k: v for k, v in meta.items() if v is not None}
        fm = yaml.safe_dump(clean, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm}\n---\n\n{body.strip()}\n"

    @staticmethod
    def _change_summary(page: WikiPage, outcome: str) -> str:
        if outcome == "created":
            return f"新建 wiki 页面（来源 {page.source_doc_id}）"
        if page.stale_items:
            return f"增量合并并标注 {len(page.stale_items)} 项 stale（来源 {page.source_doc_id}）"
        return f"增量合并（来源 {page.source_doc_id}）"

    # ── S12-2 反向回链 ──

    def _backlink_existing_pages(
        self, new_slug: str, new_title: str, aliases: list[str] | None = None
    ) -> int:
        """新建页面时，扫描已有页面正文，在提及新概念处插入 [[new_slug]]

        实现 AGENTS.md §五 5.b："已有页面中提及新概念时回链到新页面"

        Args:
            new_slug: 新建页面的 slug
            new_title: 新建页面的标题
            aliases: 标题的别名（如英文/缩写），可选

        Returns:
            被更新（插入回链）的已有页面数
        """
        # 收集候选词：标题 + 别名，过滤过短词
        candidates = [new_title] + (aliases or [])
        candidates = [c for c in candidates if c and self._is_meaningful_token(c)]
        # 按长度降序（优先匹配长词，避免短词子串污染）
        candidates.sort(key=len, reverse=True)
        if not candidates:
            return 0

        # 列出所有已有 wiki 页面
        existing_pages = list_wiki_pages(limit=10000)
        # P0: 批量预加载所有已有页面内容（单次 SQL 查询替代 N 次 get_latest）
        all_keys = [
            ep["doc_key"] for ep in existing_pages
            if ep["slug"] != new_slug and ep["slug"] != "index"
        ]
        if not all_keys:
            return 0

        batch_data = self.vc.get_latest_batch(all_keys)
        updated_count = 0

        for page_meta in existing_pages:
            slug = page_meta["slug"]
            # 跳过自身、index
            if slug == new_slug or slug == "index":
                continue

            doc_key = page_meta["doc_key"]
            latest = batch_data.get(doc_key)
            if not latest:
                continue
            original_content = latest["content"]

            # 已有指向 new_slug 的链接 → 跳过
            if f"[[{new_slug}" in original_content:
                continue

            new_content, matched = self._insert_wikilink_in_body(
                original_content, new_slug, candidates
            )
            if not matched:
                continue

            # 保存新版本
            self.vc.save_version(
                doc_key=doc_key,
                title=page_meta["title"],
                content=new_content,
                author="wiki-backlink-bot",
                change_summary=f"反向回链：插入 [[{new_slug}]]",
            )
            # 刷新被修改页面的出链 backlink
            update_backlinks(slug, new_content)
            updated_count += 1
            logger.info(
                "wiki_backlink_inserted",
                source=slug,
                target=new_slug,
            )

        return updated_count

    @staticmethod
    def _is_meaningful_token(text: str) -> bool:
        """判断候选词是否值得建链（避免过短词造成噪音）

        - 含 CJK 字符：长度 >= 2
        - 纯 ASCII：长度 >= 3
        """
        if not text:
            return False
        has_cjk = bool(_CJK_RE.search(text))
        return len(text) >= (2 if has_cjk else 3)

    def _insert_wikilink_in_body(
        self, content: str, new_slug: str, candidates: list[str]
    ) -> tuple[str, bool]:
        """在正文中找到首次提及候选词的位置，替换为 [[new_slug|原文]]

        保护策略：
        - 不动 frontmatter
        - 不动代码块（``` ... ```）
        - 不动已有的 [[wikilink]]（避免嵌套）
        - 不动表格行（避免破坏对齐）
        - 不动 H1 标题行（页面自己的标题）
        - 仅替换整个文档中的首次出现（AGENTS.md "首次提及建链"）
        """
        # 拆分 frontmatter（保留原始 frontmatter 字符串以便重组）
        front, body = self._split_frontmatter_raw(content)

        lines = body.split("\n")
        in_code_block = False
        matched = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            # 代码块开关
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            # 跳过 H1 标题、表格行、frontmatter 边界（保险）
            if stripped.startswith("# ") or stripped.startswith("|") or stripped == "---":
                continue

            for candidate in candidates:
                new_line, did_replace = self._replace_first_outside_wikilink(
                    line, candidate, f"[[{new_slug}|{candidate}]]"
                )
                if did_replace:
                    lines[i] = new_line
                    matched = True
                    break
            if matched:
                break  # 全文仅替换首次出现

        if not matched:
            return content, False

        new_body = "\n".join(lines)
        new_content = (front + new_body) if front else new_body
        return new_content, True

    @staticmethod
    def _split_frontmatter_raw(md: str) -> tuple[str, str]:
        """拆分为 (frontmatter 原始字符串含边界, body)

        - 有 frontmatter：返回 ("---\\n...\\n---\\n\\n", body)
        - 无 frontmatter：返回 ("", md)
        """
        if not md.startswith("---"):
            return "", md
        parts = md.split("---", 2)
        if len(parts) < 3:
            return "", md
        # parts[0] 是空串，parts[1] 是 yaml，parts[2] 是 body
        front = "---" + parts[1] + "---" + "\n"
        body = parts[2].lstrip("\n")
        return front, body

    @staticmethod
    def _replace_first_outside_wikilink(
        line: str, needle: str, replacement: str
    ) -> tuple[str, bool]:
        """在行中替换首次出现的 needle（不在 [[...]] 内），返回新行和是否替换

        英文使用 \\b 词边界，中文直接子串匹配。
        """
        # 收集已有 [[...]] 区间
        blocked: list[tuple[int, int]] = [
            (m.start(), m.end()) for m in WIKILINK_RE.finditer(line)
        ]

        # 构造正则
        if _CJK_RE.search(needle):
            pattern = re.escape(needle)
        else:
            pattern = r"\b" + re.escape(needle) + r"\b"

        for m in re.finditer(pattern, line):
            s, e = m.start(), m.end()
            # 跳过位于已有 wikilink 内的匹配
            if any(bs <= s and e <= be for bs, be in blocked):
                continue
            # 跳过紧邻 | 或 ] 的位置（避免在 wikilink 边界插入）
            if s > 0 and line[s - 1] in "|[":
                continue
            if e < len(line) and line[e] in "|]":
                continue
            new_line = line[:s] + replacement + line[e:]
            return new_line, True
        return line, False

    # ── P3-2: compile_from_sections 辅助方法 ──

    def _group_sections_by_role(
        self, sections: list[Any],
    ) -> dict[str, list[Any]]:
        """按 semantic_role 分组章节"""
        groups: dict[str, list[Any]] = {}
        for s in sections:
            role = getattr(s, 'semantic_role', 'general') or 'general'
            if role not in groups:
                groups[role] = []
            groups[role].append(s)
        return groups

    def _determine_page_type(
        self, role_groups: dict[str, list[Any]],
    ) -> str:
        """根据章节角色确定 Wiki 页面类型"""
        roles = set(role_groups.keys())
        if 'cause' in roles and 'solution' in roles:
            return 'incident'
        if 'steps' in roles:
            return 'runbook'
        if 'config' in roles:
            return 'service'
        if 'overview' in roles:
            return 'concept'
        return 'concept'

    def _determine_slug(
        self, sections: list[Any], page_type: str,
    ) -> str:
        """根据章节确定 Wiki 页面 slug"""
        # 优先使用第一个章节标题的 slug
        if sections:
            title = getattr(sections[0], 'title', '')
            if title:
                slug = re.sub(r'[^\w\s-]', '', title.lower())
                slug = re.sub(r'[-\s]+', '-', slug)
                return slug.strip('-')[:60]
        return f'{page_type}-{hashlib.md5(str(sections).encode()).hexdigest()[:8]}'

    async def _synthesize_wiki_body(
        self,
        role_groups: dict[str, list[Any]],
        page_type: str,
        slug: str,
        sections: list[Any],
    ) -> str:
        """合成 Wiki 页面正文

        尝试 LLM 合成，失败时降级为模板拼接。
        """
        # 模板拼接（兜底）
        return self._synthesize_body_template(role_groups, page_type)

    def _synthesize_body_template(
        self, role_groups: dict[str, list[Any]], page_type: str,
    ) -> str:
        """模板拼接 Wiki 正文"""
        # 按语义角色顺序排列
        role_order = ['overview', 'cause', 'troubleshoot', 'solution', 'config', 'steps', 'warning', 'reference']
        section_titles = {
            'overview': '概述',
            'cause': '成因分析',
            'troubleshoot': '排查步骤',
            'solution': '处置方案',
            'config': '关键配置参数',
            'steps': '操作步骤',
            'warning': '注意事项',
            'reference': '参考',
        }

        parts: list[str] = []
        for role in role_order:
            if role not in role_groups:
                continue
            title = section_titles.get(role, role)
            parts.append(f'## {title}')
            for s in role_groups[role]:
                content = getattr(s, 'content', '')
                if content:
                    parts.append(content)
                parts.append('')

        return '\n\n'.join(parts).strip()

    def _extract_title_from_sections(self, sections: list[Any]) -> str:
        """从章节提取 Wiki 页面标题"""
        for s in sections:
            if getattr(s, 'semantic_role', '') == 'overview':
                title = getattr(s, 'title', '')
                if title:
                    return title
        return getattr(sections[0], 'title', '未命名') if sections else '未命名'

    def _extract_tags_from_sections(self, sections: list[Any]) -> list[str]:
        """从章节提取标签"""
        tags: set[str] = set()
        for s in sections:
            content = getattr(s, 'content', '')
            # 提取技术关键词作为标签
            for kw in ['nginx', 'redis', 'mysql', 'docker', 'k8s', 'kubernetes',
                        'linux', 'network', 'database', 'cache', 'proxy', 'gateway',
                        '502', '503', '504', 'timeout', 'connection', 'error']:
                if kw in content.lower():
                    tags.add(kw)
        return sorted(tags)[:10]

    def _build_sources_from_sections(
        self, sections: list[Any], doc_id: str,
    ) -> list[dict]:
        """构建 sources 列表"""
        seen: set[str] = set()
        sources: list[dict] = []
        for s in sections:
            sid = getattr(s, 'source_doc_id', doc_id)
            if sid and sid not in seen:
                seen.add(sid)
                sources.append({
                    'doc_id': sid,
                    'title': getattr(s, 'title', ''),
                    'sections': [getattr(s, 'section_id', '')],
                })
        return sources

    async def _report_progress(
        self,
        on_progress: ProgressCallback | None,
        step: str,
        message: str,
    ) -> None:
        if on_progress:
            await on_progress(step, message)


# ────────── 全局单例 ──────────

_wc: WikiCompiler | None = None


def get_wiki_compiler() -> WikiCompiler:
    global _wc
    if _wc is None:
        _wc = WikiCompiler()
    return _wc
