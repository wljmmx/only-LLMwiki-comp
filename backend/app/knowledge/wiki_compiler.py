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
from app.core.llm.concurrency import TaskPriority, get_llm_concurrency_controller
from app.extraction import KnowledgeExtractor
from app.extraction.types import ExtractedEntity, ExtractionResult
from app.knowledge.pipeline_helpers import (
    deserialize_extraction_result,
    deserialize_parsed_doc,
    serialize_compile_result_summary,
    serialize_extraction_result,
    serialize_parsed_doc,
)
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
from app.knowledge.wiki_index import _key_from_slug, _parse_frontmatter, list_wiki_pages, rebuild_index
from app.knowledge.wiki_lint import lint_all_async
from app.knowledge.wikilink import WIKILINK_RE, update_backlinks
from app.observability import record_business_histogram, record_business_metric, span
from app.parsers import get_parser
from app.parsers.base import ParsedDocument
from app.sections.store import SectionStore
from app.storage import get_document_store
from app.storage.pipeline_tracker import get_pipeline_tracker
from app.storage.version_control import get_version_control

logger = structlog.get_logger()

# ────────── 暂停/继续机制 ──────────

# 全局暂停状态：{run_id: asyncio.Event}，Event.set() 表示继续，Event.clear() 表示暂停
_paused_events: dict[str, asyncio.Event] = {}


def pause_compile(run_id: str) -> None:
    """暂停指定编译运行"""
    if run_id not in _paused_events:
        _paused_events[run_id] = asyncio.Event()
    _paused_events[run_id].clear()
    logger.info("compile_paused", run_id=run_id)


def resume_compile(run_id: str) -> None:
    """继续指定编译运行"""
    if run_id in _paused_events:
        _paused_events[run_id].set()
        logger.info("compile_resumed", run_id=run_id)


def cancel_pause(run_id: str) -> None:
    """清理暂停状态（编译完成/取消时调用）"""
    evt = _paused_events.pop(run_id, None)
    if evt is not None:
        evt.set()  # 确保不阻塞
        logger.info("compile_pause_cleaned", run_id=run_id)


async def _check_paused(run_id: str | None, timeout: float = 600.0) -> bool:
    """检查是否暂停，若暂停则等待直到继续。返回 True 表示已暂停等待过。

    修复：旧版 `await evt.wait()` 无超时，若调用 pause 后未 resume
    （如用户关闭浏览器且 SSE 已断），编译协程将永久阻塞，占用 LLM 并发槽。
    现加入超时（默认 600s），超时后自动恢复，避免永久卡死。
    超时也会清理 _paused_events 中的条目，防止内存泄漏。
    """
    if not run_id or run_id not in _paused_events:
        return False
    evt = _paused_events[run_id]
    if evt.is_set():
        return False
    logger.info("compile_waiting_paused", run_id=run_id, timeout=timeout)
    try:
        await asyncio.wait_for(evt.wait(), timeout=timeout)
        logger.info("compile_resumed_after_pause", run_id=run_id)
        return True
    except asyncio.TimeoutError:
        # 超时自动恢复，避免永久阻塞；清理暂停状态
        logger.warning(
            "compile_pause_timeout_auto_resume",
            run_id=run_id, timeout=timeout,
        )
        cancel_pause(run_id)
        return True


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
        self.section_store = SectionStore()
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
        timeout: float | None = None,
    ) -> str:
        """统一 LLM 调用入口（S3: 带重试机制，最多 3 次）

        使用 LLMConcurrencyController 全局限流，防止本地部署过载。
        添加 asyncio.wait_for 超时保护，防止 LLM 调用无限挂起。
        """
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt))

        # 超时配置：优先使用参数，其次用 settings，默认 900s（15分钟，本地大模型推理较慢）
        call_timeout = timeout or getattr(self.settings, 'llm_call_timeout', 900)

        # P2-1: LLM 并发控制
        controller = get_llm_concurrency_controller()
        last_error = ""
        for attempt in range(1, _MAX_LLM_RETRIES + 1):
            try:
                async with controller.acquire(
                    stage="section_compile",
                    priority=TaskPriority.MEDIUM,
                ):
                    # 使用 asyncio.wait_for 包裹 LLM 调用，防止无限挂起
                    resp = await asyncio.wait_for(
                        self.llm.chat(
                            messages=messages,
                            temperature=temperature,
                            max_tokens=self.settings.llm_max_tokens,
                        ),
                        timeout=call_timeout,
                    )
                # ── 编译指标埋点：LLM 调用成功 ──
                try:
                    record_business_metric("llm_calls_total", backend=self.settings.llm_backend, status="success")
                except Exception:  # noqa: BLE001
                    pass
                return resp.text or ""
            except asyncio.TimeoutError:
                last_error = f"LLM 调用超时（{call_timeout}s，第 {attempt}/{_MAX_LLM_RETRIES} 次）"
                logger.warning(
                    "wiki_compiler_llm_timeout",
                    attempt=attempt,
                    timeout=call_timeout,
                )
            except Exception as e:
                last_error = str(e)
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
        # 抛出带详细信息的异常，让调用方能捕获并上报
        raise RuntimeError(f"LLM 调用失败（重试 {_MAX_LLM_RETRIES} 次）：{last_error}")

    async def _llm_complete_stream(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        on_chunk: Any | None = None,
    ) -> str:
        """流式 LLM 调用 — 通过 stream() 方法实时推送 chunk

        Args:
            prompt: 用户 prompt
            system: system message
            temperature: 温度
            on_chunk: chunk 回调 (chunk_text: str) -> None

        Returns:
            完整的 LLM 响应文本
        """
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt))

        controller = get_llm_concurrency_controller()
        last_error = ""
        for attempt in range(1, _MAX_LLM_RETRIES + 1):
            try:
                async with controller.acquire(
                    stage="section_compile",
                    priority=TaskPriority.MEDIUM,
                ):
                    full_text = ""
                    async for chunk in self.llm.stream(
                        messages=messages,
                        temperature=temperature,
                        max_tokens=self.settings.llm_max_tokens,
                    ):
                        if chunk:
                            full_text += chunk
                            if on_chunk:
                                try:
                                    on_chunk(chunk)
                                except Exception:
                                    pass
                return full_text
            except Exception as e:
                last_error = str(e)
                if attempt < _MAX_LLM_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
                else:
                    raise RuntimeError(f"LLM 流式调用失败（重试 {_MAX_LLM_RETRIES} 次）：{last_error}")
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
        # 流水线追踪
        pipeline_run_id: str | None = None,
        start_from_stage: str | None = None,
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
            pipeline_run_id: 指定已存在的 pipeline run ID；
                            None 时自动创建新 run。
            start_from_stage: 从指定阶段开始重处理（parse|extract|compile|index），
                              None 时从 parse 开始。
                              指定 extract/compile/index 时，会从该 run 的
                              上一阶段产物加载输入数据。
        """
        result = WikiCompileResult(doc_id=doc_id)

        # ── 流水线追踪初始化 ──
        tracker = get_pipeline_tracker()
        if pipeline_run_id is None:
            pipeline_run_id = self.store.create_pipeline_run(doc_id)
        try:
            self.store.start_pipeline_run(pipeline_run_id, start_from_stage or "parse")
        except Exception:  # noqa: BLE001
            pass  # pipeline_runs 表可能尚未创建，容错

        def _track(stage: str, direction: str, payload: Any) -> None:
            try:
                tracker.save_artifact(pipeline_run_id, doc_id, stage, direction, payload)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "pipeline_tracker_save_failed",
                    run_id=pipeline_run_id, stage=stage, direction=direction,
                )

        def _update_step(stage: str, status: str, error: str | None = None) -> None:
            try:
                self.store.update_pipeline_step(
                    pipeline_run_id, stage, status, error=error,
                )
            except Exception:  # noqa: BLE001
                pass

        def _finish_run(status: str = "done") -> None:
            # 修复：所有结束路径（done/error/cancelled）都清理暂停事件，
            # 防止 _paused_events 字典在异常路径下泄漏条目。
            try:
                cancel_pause(pipeline_run_id)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.store.finish_pipeline_run(pipeline_run_id, status)
            except Exception:  # noqa: BLE001
                pass

        # 阶段跳过判断
        start_index = 0  # 默认从 parse 开始
        if start_from_stage:
            valid_stages = ["parse", "extract", "compile", "index"]
            if start_from_stage not in valid_stages:
                result.errors.append(f"无效的 start_from_stage: {start_from_stage}")
                _finish_run("error")
                return result
            start_index = valid_stages.index(start_from_stage)

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
                _finish_run("error")
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
                _finish_run("error")
                return result

            # ── 阶段 1: parse ──
            doc: ParsedDocument | None = None
            if start_index <= 0:
                # 真正执行 parse
                _emit(ProgressEventType.STEP_START, {"step": "parse", "message": "开始解析文档..."})
                _update_step("parse", "running")
                _track("parse", "input", {
                    "doc_id": doc_id,
                    "filename": meta.get("filename", ""),
                    "format": meta.get("format", ""),
                    "checksum": meta.get("checksum", ""),
                    "stored_path": meta.get("stored_path", ""),
                    "size_bytes": meta.get("size_bytes", 0),
                })
                try:
                    parser = get_parser(meta["format"])
                    with span(
                        "document.parse",
                        doc_id=doc_id,
                        format=meta.get("format", ""),
                    ):
                        # LLM 段落分类：优先使用异步 aparse，失败降级到同步 parse
                        use_llm_cls = self.settings.paragraph_classify_use_llm
                        if use_llm_cls and hasattr(parser, 'aparse'):
                            try:
                                doc = await parser.aparse(
                                    meta["stored_path"], doc_id,
                                    use_llm_classification=True,
                                )
                            except Exception as parse_err:
                                logger.warning(
                                    "async_parse_fallback_to_sync",
                                    doc_id=doc_id,
                                    error=str(parse_err),
                                )
                                doc = parser.parse(meta["stored_path"], doc_id)
                        else:
                            doc = parser.parse(meta["stored_path"], doc_id)
                    # 发送解析进度事件（逐元素进度）
                    total_elements = len(doc.elements)
                    total_headings = len(doc.heading_tree)
                    _emit(ProgressEventType.PROGRESS, {
                        "percent": 100,
                        "current": total_elements,
                        "total": total_elements,
                        "message": f"解析完成：{total_elements} 个元素，{total_headings} 个章节",
                    })
                    # 逐元素发送进度（为前端提供增量展示）
                    if total_elements > 0:
                        _emit(ProgressEventType.PAGE_START, {
                            "entity": "document_elements",
                            "index": total_elements,
                            "total": total_elements,
                            "message": f"已解析 {total_elements} 个元素",
                        })
                except Exception as e:
                    result.errors.append(f"解析失败: {e}")
                    _emit(ProgressEventType.STEP_DONE, {"step": "parse", "error": str(e)})
                    _update_step("parse", "error", error=str(e))
                    _finish_run("error")
                    return result
                _emit(ProgressEventType.STEP_DONE, {
                    "step": "parse",
                    "elements": len(doc.elements),
                    "heading_tree_count": len(doc.heading_tree),
                    "heading_tree_titles": [
                        {"title": h.get("title", ""), "level": h.get("level", 1)}
                        for h in doc.get_heading_tree_dict()
                    ][:50],
                })
                _update_step("parse", "done")
                _track("parse", "output", serialize_parsed_doc(doc))
                if _check_cancel():
                    _emit(ProgressEventType.STEP_DONE, {"step": "cancelled", "message": "编译已取消"})
                    cancel_pause(pipeline_run_id)
                    _finish_run("cancelled")
                    return result
            else:
                # 从 extract/compile/index 重处理 → 加载 parse 阶段输出
                parse_output = tracker.get_artifact(pipeline_run_id, "parse", "output")
                if not parse_output:
                    result.errors.append(
                        f"无法从 {start_from_stage} 阶段重处理：找不到 parse 阶段的输出产物"
                    )
                    _finish_run("error")
                    return result
                doc = deserialize_parsed_doc(parse_output)
                _emit(ProgressEventType.STEP_DONE, {
                    "step": "parse", "skipped": True,
                    "message": f"从 run {pipeline_run_id} 加载 parse 产物（{len(doc.elements)} 元素）",
                })

            # ── 阶段 2: extract ──
            extraction: ExtractionResult | None = None
            if start_index <= 1:
                _emit(ProgressEventType.STEP_START, {"step": "extract", "message": "开始知识抽取..."})
                _update_step("extract", "running")
                _track("extract", "input", serialize_parsed_doc(doc))
                try:
                    # 传递进度回调到 extract()，实现抽取过程中的实时进度
                    def _extract_progress(etype: str, data: dict) -> None:
                        try:
                            if etype == "progress":
                                _emit(ProgressEventType.PROGRESS, data)
                        except Exception:
                            pass

                    extraction = await self.extractor.extract(doc, on_progress=_extract_progress)
                    total_entities = len(extraction.auto_accepted_entities) + len(extraction.review_entities)

                    # ── 按章节分组实体，发送 per-section 进度事件 ──
                    # 收集所有实体并建立"章节→实体"映射
                    all_entities = list(extraction.auto_accepted_entities) + list(extraction.review_entities)
                    section_map: dict[str, list[ExtractedEntity]] = {}
                    # 从文档的 heading_tree 提取章节名
                    section_names: list[str] = []
                    for node in doc.heading_tree:
                        section_names.append(node.title)
                    # 也收集元素中出现的 section
                    for el in doc.elements:
                        if el.section and el.section not in section_names:
                            section_names.append(el.section)
                    if not section_names:
                        section_names = ["(全文)"]

                    # 基于 evidence_span 将实体映射到章节
                    for ent in all_entities:
                        matched_section = self._match_entity_to_section(ent, doc, section_names)
                        if matched_section not in section_map:
                            section_map[matched_section] = []
                        section_map[matched_section].append(ent)

                    # 为每个实体发送 PAGE_START（带章节信息）+ PAGE_DONE
                    sec_idx = 0
                    for sec_name in section_names:
                        if sec_name not in section_map:
                            continue
                        sec_entities = section_map[sec_name]
                        sec_idx += 1
                        for ent in sec_entities:
                            _emit(ProgressEventType.PAGE_START, {
                                "entity": ent.name,
                                "section": sec_name,
                                "index": sec_idx,
                                "total": len(section_map),
                                "confidence": ent.confidence,
                                "entity_type": ent.entity_type,
                            })
                            _emit(ProgressEventType.PAGE_DONE, {
                                "entity": ent.name,
                                "section": sec_name,
                                "index": sec_idx,
                                "total": len(section_map),
                                "status": "done",
                                "confidence": ent.confidence,
                                "entity_type": ent.entity_type,
                            })

                    _emit(ProgressEventType.PROGRESS, {
                        "percent": 100,
                        "current": total_entities,
                        "total": total_entities,
                        "message": f"抽取完成：{total_entities} 个实体",
                    })
                except Exception as e:
                    result.errors.append(f"抽取失败: {e}")
                    _emit(ProgressEventType.STEP_DONE, {"step": "extract", "error": str(e)})
                    _update_step("extract", "error", error=str(e))
                    _finish_run("error")
                    return result
                _emit(ProgressEventType.STEP_DONE, {
                    "step": "extract",
                    "entities": len(extraction.auto_accepted_entities) + len(extraction.review_entities),
                    "entity_names": [e.name for e in list(extraction.auto_accepted_entities) + list(extraction.review_entities)],
                })
                _update_step("extract", "done")
                _track("extract", "output", serialize_extraction_result(extraction))
                if _check_cancel():
                    _emit(ProgressEventType.STEP_DONE, {"step": "cancelled", "message": "编译已取消"})
                    cancel_pause(pipeline_run_id)
                    _finish_run("cancelled")
                    return result
            else:
                extract_output = tracker.get_artifact(pipeline_run_id, "extract", "output")
                if not extract_output:
                    result.errors.append(
                        f"无法从 {start_from_stage} 阶段重处理：找不到 extract 阶段的输出产物"
                    )
                    _finish_run("error")
                    return result
                extraction = deserialize_extraction_result(extract_output)
                _emit(ProgressEventType.STEP_DONE, {
                    "step": "extract", "skipped": True,
                    "message": f"从 run {pipeline_run_id} 加载 extract 产物（{len(extraction.entities)} 实体）",
                })

            # S1: 段落级 LLM 归类
            _emit(ProgressEventType.STEP_START, {"step": "classify", "message": "段落分类中..."})
            paragraph_classifications: list[dict] = []
            classification_lookup: dict[int, dict] = {}
            try:
                def _classify_progress(batch_idx: int, total_batches: int, done_count: int):
                    _emit(ProgressEventType.PROGRESS, {
                        "step": "classify",
                        "message": f"段落分类批次 {batch_idx}/{total_batches}（已分类 {done_count} 段）",
                        "current": batch_idx,
                        "total": total_batches,
                    })

                paragraph_classifications = await self.extractor.classify_paragraphs(
                    doc, on_progress=_classify_progress,
                )
                result.paragraph_count = len(paragraph_classifications)

                # 统计段落分类信息供前端展示
                label_counts: dict[str, int] = {}
                top_labels: list[dict] = []
                sample_paragraphs: list[dict] = []
                for pc in paragraph_classifications:
                    label = pc.get("label", "未分类")
                    label_counts[label] = label_counts.get(label, 0) + 1
                # 取前 20 个标签按数量降序
                sorted_labels = sorted(label_counts.items(), key=lambda x: x[1], reverse=True)[:20]
                top_labels = [{"label": lb, "count": ct} for lb, ct in sorted_labels]
                # 取前 10 个段落作为示例
                for pc in paragraph_classifications[:10]:
                    sample_paragraphs.append({
                        "index": pc.get("index", 0),
                        "label": pc.get("label", "未分类"),
                        "summary": (pc.get("summary") or "")[:120],
                        "confidence": pc.get("confidence", 0),
                    })

                _emit(ProgressEventType.STEP_DONE, {
                    "step": "classify",
                    "paragraphs": len(paragraph_classifications),
                    "label_count": len(label_counts),
                    "top_labels": top_labels,
                    "sample_paragraphs": sample_paragraphs,
                    "message": f"段落分类完成：{len(paragraph_classifications)} 个段落，{len(label_counts)} 个分类标签",
                })
                logger.info(
                    "paragraph_classification_integrated",
                    doc_id=doc_id,
                    count=result.paragraph_count,
                )

                # 将分类结果写回 doc.elements.metadata，作为后续图谱和 Wiki 编译的基准
                for pc in paragraph_classifications:
                    idx = pc.get("index")
                    if idx is not None and 0 <= idx < len(doc.elements):
                        doc.elements[idx].metadata["classification"] = {
                            "label": pc.get("label", ""),
                            "summary": pc.get("summary", ""),
                            "structured_content": pc.get("structured_content", ""),
                            "confidence": pc.get("confidence", 0),
                        }
                        classification_lookup[idx] = pc
                logger.info(
                    "paragraph_classification_written_to_elements",
                    doc_id=doc_id,
                    written=len(classification_lookup),
                    total_elements=len(doc.elements),
                )
            except Exception as e:
                logger.warning(
                    "paragraph_classification_failed",
                    doc_id=doc_id,
                    error=str(e),
                )
                _emit(ProgressEventType.STEP_DONE, {
                    "step": "classify",
                    "paragraphs": 0,
                    "error": str(e),
                    "message": f"段落分类失败：{e}",
                })
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
                # 无实体时 compile/index 阶段标记为空跑
                if start_index <= 2:
                    _track("compile", "output", serialize_compile_result_summary(result))
                    _update_step("compile", "done")
                if start_index <= 3:
                    _track("index", "output", {"index_rebuilt": False, "slugs": []})
                    _update_step("index", "done")
                _finish_run("done")
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

            # 3. 逐段编译（正确流程：按段落顺序处理，而非按实体）
            source_entry = {
                "doc_id": doc_id,
                "title": meta.get("title") or meta.get("filename", doc_id),
                "checksum": meta.get("checksum", ""),
            }

            # ── 阶段 3: compile — 按段落顺序编译 ──
            # 构建段落→实体的反向映射
            para_entities_map: dict[int, list[dict]] = {}
            for ent in entities:
                para_idx = -1
                if ent.properties:
                    para_idx = ent.properties.get('source_paragraph_index', -1)
                if para_idx >= 0:
                    if para_idx not in para_entities_map:
                        para_entities_map[para_idx] = []
                    para_entities_map[para_idx].append({
                        "name": ent.name,
                        "entity_type": ent.entity_type,
                        "confidence": ent.confidence,
                    })

            # 收集段落分类后的有效段落索引（如果有分类结果）
            classified_indices: set[int] = set()
            if paragraph_classifications:
                for pc in paragraph_classifications:
                    idx = pc.get("index")
                    if idx is not None:
                        classified_indices.add(idx)

            # 收集需要编译的段落（按顺序）
            # 优先使用段落分类结果过滤，确保只编译被分类过的段落
            all_paragraphs: list[dict] = []
            for idx, elem in enumerate(doc.elements or []):
                content = elem.content if hasattr(elem, 'content') else (elem.get('content', '') if isinstance(elem, dict) else '')
                section = elem.section if hasattr(elem, 'section') else (elem.get('section', '') if isinstance(elem, dict) else '')
                elem_type = elem.type.value if hasattr(elem, 'type') and hasattr(elem.type, 'value') else str(elem.type) if hasattr(elem, 'type') else 'paragraph'
                if content and content.strip():
                    # 如果有分类结果，只保留被分类过的段落
                    if classified_indices and idx not in classified_indices:
                        continue
                    all_paragraphs.append({
                        'index': idx,
                        'content': content.strip(),
                        'section': section or '未分类',
                        'type': elem_type,
                    })

            total_paragraphs = len(all_paragraphs)
            logger.info(
                "wiki_compiler_paragraph_selection",
                doc_id=doc_id,
                total_elements=len(doc.elements or []),
                classified_count=len(paragraph_classifications),
                classified_indices_count=len(classified_indices),
                compiled_paragraphs=total_paragraphs,
            )

            if start_index <= 2:
                _emit(ProgressEventType.STEP_START, {
                    "step": "compile",
                    "total": total_paragraphs,
                    "message": f"开始按段落编译，共 {total_paragraphs} 个段落（关联 {len(entities)} 个实体）...",
                })
                _update_step("compile", "running")
                _track("compile", "input", {
                    "parsed_doc": serialize_parsed_doc(doc),
                    "extraction_result": serialize_extraction_result(extraction),
                    "paragraph_labels": doc_labels,
                    "source_entry": source_entry,
                    "total_paragraphs": total_paragraphs,
                    "total_entities": len(entities),
                })

                # ── Phase 1: 逐段顺序编译 ──
                compiled_paragraphs: list[dict] = []
                section_groups: dict[str, list[dict]] = {}

                for idx, para in enumerate(all_paragraphs):
                    if _check_cancel():
                        _emit(ProgressEventType.STEP_DONE, {
                            "step": "cancelled",
                            "message": f"编译已取消（已完成 {idx}/{total_paragraphs}）",
                        })
                        cancel_pause(pipeline_run_id)
                        _finish_run("cancelled")
                        return result

                    await _check_paused(pipeline_run_id)

                    related_ents = para_entities_map.get(para['index'], [])
                    display_name = related_ents[0]['name'] if related_ents else f"段落-{idx+1}"

                    para_chunk_buffer: list[str] = []

                    def _make_para_chunk_cb(pi: int):
                        def _cb(chunk_text: str) -> None:
                            para_chunk_buffer.append(chunk_text)
                            _emit(ProgressEventType.PAGE_CHUNK, {
                                "para_index": pi,
                                "chunk": chunk_text,
                                "buffer_length": sum(len(c) for c in para_chunk_buffer),
                            })
                        return _cb

                    chunk_cb = _make_para_chunk_cb(para['index'])

                    _emit(ProgressEventType.PAGE_START, {
                        "entity": display_name,
                        "index": idx + 1,
                        "total": total_paragraphs,
                        "raw_content": para['content'][:500],
                        "entity_type": para['type'],
                        "section": para['section'],
                        "para_index": idx + 1,
                        "related_entity_count": len(related_ents),
                    })
                    _emit(ProgressEventType.PROGRESS, {
                        "percent": round((idx + 1) / max(total_paragraphs, 1) * 100),
                        "current": idx + 1,
                        "total": total_paragraphs,
                        "step": "compile",
                    })

                    try:
                        # 获取段落分类信息（如果有的话）
                        para_classification = classification_lookup.get(para['index'])

                        compiled = await self._compile_paragraph_page(
                            para_content=para['content'],
                            para_index=para['index'],
                            para_section=para['section'],
                            para_type=para['type'],
                            source_entry=source_entry,
                            related_entities=related_ents,
                            para_classification=para_classification,
                            on_chunk=chunk_cb,
                        )
                        compiled_paragraphs.append(compiled)

                        section = compiled['section']
                        if section not in section_groups:
                            section_groups[section] = []
                        section_groups[section].append(compiled)

                        _emit(ProgressEventType.PAGE_DONE, {
                            "entity": display_name,
                            "index": idx + 1,
                            "total": total_paragraphs,
                            "status": "error" if compiled['llm_error'] else "done",
                            "processing_time_ms": compiled['processing_time_ms'],
                            "llm_error": compiled['llm_error'],
                        })
                        _emit(ProgressEventType.PAGE_COMPLETE, {
                            "entity": display_name,
                            "raw_content": compiled['raw_content'],
                            "compiled_content": compiled['compiled_content'],
                            "compiled_chars": compiled['compiled_chars'],
                            "processing_time_ms": compiled['processing_time_ms'],
                            "llm_error": compiled['llm_error'],
                            "section": para['section'],
                            "related_entity_count": len(related_ents),
                            "para_index": idx + 1,
                        })

                    except Exception as e:
                        logger.exception("wiki_compiler_paragraph_failed", para_index=para['index'])
                        result.errors.append(f"段落 {para['index']} 编译失败: {e}")
                        _emit(ProgressEventType.PAGE_DONE, {
                            "entity": display_name,
                            "index": idx + 1,
                            "total": total_paragraphs,
                            "status": "error",
                            "error": str(e),
                        })

                # ── Phase 2: 按章节分组生成 Wiki 页面 ──
                for section, comp_results in section_groups.items():
                    if not comp_results:
                        continue
                    section_content_parts = [cr['compiled_content'] for cr in comp_results]
                    section_body = "\n\n".join(section_content_parts)

                    section_slug = _slugify(section) or f"section-{hash(section) % 10000}"
                    if not section_slug:
                        section_slug = f"section-{len(result.slugs) + 1}"

                    page = WikiPage(
                        slug=section_slug,
                        title=section,
                        type="concept",
                        tags=["section", _slugify(section)[:20]],
                        sources=[source_entry],
                        body_md=section_body,
                        review_status="auto",
                        llm_error=None,
                        source_doc_id=source_entry.get("doc_id", ""),
                        paragraph_labels=[],
                    )
                    try:
                        outcome = self._save_page(page, force=force)
                        self._sync_page_to_graph(page)
                        result.slugs.append(page.slug)
                        if outcome == "created":
                            result.pages_created += 1
                        elif outcome == "updated":
                            result.pages_updated += 1
                        else:
                            result.pages_unchanged += 1
                        # 记录段落→页面的贡献关系（P1: SectionContribution 持久化）
                        # 用于反向追溯：给定 wiki 页面，查询其内容由哪些段落贡献
                        try:
                            section_id = f"{doc.doc_id}:{section_slug}"
                            source_doc_id = source_entry.get("doc_id", doc.doc_id)
                            for cr in comp_results:
                                para_idx = cr.get("para_index")
                                if para_idx is not None:
                                    para_section_id = f"{section_id}:p{para_idx}"
                                    self.section_store.add_contribution(
                                        section_id=para_section_id,
                                        source_doc_id=source_doc_id,
                                        target_type="wiki_page",
                                        target_slug=page.slug,
                                        contribution_type="primary",
                                        compiled_version=1,
                                    )
                        except Exception as contrib_err:  # noqa: BLE001
                            logger.warning(
                                "section_contribution_record_failed",
                                slug=page.slug, error=str(contrib_err),
                            )
                    except Exception as e:
                        logger.exception("wiki_compiler_section_save_failed", slug=section_slug)
                        result.errors.append(f"章节 {section} 保存失败: {e}")

                # ── Phase 3: 保留实体级 Wiki 页面编译（实体浏览入口） ──
                for entity in entities:
                    try:
                        page = await self._compile_entity_page(
                            entity, source_entry,
                            para_labels=doc_labels,
                            relations_map=None,
                        )
                        if page:
                            outcome = self._save_page(page, force=force)
                            self._sync_page_to_graph(page)
                            result.slugs.append(page.slug)
                            if outcome == "created":
                                result.pages_created += 1
                            elif outcome == "updated":
                                result.pages_updated += 1
                            else:
                                result.pages_unchanged += 1
                            if page.review_status == "review_needed":
                                result.review_needed.append(page.slug)
                    except Exception as e:
                        result.errors.append(f"{entity.name}: {e}")

                _track("compile", "output", serialize_compile_result_summary(result))
                # 注意：不在此标记 compile 为 done，因为 struct_compile 和 extract_compiled
                # 仍是 compile 阶段的子步骤，必须等它们完成后再标记

                _emit(ProgressEventType.STEP_DONE, {
                    "step": "compile",
                    "paragraphs_processed": total_paragraphs,
                    "entities_processed": len(entities),
                    "pages": len(result.slugs),
                    "pages_created": result.pages_created,
                    "pages_updated": result.pages_updated,
                    "message": f"段落编译完成：{total_paragraphs} 段落 → {len(result.slugs)} 页面",
                })

                # ── 4. 结构编译（增量集成到已有 Wiki 知识库） ──
                # 核心：将本次新增 wiki 页面逐个向已有 wiki 目录环境进行融合
                #  - 判断新增分支节点（已有目录不存在 → 创建）
                #  - 在已有节点修改/补充/合并（已有目录存在相似页面 → 融合）
                #  - 补充双向关联：wiki ↔ graph ↔ 实体 ↔ 原始文档

                struct_compile_error = None
                integration_report: dict[str, Any] = {
                    "pages_created": 0,
                    "pages_merged": 0,
                    "pages_updated": 0,
                    "pages_unchanged": 0,
                    "graph_relations_added": 0,
                    "review_needed": [],
                    "stale_marked": [],
                    "errors": [],
                    "pages_integrated": len(result.slugs),
                    "integration_detail": [],
                }

                _emit(ProgressEventType.STEP_START, {
                    "step": "struct_compile",
                    "message": f"开始结构集成，将 {len(result.slugs)} 个新页面融合到已有 Wiki 目录...",
                })
                _update_step("struct_compile", "running")

                try:
                    struct_result = WikiCompileResult(doc_id=doc.doc_id)
                    integration_report = await self._integrate_pages_into_wiki(
                        new_slugs=list(result.slugs),
                        source_entry=source_entry,
                        entities=entities,
                        para_entities_map=para_entities_map,
                        doc=doc,
                        force=force,
                        on_progress=_emit,
                        pipeline_run_id=pipeline_run_id,
                    )

                    # 汇总结构编译结果
                    result.pages_created += integration_report.get("pages_created", 0)
                    result.pages_updated += integration_report.get("pages_updated", 0)
                    result.pages_unchanged += integration_report.get("pages_unchanged", 0)
                    result.review_needed.extend(integration_report.get("review_needed", []))
                    result.stale_marked.extend(integration_report.get("stale_marked", []))
                    result.errors.extend(integration_report.get("errors", []))

                    # 记录结构集成事件
                    _track("struct_compile", "output", integration_report)
                    _update_step("struct_compile", "done")

                    _emit(ProgressEventType.STEP_DONE, {
                        "step": "struct_compile",
                        "pages_integrated": integration_report.get("pages_integrated", 0),
                        "pages_created": integration_report.get("pages_created", 0),
                        "pages_merged": integration_report.get("pages_merged", 0),
                        "pages_updated": integration_report.get("pages_updated", 0),
                        "graph_relations_added": integration_report.get("graph_relations_added", 0),
                        "message": (
                            f"结构集成完成：新增 {integration_report.get('pages_created', 0)} 页面，"
                            f"合并 {integration_report.get('pages_merged', 0)} 页面，"
                            f"更新 {integration_report.get('pages_updated', 0)} 页面，"
                            f"图谱关联 {integration_report.get('graph_relations_added', 0)} 条"
                        ),
                    })

                except Exception as e:
                    logger.exception("wiki_compiler_struct_compile_failed", doc_id=doc_id)
                    struct_compile_error = str(e)
                    result.errors.append(f"结构集成失败: {e}")
                    _track("struct_compile", "output", {"error": struct_compile_error})
                    _update_step("struct_compile", "error", error=struct_compile_error)
                    _emit(ProgressEventType.STEP_DONE, {
                        "step": "struct_compile",
                        "error": struct_compile_error,
                        "message": f"结构集成失败：{e}",
                    })

                # 4.5 从编译后内容重新抽取实体（独立运行，不依赖 struct_compile 成功）
                # 使用 result.slugs（包含 compile 阶段产出的 + struct_compile 产出的）
                if result.slugs:
                    _emit(ProgressEventType.STEP_START, {"step": "extract_compiled", "message": "从编译后 wiki 页面重新抽取实体..."})
                    _update_step("extract_compiled", "running")
                    try:
                        compiled_entities = await self._extract_from_compiled_pages(
                            result.slugs, doc.doc_id, source_entry, _emit,
                        )
                        if compiled_entities:
                            # 合并编译后抽取的实体（优先保留编译后抽取的结果）
                            existing_entity_names = {e.name for e in result.entities}
                            new_count = 0
                            for ce in compiled_entities:
                                if ce.name not in existing_entity_names:
                                    result.entities.append(ce)
                                    new_count += 1
                            _track("extract_compiled", "output", {
                                "entities": len(compiled_entities),
                                "new_entities": new_count,
                                "entity_names": [e.name for e in compiled_entities],
                            })
                            _update_step("extract_compiled", "done")
                            _emit(ProgressEventType.STEP_DONE, {
                                "step": "extract_compiled",
                                "entities": len(compiled_entities),
                                "new_entities": new_count,
                                "entity_names": [e.name for e in compiled_entities],
                                "message": f"编译后抽取：{len(compiled_entities)} 个实体（{new_count} 个新增）",
                            })
                        else:
                            _track("extract_compiled", "output", {"entities": 0, "new_entities": 0})
                            _update_step("extract_compiled", "done")
                            _emit(ProgressEventType.STEP_DONE, {"step": "extract_compiled", "entities": 0})
                    except Exception as e:
                        logger.exception("wiki_compiler_extract_compiled_failed", doc_id=doc_id)
                        _track("extract_compiled", "output", {"error": str(e)})
                        _update_step("extract_compiled", "error", error=str(e))
                        _emit(ProgressEventType.STEP_DONE, {"step": "extract_compiled", "error": str(e)})
                else:
                    # 无 slugs 时也标记 extract_compiled 为跳过
                    _track("extract_compiled", "output", {"skipped": True, "reason": "无编译产物"})
                    _update_step("extract_compiled", "done")
                    _emit(ProgressEventType.STEP_DONE, {"step": "extract_compiled", "entities": 0, "skipped": True, "message": "无编译产物，跳过编译后实体抽取"})

                # 注意：compile 的 _track/_update_step 在阶段 4 (index) 之前统一执行，
                # 避免在 struct_compile/extract_compiled 完成前过早标记为 done
            else:
                # start_from_stage == "index" — 加载 compile 输出以获取 slugs
                compile_output = tracker.get_artifact(pipeline_run_id, "compile", "output")
                if compile_output:
                    result.slugs = list(compile_output.get("slugs", []))
                    result.pages_created = compile_output.get("pages_created", 0)
                    result.pages_updated = compile_output.get("pages_updated", 0)
                    result.pages_unchanged = compile_output.get("pages_unchanged", 0)
                    result.review_needed = list(compile_output.get("review_needed", []))
                    result.stale_marked = list(compile_output.get("stale_marked", []))
                    result.errors = list(compile_output.get("errors", []))
                _emit(ProgressEventType.STEP_DONE, {
                    "step": "compile", "skipped": True,
                    "message": f"从 run {pipeline_run_id} 加载 compile 产物（{len(result.slugs)} 个页面）",
                })

            # 4. 状态推进
            self.store.update_status(doc_id, "compiled")

            # 5. 记录编译时 checksum（供 P1-1 漂移检测使用），清除已重编译页面的 stale
            try:
                record_compiled_checksum(doc_id, meta.get("checksum", ""))
                for slug in result.slugs:
                    clear_stale(slug)
            except Exception as e:
                result.errors.append(f"checksum/stale 同步失败: {e}")

            # compile 阶段输出已就绪，记录产物（在 index 之前）
            if start_index <= 2:
                _track("compile", "output", serialize_compile_result_summary(result))
                _update_step("compile", "done")

            # ── 阶段 4: index ──
            if start_index <= 3:
                _update_step("index", "running")
                _track("index", "input", {
                    "pages_created": result.pages_created,
                    "pages_updated": result.pages_updated,
                    "slugs": list(result.slugs),
                })

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

            # index 阶段输出
            if start_index <= 3:
                _track("index", "output", {
                    "index_rebuilt": result.index_rebuilt,
                    "slugs": list(result.slugs),
                })
                _update_step("index", "done")
                _emit(ProgressEventType.STEP_DONE, {
                    "step": "index",
                    "index_rebuilt": result.index_rebuilt,
                    "slugs_count": len(result.slugs),
                })

            # ── 阶段 5: lint（健康检查）──
            # 流水线末尾自动检测矛盾/stale/orphan/missing concept/deadlink/okf 违规
            # 与 AGENTS.md §7 Lint Workflow 对齐
            if start_index <= 3:
                _update_step("lint", "running")
                _emit(ProgressEventType.STEP_START, {
                    "step": "lint",
                    "message": "开始健康检查（矛盾/过时/孤岛/缺失概念/死链/OKF 合规）...",
                })
                _track("lint", "input", {"slugs": list(result.slugs)})

                lint_error: str | None = None
                lint_issues_count = 0
                lint_by_type: dict[str, int] = {}
                lint_review_pushed = 0
                try:
                    # 仅运行 regex 检测（include_semantic=False），
                    # 避免 LLM 语义检测拖慢流水线；语义检测由独立定时任务触发
                    lint_report = await lint_all_async(
                        include_stale=True,
                        include_semantic=False,
                    )
                    lint_issues_count = len(lint_report.issues)
                    for issue in lint_report.issues:
                        lint_by_type[issue.type] = lint_by_type.get(issue.type, 0) + 1
                    # 关键问题（矛盾/缺失概念/OKF 违规）计入审查推送数
                    review_types = {
                        "contradiction",
                        "contradiction_semantic",
                        "missing_concept",
                        "missing_concept_from_graph",
                        "okf_violation",
                    }
                    lint_review_pushed = sum(
                        c for t, c in lint_by_type.items() if t in review_types
                    )
                    logger.info(
                        "wiki_lint_done",
                        doc_id=doc_id,
                        issues=lint_issues_count,
                        by_type=lint_by_type,
                    )
                except Exception as e:  # noqa: BLE001
                    lint_error = str(e)
                    logger.warning("wiki_lint_failed", doc_id=doc_id, error=lint_error)

                _track("lint", "output", {
                    "issues_count": lint_issues_count,
                    "by_type": lint_by_type,
                    "review_pushed": lint_review_pushed,
                    "error": lint_error,
                })
                _update_step("lint", "done")
                _emit(ProgressEventType.STEP_DONE, {
                    "step": "lint",
                    "issues_count": lint_issues_count,
                    "by_type": lint_by_type,
                    "review_pushed": lint_review_pushed,
                    "error": lint_error,
                })

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
                "step": "compile_summary",
                "pages_created": result.pages_created,
                "pages_updated": result.pages_updated,
                "pages_unchanged": result.pages_unchanged,
                "review_needed": len(result.review_needed),
                "errors": len(result.errors),
                "slugs": list(result.slugs),
            })
            # 流水线追踪完成
            _finish_run("done")
            # 清理暂停状态
            cancel_pause(pipeline_run_id)

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
        on_chunk: Any | None = None,
    ) -> WikiPage | None:
        """把单个实体编译为 wiki 页面（支持流式 chunk 回调）

        - 用 LLM 生成正文（按 AGENTS.md 骨架）
        - LLM 不可用时退化为模板化正文（基于 evidence_span）
        - S1: 段落分类标签作为页面标签
        - relations_map: P1 (K4) 预取的关系映射
        - on_chunk: 可选流式回调 (chunk_text: str) -> None
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

        # 调 LLM 写正文 —— 带流式回调
        llm_error: str | None = None
        try:
            body_md = await self._llm_write_body(
                entity, page_type,
                relations_map=relations_map,
                on_chunk=on_chunk,
            )
        except Exception as e:
            llm_error = str(e)
            logger.warning("wiki_compiler_llm_write_failed", entity=entity.name, error=str(e))
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
            llm_error=llm_error,
            source_doc_id=source_entry.get("doc_id", ""),
            paragraph_labels=para_labels or [],
        )

    # ── 单段落编译（正确流程：按段落顺序处理，而非按实体） ──

    async def _compile_paragraph_page(
        self,
        para_content: str,
        para_index: int,
        para_section: str = "",
        para_type: str = "paragraph",
        source_entry: dict | None = None,
        related_entities: list[dict] | None = None,
        para_classification: dict | None = None,
        on_chunk: Any | None = None,
    ) -> dict:
        """将单个段落编译为 wiki 结构化内容。

        正确流程：
        - 逐段处理（doc.elements 顺序），不管段落是否有关联实体
        - 将段落内容作为原始内容传入 LLM
        - LLM 生成 wiki 结构化内容（含 [[wikilink]]）
        - 段落分类信息（para_classification）用于增强编译上下文
        - 返回编译结果，用于对比视图和后续 wiki 页面生成

        Returns:
            dict {
                "para_index": int,
                "section": str,
                "raw_content": str,
                "compiled_content": str,
                "llm_error": str | None,
                "processing_time_ms": int,
            }
        """
        t_start = time.monotonic()
        llm_error: str | None = None

        # 调用 LLM 将段落内容编译为 wiki 结构
        try:
            compiled_content = await self._llm_compile_paragraph(
                para_content=para_content,
                para_section=para_section,
                para_type=para_type,
                related_entities=related_entities,
                para_classification=para_classification,
                on_chunk=on_chunk,
            )
        except Exception as e:
            llm_error = str(e)
            logger.warning("wiki_compiler_paragraph_llm_failed",
                           para_index=para_index, error=str(e))
            compiled_content = ""

        # LLM 失败时的兜底：使用段落原文
        if not compiled_content:
            compiled_content = self._build_paragraph_fallback(
                para_content, para_section, para_type,
            )

        t_end = time.monotonic()
        return {
            "para_index": para_index,
            "section": para_section,
            "raw_content": para_content[:2000],
            "compiled_content": compiled_content[:3000],
            "compiled_chars": len(compiled_content),
            "llm_error": llm_error,
            "processing_time_ms": int((t_end - t_start) * 1000),
        }

    async def _llm_compile_paragraph(
        self,
        para_content: str,
        para_section: str,
        para_type: str,
        related_entities: list[dict] | None,
        para_classification: dict | None,
        on_chunk: Any | None,
    ) -> str:
        """调用 LLM 将单段内容编译为 wiki 结构化 Markdown。"""
        from app.core.llm import ChatMessage

        # 构建实体上下文
        entity_context = ""
        if related_entities:
            entity_lines = []
            for ent in related_entities[:10]:
                ent_name = ent.get("name", "")
                ent_type = ent.get("entity_type", "Concept")
                if ent_name:
                    entity_lines.append(f"- {ent_name}（类型：{ent_type}）")
            if entity_lines:
                entity_context = "\n\n本段涉及的实体：\n" + "\n".join(entity_lines)

        # 构建分类上下文
        classification_context = ""
        if para_classification:
            cls_label = para_classification.get("label", "")
            cls_summary = para_classification.get("summary", "")
            cls_confidence = para_classification.get("confidence", 0)
            if cls_label:
                classification_context += f"\n\n段落分类：{cls_label}"
            if cls_summary:
                classification_context += f"\n段落摘要：{cls_summary}"
            if cls_confidence:
                classification_context += f"\n分类置信度：{cls_confidence:.0%}"

        section_hint = f"（所属章节：{para_section}）" if para_section else ""

        system_prompt = f"""你是 Wiki 编译器。将给定的段落内容编译为结构化 Markdown Wiki 内容。

要求：
1. 保留原始信息，按 Wiki 结构重新组织
2. 识别并标注关键实体和概念，使用 [[wikilink]] 语法（如 [[entity-name]]）
3. 添加适当的小标题和分层
4. 保持内容的专业性和可读性
5. 直接输出编译后的 Markdown 内容，不要加前言或解释

段落类型：{para_type}{section_hint}
{classification_context}
{entity_context}"""

        user_message = f"""请将以下段落内容编译为 Wiki 结构化 Markdown：

---段落开始---
{para_content}
---段落结束---

编译要求：
- 保留所有原始信息，不要遗漏
- 识别并标注关键术语和实体为 [[wikilink]]
- 添加合适的小节标题
- 如果内容包含操作步骤，使用有序列表
- 如果内容包含定义或说明，使用清晰的段落结构"""

        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_message),
        ]

        response = await self.llm.chat(
            messages=messages,
            temperature=0.3,
            max_tokens=2000,
        )

        result = response.text.strip()

        # 处理流式回调（模拟 chunk 输出）
        if on_chunk and result:
            chunk_size = max(50, len(result) // 5)
            for i in range(0, len(result), chunk_size):
                chunk = result[i:i + chunk_size]
                on_chunk(chunk)

        return result

    def _build_paragraph_fallback(
        self,
        para_content: str,
        para_section: str,
        para_type: str,
    ) -> str:
        """LLM 不可用时的段落编译兜底：使用模板化结构。"""
        escaped = para_content.replace('```', '`\'\'`')

        # 根据段落类型选择模板
        if para_type in ('heading', 'header'):
            level = 2
            return f"## {para_content}\n\n> 本节内容待 LLM 增强\n\n<!-- LLM fallback: heading -->"

        if '步骤' in para_content or '步骤' in para_section:
            lines = para_content.split('\n')
            items = [l.strip() for l in lines if l.strip()]
            numbered = "\n".join(f"{i+1}. {item}" for i, item in enumerate(items[:20]))
            return f"### {para_section or '操作步骤'}\n\n{numbered}\n\n<!-- LLM fallback: procedural -->"

        if any(kw in para_content for kw in ['配置', '参数', '命令', '语法']):
            return f"""### {para_section or '配置说明'}

```
{escaped}
```

<!-- LLM fallback: configuration -->"""

        # 默认模板：概念段落
        section_title = para_section or '内容说明'
        return f"""### {section_title}

{para_content}

<!-- LLM fallback: paragraph -->"""

    # ── 结构编译：增量集成到已有 Wiki 知识库 ──

    async def _integrate_pages_into_wiki(
        self,
        new_slugs: list[str],
        source_entry: dict,
        entities: list,
        para_entities_map: dict[int, list[dict]],
        doc: ParsedDocument,
        *,
        force: bool = False,
        on_progress: Any | None = None,
        pipeline_run_id: str | None = None,
    ) -> dict:
        """将本次新增 wiki 页面增量集成到已有 Wiki 知识库。

        核心流程：
        1. 加载已有 Wiki 目录（所有现存页面的元数据索引）
        2. 对每个新页面逐个匹配已有目录：
           - slug 精确匹配 → 更新已有节点（补充/修改内容）
           - 标题/类型模糊匹配 → 合并到已有节点（融合内容）
           - 无匹配 → 创建新分支节点
        3. 补充双向关联：wiki ↔ graph ↔ 实体 ↔ 原始文档
        4. 产出集成报告

        Returns:
            dict {pages_created, pages_merged, pages_updated,
                  graph_relations_added, review_needed, stale_marked, errors}
        """
        report: dict[str, Any] = {
            "pages_created": 0,
            "pages_merged": 0,
            "pages_updated": 0,
            "pages_unchanged": 0,
            "graph_relations_added": 0,
            "review_needed": [],
            "stale_marked": [],
            "errors": [],
            "pages_integrated": len(new_slugs),
            "integration_detail": [],
        }

        # ── Phase 1: 加载已有 Wiki 目录索引 ──
        existing_index = self._load_existing_wiki_index()
        existing_slugs = set(existing_index.keys())

        logger.info(
            "struct_compile_integration_start",
            new_pages=len(new_slugs),
            existing_pages=len(existing_slugs),
        )

        # ── Phase 2: 逐页融合 ──
        for idx, slug in enumerate(new_slugs):
            if pipeline_run_id:
                await _check_paused(pipeline_run_id)

            # 从版本控制加载新页面
            new_page = self._load_wiki_page_from_storage(slug)
            if not new_page:
                report["errors"].append(f"页面 {slug} 加载失败")
                continue

            action_detail = {
                "slug": slug,
                "title": new_page.title,
                "action": "unchanged",
                "matched_existing": None,
                "note": "",
            }

            # ── 匹配决策 ──
            matched_existing = self._match_existing_page(
                new_page, existing_index, entities,
            )

            if matched_existing is None:
                # 新增分支节点：已有目录不存在此页面
                action = "created"
                report["pages_created"] += 1
                action_detail["action"] = "created"
                action_detail["note"] = "新增分支节点"

                # 确保页面已正确保存（compile 阶段已保存，这里确保图谱同步）
                self._sync_page_to_graph(new_page)
                report["graph_relations_added"] += self._update_graph_relations(
                    new_page, entities, source_entry, mode="create",
                )

            else:
                # 融合到已有节点
                existing_slug = matched_existing["slug"]
                action_detail["matched_existing"] = existing_slug

                similarity = matched_existing.get("similarity", 0)

                if similarity >= 0.85:
                    # 高度相似 → 更新（补充/修改）
                    action = "updated"
                    report["pages_updated"] += 1
                    action_detail["action"] = "updated"
                    action_detail["note"] = f"更新已有节点（相似度 {similarity:.0%}）"

                    self._merge_page_into_existing(
                        existing_slug, new_page, mode="update", force=force,
                    )
                    report["graph_relations_added"] += self._update_graph_relations(
                        new_page, entities, source_entry, mode="merge",
                        existing_slug=existing_slug,
                    )

                else:
                    # 中度相似 → 合并（融合内容）
                    action = "merged"
                    report["pages_merged"] += 1
                    action_detail["action"] = "merged"
                    action_detail["note"] = f"合并到已有节点（相似度 {similarity:.0%}）"

                    self._merge_page_into_existing(
                        existing_slug, new_page, mode="merge", force=force,
                    )
                    report["graph_relations_added"] += self._update_graph_relations(
                        new_page, entities, source_entry, mode="merge",
                        existing_slug=existing_slug,
                    )

                    # 标记新页面为 stale（已合并到已有节点）
                    report["stale_marked"].append(slug)

            # ── 发射页面集成事件 ──
            if on_progress:
                try:
                    on_progress(ProgressEventType.PAGE_DONE, {
                        "entity": new_page.title,
                        "slug": slug,
                        "action": action_detail["action"],
                        "matched_existing": matched_existing["slug"] if matched_existing else None,
                        "index": idx + 1,
                        "total": len(new_slugs),
                        "status": "done",
                    })
                except Exception:
                    pass

            report["integration_detail"].append(action_detail)

        # ── Phase 3: 构建目录树 ──
        try:
            tree_info = self._build_directory_tree(
                existing_index, new_slugs, doc, entities, para_entities_map,
            )
            report["directory_tree"] = tree_info
            logger.info(
                "struct_compile_directory_built",
                tree_nodes=len(tree_info.get("nodes", [])),
            )
        except Exception as e:
            logger.exception("struct_compile_directory_failed")
            report["errors"].append(f"目录树构建失败: {e}")

        # ── Phase 4: 重建索引 ──
        try:
            from app.knowledge.wiki_index import rebuild_index
            index_result = rebuild_index()
            report["index_rebuilt"] = True
            report["index_details"] = index_result
        except Exception as e:
            logger.warning("struct_compile_index_rebuild_failed", error=str(e))
            report["index_rebuilt"] = False

        logger.info(
            "struct_compile_integration_done",
            pages_created=report["pages_created"],
            pages_merged=report["pages_merged"],
            pages_updated=report["pages_updated"],
            graph_relations=report["graph_relations_added"],
        )
        return report

    def _load_existing_wiki_index(self) -> dict[str, dict]:
        """加载已有 Wiki 目录索引（slug → 元数据）"""
        from app.knowledge.wiki_index import list_wiki_pages

        try:
            pages = list_wiki_pages(limit=5000)
            index: dict[str, dict] = {}
            for p in pages:
                slug = p["slug"]
                index[slug] = {
                    "slug": slug,
                    "title": p["title"],
                    "type": p["type"],
                    "tags": p.get("tags", []),
                    "updated_at": p.get("updated_at"),
                    "doc_key": p.get("doc_key"),
                }
            return index
        except Exception as e:
            logger.warning("wiki_index_load_failed", error=str(e))
            return {}

    def _load_wiki_page_from_storage(self, slug: str) -> WikiPage | None:
        """从版本控制加载 Wiki 页面"""
        try:
            doc_key = _key_from_slug(slug)
            latest = self.vc.get_latest(doc_key)
            if not latest:
                return None

            meta, body = _parse_frontmatter(latest["content"])
            return WikiPage(
                slug=slug,
                title=meta.get("title", slug),
                type=meta.get("type", "concept"),
                tags=meta.get("tags", []),
                sources=meta.get("sources", []),
                body_md=body,
                review_status=meta.get("review_status", "auto"),
                llm_error=None,
                source_doc_id=meta.get("source_doc_id", ""),
                paragraph_labels=meta.get("paragraph_labels", []),
            )
        except Exception as e:
            logger.warning("wiki_page_load_failed", slug=slug, error=str(e))
            return None

    def _match_existing_page(
        self,
        new_page: WikiPage,
        existing_index: dict[str, dict],
        entities: list,
    ) -> dict | None:
        """判断新页面与已有页面的匹配关系。

        匹配策略：
        1. slug 精确匹配 → 直接返回
        2. 标题完全匹配（同 type） → 直接返回
        3. 标题相似度匹配 → 返回最高相似度的已有页面
        4. 实体关联匹配 → 查找包含相同实体的已有页面

        Returns:
            匹配的已有页面信息 dict（含 similarity），或 None（无匹配）
        """
        # 策略 1: slug 精确匹配
        if new_page.slug in existing_index:
            return {
                "slug": new_page.slug,
                "title": existing_index[new_page.slug]["title"],
                "similarity": 1.0,
                "reason": "slug_exact_match",
            }

        # 策略 2: 标题完全匹配
        for slug, meta in existing_index.items():
            if meta["title"] == new_page.title and meta["type"] == new_page.type:
                return {
                    "slug": slug,
                    "title": meta["title"],
                    "similarity": 0.95,
                    "reason": "title_exact_match",
                }

        # 策略 3: 标题相似度（简单子串 + 长度比例）
        best_match: dict | None = None
        for slug, meta in existing_index.items():
            title_sim = self._compute_title_similarity(new_page.title, meta["title"])
            if title_sim > 0.6:
                if best_match is None or title_sim > best_match["similarity"]:
                    best_match = {
                        "slug": slug,
                        "title": meta["title"],
                        "similarity": title_sim,
                        "reason": "title_similarity",
                    }

        # 策略 4: 实体关联匹配（检查新页面关联的实体是否已存在于某已有页面）
        if entities:
            new_page_entity_names = set()
            for ent in entities:
                if hasattr(ent, 'name') and ent.name:
                    if ent.name in new_page.body_md:
                        new_page_entity_names.add(ent.name)

            if new_page_entity_names:
                for slug, meta in existing_index.items():
                    if meta["type"] != new_page.type:
                        continue
                    # 检查已有页面是否包含相同实体
                    if self._page_contains_entities(slug, new_page_entity_names):
                        if best_match is None or 0.7 > best_match["similarity"]:
                            best_match = {
                                "slug": slug,
                                "title": meta["title"],
                                "similarity": 0.7,
                                "reason": "entity_overlap",
                            }
                            break

        return best_match

    def _compute_title_similarity(self, title_a: str, title_b: str) -> float:
        """计算两个标题的相似度（基于字符集交集）"""
        if not title_a or not title_b:
            return 0.0
        set_a = set(title_a.lower())
        set_b = set(title_b.lower())
        intersection = set_a & set_b
        union = set_a | set_b
        if not union:
            return 0.0
        return len(intersection) / len(union)

    def _page_contains_entities(self, slug: str, entity_names: set[str]) -> bool:
        """检查已有页面是否包含指定实体名"""
        try:
            doc_key = _key_from_slug(slug)
            latest = self.vc.get_latest(doc_key)
            if not latest:
                return False
            content = latest["content"].lower()
            count = sum(1 for name in entity_names if name.lower() in content)
            return count >= max(1, len(entity_names) // 2)
        except Exception:
            return False

    def _merge_page_into_existing(
        self,
        existing_slug: str,
        new_page: WikiPage,
        *,
        mode: str = "update",
        force: bool = False,
    ) -> None:
        """将新页面内容合并到已有页面。

        Args:
            existing_slug: 已有页面的 slug
            new_page: 新页面
            mode: "update"(补充) 或 "merge"(融合)
        """
        try:
            # 加载已有页面
            existing_page = self._load_wiki_page_from_storage(existing_slug)
            if not existing_page:
                logger.warning("merge_target_not_found", slug=existing_slug)
                return

            # ── 权威性评分（V2.1 §7.3 冲突仲裁）──
            # Authority = w_source*SourceWeight + w_recency*Recency + w_consensus*Consensus
            # 评分高的版本优先；差值<0.15 时不自动覆盖，保留双方内容
            existing_auth = self._compute_authority_score(existing_page)
            new_auth = self._compute_authority_score(new_page)
            auth_diff = new_auth - existing_auth
            # 新页面权威性显著更高时，覆盖已有内容
            new_authoritative = auth_diff >= 0.15

            # 合并内容
            if mode == "update":
                if new_authoritative:
                    # 新内容权威性更高：以新内容为主，追加已有页面中独有的段落
                    merged_body = self._merge_content_update(
                        new_page.body_md, existing_page.body_md,
                    )
                else:
                    # 补充模式：检查新内容段落是否在已有页面中不存在
                    merged_body = self._merge_content_update(
                        existing_page.body_md, new_page.body_md,
                    )
            else:
                # 融合模式：将两个版本的内容都保留
                merged_body = self._merge_content_fusion(
                    existing_page.body_md, new_page.body_md, new_page.title,
                )

            # 合并 sources
            existing_sources = existing_page.sources or []
            new_sources = new_page.sources or []
            merged_sources = list(existing_sources)
            for src in new_sources:
                if src not in merged_sources:
                    merged_sources.append(src)

            # 合并 tags
            merged_tags = list(set(
                (existing_page.tags or []) + (new_page.tags or [])
            ))

            # 更新已有页面
            updated_page = WikiPage(
                slug=existing_page.slug,
                title=existing_page.title,
                type=existing_page.type,
                tags=merged_tags,
                sources=merged_sources,
                body_md=merged_body,
                review_status="auto",
                llm_error=None,
                source_doc_id=existing_page.source_doc_id or new_page.source_doc_id,
                paragraph_labels=existing_page.paragraph_labels or new_page.paragraph_labels,
            )

            outcome = self._save_page(updated_page, force=force)
            self._sync_page_to_graph(updated_page)

            logger.info(
                "wiki_page_merged",
                target_slug=existing_slug,
                source_slug=new_page.slug,
                mode=mode,
                outcome=outcome,
                merged_chars=len(merged_body),
                existing_authority=round(existing_auth, 3),
                new_authority=round(new_auth, 3),
                new_authoritative=new_authoritative,
            )

        except Exception as e:
            logger.exception("wiki_page_merge_failed", slug=existing_slug)

    def _compute_authority_score(self, page: WikiPage) -> float:
        """计算页面权威性评分（V2.1 §7.3）

        Authority = w_source*SourceWeight + w_recency*Recency + w_consensus*Consensus

        - SourceWeight: 基于来源数量推断（多来源=更可信，单来源=0.7，无来源=0.4）
        - Recency: 基于页面 updated_at 的新近度（越新越接近 1.0）
        - Consensus: 基于来源去重数量占比（来源越多共识越高）
        """
        s = self.settings
        w_src = s.authority_source_weight
        w_rec = s.authority_recency_weight
        w_con = s.authority_consensus_weight

        # SourceWeight
        src_count = len(page.sources or [])
        if src_count >= 3:
            source_weight = 1.0
        elif src_count >= 1:
            source_weight = 0.7
        else:
            source_weight = 0.4

        # Recency（基于 source_doc_id 的 checksum 无法获取时间，用 sources 数量近似）
        # 简化：新编译页面（本次流水线产出）默认 Recency=1.0
        # 已有页面 Recency 通过 version_control 的 updated_at 计算
        try:
            from app.knowledge.wiki_index import _key_from_slug
            latest = self.vc.get_latest(_key_from_slug(page.slug))
            if latest and latest.get("updated_at"):
                # 解析时间戳，计算距今的小时数，24h内=1.0，7天=0.5，30天=0.2
                from datetime import datetime, timezone as _tz
                updated_str = latest["updated_at"]
                # 兼容多种时间格式
                try:
                    updated_dt = datetime.fromisoformat(
                        updated_str.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    updated_dt = None
                if updated_dt is not None:
                    now = datetime.now(_tz.utc)
                    age_hours = (now - updated_dt).total_seconds() / 3600
                    if age_hours <= 24:
                        recency = 1.0
                    elif age_hours <= 168:  # 7天
                        recency = 0.5
                    elif age_hours <= 720:  # 30天
                        recency = 0.2
                    else:
                        recency = 0.1
                else:
                    recency = 0.5
            else:
                recency = 1.0  # 新页面无历史记录，视为最新
        except Exception:
            recency = 0.5

        # Consensus（来源去重占比）
        consensus = min(1.0, src_count / 3.0) if src_count > 0 else 0.0

        return w_src * source_weight + w_rec * recency + w_con * consensus

    def _merge_content_update(self, existing: str, new: str) -> str:
        """补充模式：将新内容中已有页面不存在的段落追加进去"""
        existing_paragraphs = [
            p.strip() for p in existing.split("\n\n") if p.strip()
        ]
        new_paragraphs = [
            p.strip() for p in new.split("\n\n") if p.strip()
        ]

        existing_text = existing.lower()
        appended: list[str] = []
        for para in new_paragraphs:
            # 检查该段落是否已在已有内容中
            if para.lower() not in existing_text and len(para) > 20:
                appended.append(para)

        if appended:
            return existing + "\n\n## 补充内容\n\n" + "\n\n".join(appended)
        return existing

    def _merge_content_fusion(self, existing: str, new: str, new_title: str) -> str:
        """融合模式：保留两个版本的内容，添加来源标注"""
        fused = f"""{existing}

---

## 更新内容（来自本次编译：{new_title}）

{new}

<!-- 融合来源：已有页面 + 新编译页面 -->"""
        return fused

    def _update_graph_relations(
        self,
        page: WikiPage,
        entities: list,
        source_entry: dict,
        *,
        mode: str = "create",
        existing_slug: str | None = None,
    ) -> int:
        """更新图谱双向关联关系。

        Returns:
            新增的图谱关系数量
        """
        relations_added = 0

        try:
            from app.knowledge.graph_store import GraphEntity, GraphRelation, get_graph_store

            store = get_graph_store()

            # 1. 确保页面节点在图谱中
            page_entity = GraphEntity(
                entity_type=page.type,
                name=page.title,
                properties={
                    "slug": page.slug,
                    "tags": page.tags,
                    "review_status": page.review_status,
                    "source_doc_id": page.source_doc_id,
                    "is_wiki_page": True,
                },
                source_doc_id=source_entry.get("doc_id", ""),
                confidence=1.0,
            )
            store.upsert_entity(page_entity)
            relations_added += 1

            # 2. 建立实体 → 页面的双向关联
            for ent in entities:
                if hasattr(ent, 'name') and ent.name:
                    try:
                        graph_ent = GraphEntity(
                            entity_type=getattr(ent, 'entity_type', 'Concept'),
                            name=ent.name,
                            properties={
                                "slug": getattr(ent, 'slug', ''),
                                "confidence": getattr(ent, 'confidence', 0.8),
                            },
                            source_doc_id=source_entry.get("doc_id", ""),
                            confidence=getattr(ent, 'confidence', 0.8),
                        )
                        store.upsert_entity(graph_ent)
                        relations_added += 1

                        # has_entity 关系
                        rel = GraphRelation(
                            relation_type="has_entity",
                            from_entity=page.slug,
                            to_entity=ent.name,
                            properties={"source": "struct_compile"},
                            source_doc_id=source_entry.get("doc_id", ""),
                            confidence=0.9,
                        )
                        store.upsert_relation(rel)
                        relations_added += 1
                    except Exception:
                        pass

            # 3. 合并关联
            if existing_slug and existing_slug != page.slug:
                try:
                    rel = GraphRelation(
                        relation_type="merged_from",
                        from_entity=existing_slug,
                        to_entity=page.slug,
                        properties={"mode": mode},
                        source_doc_id=source_entry.get("doc_id", ""),
                        confidence=1.0,
                    )
                    store.upsert_relation(rel)
                    relations_added += 1
                except Exception:
                    pass

            # 4. 页面 → 源文档关联
            if source_entry.get("doc_id"):
                try:
                    rel = GraphRelation(
                        relation_type="sourced_from",
                        from_entity=page.slug,
                        to_entity=source_entry["doc_id"],
                        properties={"source_type": "original_document"},
                        source_doc_id=source_entry.get("doc_id", ""),
                        confidence=1.0,
                    )
                    store.upsert_relation(rel)
                    relations_added += 1
                except Exception:
                    pass

        except Exception as e:
            logger.warning(
                "graph_relation_update_failed",
                slug=page.slug,
                mode=mode,
                error=str(e),
            )

        return relations_added

    def _build_directory_tree(
        self,
        existing_index: dict[str, dict],
        new_slugs: list[str],
        doc: ParsedDocument,
        entities: list,
        para_entities_map: dict[int, list[dict]],
    ) -> dict:
        """构建目录树：基于标题树骨架 + 已有 wiki 页面 + 新页面

        Returns:
            {"nodes": [...], "total_pages": int, "tree_depth": int}
        """
        tree_nodes: list[dict] = []
        heading_tree = doc.heading_tree or []

        # 构建实体名 → 页面slug映射
        entity_to_slug: dict[str, str] = {}
        for slug in new_slugs:
            page = self._load_wiki_page_from_storage(slug)
            if page:
                # 查找该页面包含的实体
                for ent in entities:
                    if hasattr(ent, 'name') and ent.name in page.body_md:
                        entity_to_slug[ent.name] = slug

        # 遍历标题树构建目录
        def _build_node(heading_node, depth: int = 0) -> dict:
            node = {
                "title": heading_node.title,
                "level": heading_node.level,
                "slug": heading_node.slug,
                "depth": depth,
                "wiki_pages": [],
                "child_sections": [],
                "entities": [],
            }

            # 查找匹配的 wiki 页面
            heading_slug = heading_node.slug or _slugify(heading_node.title)
            if heading_slug and heading_slug in existing_index:
                node["wiki_pages"].append({
                    "slug": heading_slug,
                    "title": existing_index[heading_slug]["title"],
                    "type": existing_index[heading_slug]["type"],
                })
            elif heading_slug and heading_slug in new_slugs:
                node["wiki_pages"].append({
                    "slug": heading_slug,
                    "title": heading_node.title,
                    "type": "concept",
                    "is_new": True,
                })

            # 查找该章节涉及的实体
            for ent in entities:
                if hasattr(ent, 'source_section_id') and ent.source_section_id:
                    if heading_node.title in ent.source_section_id or ent.source_section_id in heading_node.title:
                        node["entities"].append({
                            "name": ent.name,
                            "type": ent.entity_type,
                            "wiki_slug": entity_to_slug.get(ent.name, ""),
                        })

            # 递归处理子节点
            if hasattr(heading_node, 'children') and heading_node.children:
                for child in heading_node.children:
                    node["child_sections"].append(
                        _build_node(child, depth + 1)
                    )

            return node

        for h in heading_tree:
            tree_nodes.append(_build_node(h))

        # 如果没有标题树，基于新页面构建扁平目录
        if not tree_nodes and new_slugs:
            for slug in new_slugs:
                page = self._load_wiki_page_from_storage(slug)
                if page:
                    tree_nodes.append({
                        "title": page.title,
                        "level": 1,
                        "slug": slug,
                        "depth": 0,
                        "wiki_pages": [{
                            "slug": slug,
                            "title": page.title,
                            "type": page.type,
                            "is_new": True,
                        }],
                        "child_sections": [],
                        "entities": [],
                    })

        return {
            "nodes": tree_nodes,
            "total_pages": len(new_slugs),
            "tree_depth": max(
                (n.get("depth", 0) for n in tree_nodes),
                default=0,
            ),
            "entity_slug_map": entity_to_slug,
        }

    async def _extract_from_compiled_pages(
        self,
        slugs: list[str],
        doc_id: str,
        source_entry: dict,
        on_progress: ProgressCallback | None = None,
    ) -> list[ExtractedEntity]:
        """从编译后的 wiki 页面中抽取实体

        在 struct_compile 完成后调用，从 LLM 编译后的结构化 Markdown 中
        抽取实体，比从原始文本抽取更准确（编译后内容更干净、结构化）。

        使用 compiled_extractor 从已保存的 wiki 页面中提取实体。
        """
        if not slugs:
            return []

        from app.extraction.compiled_extractor import CompiledKnowledgeExtractor  # noqa: F401

        vc = get_version_control()
        entities: list[ExtractedEntity] = []
        seen_names: set[str] = set()

        def _emit(etype: ProgressEventType, data: dict[str, Any]) -> None:
            if on_progress:
                try:
                    on_progress(etype, data)
                except Exception:
                    pass

        for i, slug in enumerate(slugs):
            try:
                latest = vc.get_latest(f"wiki:{slug}")
                if not latest:
                    continue

                content = latest.get("content", "")
                title = latest.get("title", slug)

                # 从编译后的 wiki 页面内容中提取实体
                # 使用标题作为实体名，类型从 frontmatter 解析
                meta, body = self._split_frontmatter(content) if hasattr(self, '_split_frontmatter') else ({}, content)
                page_type = meta.get("type", "concept")

                # 基于标题树层级提取实体
                entity = ExtractedEntity(
                    entity_type=page_type.capitalize() if page_type != "concept" else "Concept",
                    name=title,
                    properties={
                        "slug": slug,
                        "tags": meta.get("tags", []),
                        "source_doc_id": doc_id,
                    },
                    confidence=0.85,
                    evidence_span=body[:200] if body else "",
                    source_doc_id=doc_id,
                )
                if title not in seen_names:
                    seen_names.add(title)
                    entities.append(entity)

                # 从 wiki 页面正文中提取 [[wikilink]] 引用的实体
                wikilink_entities = self._extract_wikilink_entities(body, doc_id)
                for we in wikilink_entities:
                    if we.name not in seen_names:
                        seen_names.add(we.name)
                        entities.append(we)

                _emit(ProgressEventType.PROGRESS, {
                    "percent": round((i + 1) / len(slugs) * 100),
                    "current": i + 1,
                    "total": len(slugs),
                    "message": f"抽取实体: {slug}",
                })
            except Exception as e:
                logger.warning("extract_compiled_page_failed", slug=slug, error=str(e))

        logger.info(
            "extract_compiled_done",
            doc_id=doc_id,
            total_pages=len(slugs),
            entities=len(entities),
        )
        return entities

    def _extract_wikilink_entities(self, body_md: str, doc_id: str) -> list[ExtractedEntity]:
        """从 wiki 页面正文中提取 [[wikilink]] 引用为实体"""
        entities: list[ExtractedEntity] = []
        matches = WIKILINK_RE.findall(body_md)
        for match in matches:
            slug = match[0] if isinstance(match, tuple) else match
            if slug and not slug.startswith("#"):
                name = slug.replace("-", " ").title()
                entities.append(ExtractedEntity(
                    entity_type="Concept",
                    name=name,
                    properties={"slug": slug, "source_doc_id": doc_id},
                    confidence=0.7,
                    evidence_span=f"引用: [[{slug}]]",
                    source_doc_id=doc_id,
                ))
        return entities

    async def _compile_heading_tree_to_wiki(
        self,
        doc: ParsedDocument,
        source_entry: dict,
        *,
        force: bool = False,
        on_progress: ProgressCallback | None = None,
        pipeline_run_id: str | None = None,
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
            pipeline_run_id=pipeline_run_id,
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
        pipeline_run_id: str | None = None,
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

        # 在并行 LLM 编译前，先发射所有 section_start 事件
        # 这样前端能立即看到所有章节节点，而不是等 LLM 全部完成后才批量出现
        for node in compile_nodes:
            slug = node.get("slug")
            title = node.get("title", "")
            level = node.get("level", 1)
            section_idx = node_section_indices.get(slug, _section_index[0])
            _emit(ProgressEventType.SECTION_START, {
                "slug": slug,
                "title": title,
                "level": level,
                "index": section_idx,
                "total": total_sections,
                "children_count": len(node.get("children", [])),
            })

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
                    # 暂停检查（递归子节点前）
                    await _check_paused(pipeline_run_id)
                    child_count = await self._compile_tree_node_with_llm(
                        node["children"], doc, source_entry, result, force=force, parent_slug=slug, trace_buffer=trace_buffer,
                        on_section_progress=on_section_progress,
                        on_progress=on_progress,
                        total_sections=total_sections,
                        _section_index=_section_index,
                        pipeline_run_id=pipeline_run_id,
                    )
                    count += child_count
                continue

            # 暂停检查（处理每个章节节点前）
            await _check_paused(pipeline_run_id)

            section_idx = node_section_indices.get(slug, _section_index[0])

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
                    "raw_content": raw_content[:2000],
                    "compiled_content": body_md[:3000],
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
                # 暂停检查（递归子节点前）
                await _check_paused(pipeline_run_id)
                count += await self._compile_tree_node_with_llm(
                    node["children"], doc, source_entry, result, force=force, parent_slug=slug, trace_buffer=trace_buffer,
                    pipeline_run_id=pipeline_run_id,
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

    async def _llm_write_body(
        self,
        entity: ExtractedEntity,
        page_type: str,
        relations_map: dict[str, str] | None = None,
        on_chunk: Any | None = None,
    ) -> str:
        """让 LLM 按 AGENTS.md 骨架写页面正文（支持流式 chunk 回调）

        Args:
            entity: 抽取实体
            page_type: wiki 页面类型
            relations_map: P1 (K4) 预取的关系映射
            on_chunk: 可选回调，每次收到 LLM chunk 时调用 (chunk_text: str) -> None

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
            result = self._llm_cache[cache_key]
            if on_chunk:
                try:
                    on_chunk(result)
                except Exception:
                    pass
            return result

        logger.info("llm_cache_miss", method="write_body", entity=entity.name, type=entity.entity_type)
        system = (
            "你是 OpsKG Wiki 管理员。把运维知识编译为结构化 Markdown wiki 页面。"
            "严格遵循 AGENTS.md 规定的页面骨架。"
            "使用 [[slug]] 双向链接到相关概念。"
            "只输出 Markdown 正文，不要 YAML frontmatter，不要 ```md 包裹。"
        )
        prompt = self._build_writing_prompt(entity, page_type, relations_map=relations_map)

        # 流式输出模式
        if on_chunk:
            text = await self._llm_complete_stream(prompt, system=system, temperature=0.2, on_chunk=on_chunk)
        else:
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

    # ── 富化 raw_content：直接使用已在抽取阶段绑定的段落内容 ──

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

    @staticmethod
    def _match_entity_to_section(
        ent: "ExtractedEntity",
        doc: "ParsedDocument",
        section_names: list[str],
    ) -> str:
        """将抽取的实体匹配到文档中的章节

        策略：基于实体的 evidence_span 或 name 与章节内容进行模糊匹配。
        如果无法匹配，归入第一个章节或"(未分类)"。
        """
        evidence = ent.evidence_span or ""
        ent_name = ent.name or ""

        # 收集每个章节的内容摘要（用于匹配）
        section_content: dict[str, str] = {}
        for node in doc.heading_tree:
            section_content[node.title] = "\n".join(
                e.content for e in node.elements if e.content
            )
        for el in doc.elements:
            if el.section:
                content = section_content.get(el.section, "")
                if el.content:
                    section_content[el.section] = content + "\n" + el.content

        best_section = ""
        best_score = 0

        for sec_name in section_names:
            sec_content = section_content.get(sec_name, "")
            if not sec_content:
                continue
            # 评分：evidence_span 在章节内容中的匹配度
            score = 0
            if evidence and evidence in sec_content:
                score += len(evidence)  # 精确匹配给高分
            if ent_name and ent_name in sec_content:
                score += len(ent_name) * 0.5  # 实体名匹配给中等分
            # 子章节名匹配
            if sec_name in evidence or sec_name in ent_name:
                score += len(sec_name) * 0.3

            if score > best_score:
                best_score = score
                best_section = sec_name

        if best_section:
            return best_section
        return section_names[0] if section_names else "(未分类)"

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
