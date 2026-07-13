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
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog
import yaml

from app.config import get_settings
from app.core.llm import ChatMessage, get_llm_client
from app.extraction import KnowledgeExtractor
from app.extraction.types import EntityType, ExtractedEntity, ExtractionResult
from app.knowledge.wiki_drift import clear_stale, record_compiled_checksum
from app.knowledge.wiki_index import _key_from_slug, list_wiki_pages, rebuild_index
from app.knowledge.wikilink import WIKILINK_RE, update_backlinks
from app.observability import span
from app.parsers import get_parser
from app.parsers.base import ParsedDocument
from app.storage import get_document_store
from app.storage.version_control import get_version_control

logger = structlog.get_logger()

# ────────── S3: LLM 重试配置 ──────────
_MAX_LLM_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # 秒

# ────────── S2: 模板兜底标记正则 ──────────
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"待\s*(LLM|补全|人工)")

# ────────── S2: 类型必含章节规范 ──────────
_REQUIRED_SECTIONS: dict[str, list[str]] = {
    "entity": ["概述", "属性", "来源"],
    "concept": ["概述", "来源"],
    "incident": ["概述", "排查步骤", "处置方案", "来源"],
    "runbook": ["概述", "排查步骤", "处置方案", "来源"],
    "service": ["概述", "来源"],
    "host": ["概述", "来源"],
}

# ────────── L3: 质量阈值配置 ──────────
_QUALITY_PUBLISH_THRESHOLD = 0.8  # 质量分 ≥ 0.8 自动发布
_QUALITY_REVIEW_THRESHOLD = 0.5   # 质量分 ≥ 0.5 标记 review_needed，< 0.5 拒绝发布

# ────────── M3: 进度事件类型 ──────────


class ProgressEventType(str, Enum):
    """编译进度事件类型"""
    STEP_START = "step_start"
    STEP_DONE = "step_done"
    PAGE_START = "page_start"
    PAGE_DONE = "page_done"
    QUALITY_CHECK = "quality_check"
    CONFLICT_DETECTED = "conflict_detected"
    PROGRESS = "progress"  # 百分比进度


# M3: 进度回调类型
ProgressCallback = Callable[[ProgressEventType, dict[str, Any]], None]

# CJK 字符检测（用于决定匹配策略与最小词长）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# ────────── M1: 相似度检测工具函数 ──────────


def _entity_to_wiki_slugs(name: str) -> list[str]:
    """GS-2: 将图谱实体名称映射为可能的 wiki slug 候选"""
    candidates = [name]
    kebab = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", name).strip("-").lower()
    if kebab and kebab != name:
        candidates.append(kebab)
    for prefix in ["host-", "service-", "component-", "incident-"]:
        if not name.lower().startswith(prefix):
            candidates.append(f"{prefix}{name}")
    return candidates


def _tokenize(text: str) -> list[str]:
    """简单分词：按空格/CJK字符/标点拆分，保留 2+ 字符的 token"""
    tokens: list[str] = []
    # 按非字母数字/CJK 拆分
    parts = re.split(r"[^\w\u4e00-\u9fff]+", text.lower().strip())
    for part in parts:
        if len(part) >= 2:
            tokens.append(part)
        elif _CJK_RE.search(part):
            # CJK 单字符也保留
            tokens.append(part)
    return tokens


def _cosine_similarity(bow1: dict[str, float], bow2: dict[str, float]) -> float:
    """计算两个词袋的余弦相似度"""
    if not bow1 or not bow2:
        return 0.0
    # 交集
    common = set(bow1.keys()) & set(bow2.keys())
    if not common:
        return 0.0
    dot = sum(bow1[k] * bow2[k] for k in common)
    norm1 = sum(v * v for v in bow1.values()) ** 0.5
    norm2 = sum(v * v for v in bow2.values()) ** 0.5
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return round(dot / (norm1 * norm2), 4)


def _parse_json_response(text: str) -> dict | None:
    """从 LLM 响应中提取 JSON 对象"""
    if not text.strip():
        return None
    # 尝试直接解析
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 尝试提取 { ... } 对象
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None

# ────────── EntityType → Wiki 页面类型映射 ──────────
# 见 AGENTS.md §三：entity | concept | incident | runbook | service | host
ENTITY_TYPE_TO_PAGE_TYPE: dict[str, str] = {
    EntityType.HOST.value: "host",
    EntityType.SERVICE.value: "service",
    EntityType.COMPONENT.value: "entity",
    EntityType.PARAMETER.value: "entity",
    EntityType.COMMAND.value: "entity",
    EntityType.PROCEDURE.value: "runbook",
    EntityType.INCIDENT.value: "incident",
    EntityType.SYMPTOM.value: "incident",
    EntityType.EXPERIENCE.value: "concept",
    EntityType.CONCEPT.value: "concept",
    EntityType.DOCUMENT.value: "concept",
}


@dataclass
class WikiPage:
    """编译产出的单个 wiki 页面（未持久化前）"""

    slug: str
    title: str
    type: str  # entity | concept | incident | runbook | service | host
    tags: list[str]
    sources: list[dict]  # [{doc_id, title, checksum}]
    body_md: str  # 不含 frontmatter 的正文
    review_status: str = "auto"  # auto | review_needed | approved
    source_doc_id: str = ""
    stale_items: list[str] = field(default_factory=list)  # 与已有版本冲突的事实
    # S1: 段落分类标签（层级标签），用于页面标签和内容组织
    paragraph_labels: list[str] = field(default_factory=list)


@dataclass
class WikiCompileResult:
    """一次编译任务的汇总结果"""

    doc_id: str
    pages_created: int = 0
    pages_updated: int = 0
    pages_unchanged: int = 0
    slugs: list[str] = field(default_factory=list)
    review_needed: list[str] = field(default_factory=list)
    stale_marked: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    index_rebuilt: bool = False
    # P3-4: 统一编译 — 标记是否同时写入了知识图谱
    graph_compiled: bool = False
    # S1: 段落分类统计
    paragraph_count: int = 0


# ────────── 命名约定（AGENTS.md §五）──────────

_SLUG_SAFE_RE = re.compile(r"[^a-zA-Z0-9\-_]")


def _slugify(name: str) -> str:
    """转 kebab-case slug 安全形式"""
    s = name.strip().lower()
    s = s.replace(" ", "-").replace("_", "-")
    s = _SLUG_SAFE_RE.sub("", s)
    # 合并连续 -
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unnamed"


def make_slug(entity_type: str, name: str) -> str:
    """根据 AGENTS.md 命名约定生成 slug

    - 实体页：{type}-{name}（host/service/component）
    - 故障页：{symptom}-troubleshooting
    - 概念页：直接用概念名
    - Runbook 页：runbook-{scenario}
    """
    page_type = ENTITY_TYPE_TO_PAGE_TYPE.get(entity_type, "concept")
    base = _slugify(name)
    if page_type == "host":
        return f"host-{base}"
    if page_type == "service":
        return f"service-{base}"
    if page_type == "incident":
        # 若 name 已含 troubleshooting 字样则不再追加
        if "troubleshoot" in base or "故障" in name:
            return base
        return f"{base}-troubleshooting"
    if page_type == "runbook":
        return f"runbook-{base}"
    # concept / entity
    return base


def make_hierarchical_slug(
    title: str,
    level: int,
    parent_slug: str | None = None,
    entity_type: str | None = None,
    max_length: int = 100,
) -> str:
    """生成层级化 Slug，反映文档章节结构

    Slug 命名规则：
    - H1：{slugified-title}（文档主标题）
    - H2：{parent-slug}-{section-slug}
    - H3：{grandparent-slug}-{parent-slug}-{section-slug}
    - 截断规则：总长度不超过 max_length，保留关键识别信息
    - 分隔符：连字符 "-"
    - 大小写：全小写

    Args:
        title: 章节标题
        level: 标题层级（1-6）
        parent_slug: 父章节的 slug（可选）
        entity_type: 实体类型（用于确定前缀，可选）
        max_length: slug 最大长度

    Returns:
        层级化 slug 字符串
    """
    base = _slugify(title)
    if not base:
        base = f"section-{level}"

    prefix = ""
    if entity_type:
        page_type = ENTITY_TYPE_TO_PAGE_TYPE.get(entity_type, "")
        if page_type == "host":
            prefix = "host-"
        elif page_type == "service":
            prefix = "service-"
        elif page_type == "runbook":
            prefix = "runbook-"
        elif page_type == "incident":
            if "troubleshoot" not in base and "故障" not in title:
                base += "-troubleshooting"

    if parent_slug:
        parts = parent_slug.split("-")
        if len(parts) > level - 1:
            parent_base = "-".join(parts[: level - 1])
        else:
            parent_base = parent_slug
        candidate = f"{prefix}{parent_base}-{base}" if prefix else f"{parent_base}-{base}"
    else:
        candidate = f"{prefix}{base}" if prefix else base

    if len(candidate) <= max_length:
        return candidate

    truncated_base = base[: max_length - len(prefix) - 1]
    candidate = f"{prefix}{truncated_base}" if prefix else truncated_base
    return candidate.rstrip("-")


def generate_slug_for_heading_tree(
    heading_tree: list[dict],
    parent_slug: str | None = None,
) -> list[dict]:
    """递归为标题树生成层级化 Slug

    Args:
        heading_tree: 标题树字典列表（由 HeadingNode.to_dict() 生成）
        parent_slug: 父章节 slug

    Returns:
        更新后的标题树列表（含 slug 字段）
    """
    result = []
    for node in heading_tree:
        slug = make_hierarchical_slug(
            title=node["title"],
            level=node["level"],
            parent_slug=parent_slug,
        )
        node["slug"] = slug
        if node.get("children"):
            node["children"] = generate_slug_for_heading_tree(
                node["children"],
                parent_slug=slug,
            )
        result.append(node)
    return result


# ────────── 编译器主体 ──────────


class WikiCompiler:
    """把 raw 文档编译为 wiki 页面

    使用方式：
        compiler = get_wiki_compiler()
        result = await compiler.compile_raw_to_wiki(doc_id)
    """

    def __init__(self) -> None:
        self.llm = get_llm_client()
        self.settings = get_settings()
        self.extractor = KnowledgeExtractor()
        self.vc = get_version_control()
        self.store = get_document_store()

    # ── LLM 包装 ──

    async def _llm_complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        """统一 LLM 调用入口（S3: 带重试机制，最多 3 次）"""
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt))
        for attempt in range(1, _MAX_LLM_RETRIES + 1):
            try:
                resp = await self.llm.chat(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=self.settings.llm_max_tokens,
                )
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
            _emit(ProgressEventType.STEP_DONE, {"step": "parse", "elements": len(doc.elements)})
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

            # 3. 逐个编译（实体抽取方式）
            source_entry = {
                "doc_id": doc_id,
                "title": meta.get("title") or meta.get("filename", doc_id),
                "checksum": meta.get("checksum", ""),
            }

            _emit(ProgressEventType.STEP_START, {"step": "compile", "total": len(entities)})
            total_entities = len(entities)
            for i, entity in enumerate(entities):
                if _check_cancel():
                    if task_state is not None:
                        task_state["last_entity_idx"] = i
                        task_state["steps_completed"].append("compile_partial")
                    _emit(ProgressEventType.STEP_DONE, {"step": "cancelled", "message": f"编译已取消（已完成 {i}/{total_entities}）"})
                    return result
                # L1: 断点恢复 — 跳过已处理的实体
                if task_state is not None and i <= task_state.get("last_entity_idx", -1):
                    continue
                try:
                    _emit(ProgressEventType.PAGE_START, {
                        "entity": entity.name,
                        "index": i + 1,
                        "total": total_entities,
                    })
                    _emit(ProgressEventType.PROGRESS, {
                        "percent": round((i + 1) / max(total_entities, 1) * 100),
                        "current": i + 1,
                        "total": total_entities,
                    })
                    # S1: 使用文档级段落分类标签作为页面标签
                    page = await self._compile_entity_page(
                        entity, source_entry, para_labels=doc_labels
                    )
                    if page is None:
                        continue
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
                try:
                    struct_result = await self._compile_heading_tree_to_wiki(
                        doc, source_entry, force=force
                    )
                    result.pages_created += struct_result.pages_created
                    result.pages_updated += struct_result.pages_updated
                    result.pages_unchanged += struct_result.pages_unchanged
                    result.slugs.extend(struct_result.slugs)
                    result.review_needed.extend(struct_result.review_needed)
                    result.stale_marked.extend(struct_result.stale_marked)
                    result.errors.extend(struct_result.errors)
                except Exception as e:
                    logger.exception("wiki_compiler_struct_failed", doc_id=doc_id)
                    result.errors.append(f"结构编译失败: {e}")

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
    ) -> WikiPage | None:
        """把单个实体编译为 wiki 页面

        - 用 LLM 生成正文（按 AGENTS.md 骨架）
        - LLM 不可用时退化为模板化正文（基于 evidence_span）
        - S1: 段落分类标签作为页面标签
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
        body_md = await self._llm_write_body(entity, page_type)
        if not body_md:
            body_md = self._template_body(entity, page_type)

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

        Returns:
            WikiCompileResult
        """
        result = WikiCompileResult(doc_id=doc.doc_id)

        heading_tree_dicts = doc.get_heading_tree_dict()
        if not heading_tree_dicts:
            return result

        tree_with_slugs = generate_slug_for_heading_tree(heading_tree_dicts)

        page_count = await self._compile_tree_node_with_llm(
            tree_with_slugs,
            doc,
            source_entry,
            result,
            force=force,
        )

        logger.info(
            "wiki_compiler_struct_done",
            doc_id=doc.doc_id,
            pages=page_count,
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
    ) -> int:
        """递归编译标题树节点为 wiki 页面（使用 LLM 生成内容）"""
        count = 0
        for node in nodes:
            slug = node.get("slug")
            title = node.get("title", "")
            level = node.get("level", 1)

            if not slug or level > 3:
                if node.get("children"):
                    count += await self._compile_tree_node_with_llm(
                        node["children"], doc, source_entry, result, force=force, parent_slug=slug
                    )
                continue

            try:
                body_md = await self._llm_compile_section(node, parent_slug)
            except Exception as e:
                logger.warning("llm_compile_section_failed", slug=slug, error=str(e))
                body_md = self._build_section_body(node, parent_slug)

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
            except Exception as e:
                logger.exception("wiki_compiler_struct_node_failed", slug=slug)
                result.errors.append(f"{slug}: {e}")

            if node.get("children"):
                count += await self._compile_tree_node_with_llm(
                    node["children"], doc, source_entry, result, force=force, parent_slug=slug
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

        try:
            text = await self._llm_complete(user_prompt, system=system_prompt, temperature=0.2)
            return self._strip_codefence(text).strip()
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

    async def _llm_write_body(self, entity: ExtractedEntity, page_type: str) -> str:
        """让 LLM 按 AGENTS.md 骨架写页面正文

        返回不含 frontmatter 的 Markdown 正文。
        """
        system = (
            "你是 OpsKG Wiki 管理员。把运维知识编译为结构化 Markdown wiki 页面。"
            "严格遵循 AGENTS.md 规定的页面骨架。"
            "使用 [[slug]] 双向链接到相关概念。"
            "只输出 Markdown 正文，不要 YAML frontmatter，不要 ```md 包裹。"
        )
        prompt = self._build_writing_prompt(entity, page_type)
        text = await self._llm_complete(prompt, system=system, temperature=0.2)
        # 防御：剥离可能误加的代码块围栏
        text = self._strip_codefence(text)
        return text.strip()

    def _build_writing_prompt(self, entity: ExtractedEntity, page_type: str) -> str:
        """构造写作 prompt（P3-1: 融合图谱关系作为编译上下文）"""
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

        # P3-1: 查询图谱关系，作为编译上下文注入 prompt
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

    def _template_body(self, entity: ExtractedEntity, page_type: str) -> str:
        """LLM 不可用时的模板化兜底正文"""
        props = entity.properties or {}
        props_lines = (
            "\n".join(f"- **{k}**: {v}" for k, v in props.items() if v)
            or "- （暂无属性）"
        )
        evidence = (entity.evidence_span or "").strip()

        sections: list[str] = [f"# {entity.name}", ""]
        sections.append("## 概述")
        sections.append(
            f"{entity.name} 是一个 {entity.entity_type.lower()} 实体，"
            f"由文档 `{entity.source_doc_id}` 编译而来。"
        )
        sections.append("")

        if page_type in ("entity", "service", "host", "concept"):
            sections.append("## 属性")
            sections.append(props_lines)
            sections.append("")

        if page_type in ("incident", "runbook"):
            sections.append("## 成因分析")
            sections.append(f"> 待补全。原始证据：{evidence[:200] or '（无）'}")
            sections.append("")
            sections.append("## 排查步骤")
            sections.append("1. （待 LLM 重编译补充）")
            sections.append("")
            sections.append("## 处置方案")
            sections.append("- （待 LLM 重编译补充）")
            sections.append("")

        if page_type == "service":
            sections.append("## 依赖")
            sections.append("- （待补全，建议建立 [[wikilink]] 到上下游服务）")
            sections.append("")

        sections.append("## 来源")
        sections.append(f"- doc_id: `{entity.source_doc_id}`")
        sections.append("")
        return "\n".join(sections)

    # ── GS-6: 双向同步 — Wiki 页面保存后同步更新图谱 ──

    def _sync_page_to_graph(self, page: WikiPage) -> None:
        """GS-6: Wiki 页面保存后，将实体信息同步到 Neo4j 知识图谱

        Wiki → Graph 双向同步：
        - 创建/更新对应的 GraphEntity 节点
        - 图不可用时静默降级，不影响编译流程
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
            logger.debug("wiki_graph_sync_skipped", slug=page.slug, error=str(e))

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

        best_slug: str | None = None
        best_score = 0.0

        for ep in existing_pages:
            ep_slug = ep.get("slug", "")
            if not ep_slug or ep_slug == page.slug:
                continue
            # 只比较同类型页面
            if ep.get("type", "") != page.type:
                continue

            try:
                ep_key = _key_from_slug(ep_slug)
                ep_data = self.vc.get_latest(ep_key)
                if not ep_data:
                    continue
                ep_title = ep.get("title", "")
                ep_body = ep_data.get("content", "")
                ep_bow = self._build_bow(ep_title, ep_body)
                score = _cosine_similarity(new_bow, ep_bow)
                if score > best_score:
                    best_score = score
                    best_slug = ep_slug
            except Exception:
                continue

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
        updated_count = 0

        for page_meta in existing_pages:
            slug = page_meta["slug"]
            # 跳过自身、index
            if slug == new_slug or slug == "index":
                continue

            doc_key = page_meta["doc_key"]
            latest = self.vc.get_latest(doc_key)
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


# ────────── 全局单例 ──────────

_wc: WikiCompiler | None = None


def get_wiki_compiler() -> WikiCompiler:
    global _wc
    if _wc is None:
        _wc = WikiCompiler()
    return _wc
