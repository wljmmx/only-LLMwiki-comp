"""Wiki 编译器类型定义 — 数据类、枚举、配置常量

从 wiki_compiler.py 提取的独立类型模块，供 wiki_compiler.py /
wiki_compiler_utils.py / 外部消费者共用。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.extraction.types import EntityType

# ────────── S3: LLM 重试配置 ──────────
LLM_MAX_RETRIES: int = 3
LLM_RETRY_BASE_DELAY: float = 1.0  # 秒

# 向后兼容旧名称（内部使用）
_MAX_LLM_RETRIES = LLM_MAX_RETRIES
_RETRY_BASE_DELAY = LLM_RETRY_BASE_DELAY

# ────────── S2: 模板兜底标记正则 ──────────
TEMPLATE_PLACEHOLDER_RE = re.compile(r"待\s*(LLM|补全|人工)")
_TEMPLATE_PLACEHOLDER_RE = TEMPLATE_PLACEHOLDER_RE

# ────────── S2: 类型必含章节规范 ──────────
REQUIRED_SECTIONS: dict[str, list[str]] = {
    "entity": ["概述", "属性", "来源"],
    "concept": ["概述", "来源"],
    "incident": ["概述", "排查步骤", "处置方案", "来源"],
    "runbook": ["概述", "排查步骤", "处置方案", "来源"],
    "service": ["概述", "来源"],
    "host": ["概述", "来源"],
}
_REQUIRED_SECTIONS = REQUIRED_SECTIONS

# ────────── L3: 质量阈值配置 ──────────
QUALITY_PUBLISH_THRESHOLD: float = 0.8   # 质量分 ≥ 0.8 自动发布
QUALITY_REVIEW_THRESHOLD: float = 0.5    # 质量分 ≥ 0.5 标记 review_needed，< 0.5 拒绝发布
_QUALITY_PUBLISH_THRESHOLD = QUALITY_PUBLISH_THRESHOLD
_QUALITY_REVIEW_THRESHOLD = QUALITY_REVIEW_THRESHOLD

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
    SECTION_PROGRESS = "section_progress"  # 章节处理进度
    SECTION_START = "section_start"  # 单个章节处理开始
    SECTION_DONE = "section_done"    # 单个章节处理完成


# M3: 进度回调类型
ProgressCallback = Callable[[ProgressEventType, dict[str, Any]], None]

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
    # Pipeline trace: 章节级管道追踪
    pipeline_trace: PipelineTrace | None = None


@dataclass
class SectionTrace:
    """单个章节的 LLM 处理追踪记录"""

    title: str                          # 章节标题
    level: int                          # 标题层级
    slug: str                           # 生成的 slug
    raw_content: str                    # 处理前原始内容
    raw_chars: int                      # 原始字符数
    compiled_content: str               # LLM 编译后内容
    compiled_chars: int                 # 编译后字符数
    llm_success: bool                   # LLM 是否成功
    processing_time_ms: float           # 处理耗时(ms)
    children_count: int                 # 子章节数


@dataclass
class PipelineTrace:
    """一次编译的完整管道追踪"""

    doc_id: str
    doc_title: str
    duration_ms: float
    sections: list[SectionTrace] = field(default_factory=list)
    total_raw_chars: int = 0
    total_compiled_chars: int = 0
    total_sections: int = 0
    sections_with_children: int = 0
    llm_success_count: int = 0
    llm_fail_count: int = 0
