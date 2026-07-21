"""章节拆分器

从 ParsedDocument 按 heading_tree 拆分为独立的 Section。
每个 Section 是独立的加工单元，分别进入 LLM 编译。

核心策略:
1. 有标题树 → 按 heading_tree 递归拆分
2. 无标题树 → 使用 inferred_structure 拆分
3. 仍然无结构 → 使用段落语义分类拆分
4. 每条拆分都保留来源文档、父章节、附件引用、代码块等上下文
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.parsers.markdown_parser import ParsedDocument, ParsedElement
from app.parsers.text_cleaner import CleanedDocument


@dataclass
class Section:
    """文档章节

    独立的加工单元，每个 Section 对应文档中的一个语义段落。
    """
    section_id: str          # "sec_{doc_id}_{idx}"
    source_doc_id: str       # 来源文档 ID
    parent_section_id: str | None  # 父章节 ID（层级关系）
    title: str               # 章节标题
    level: int               # 标题层级 1-6
    semantic_role: str       # 语义角色 (overview/cause/troubleshoot/solution/config/steps/warning)
    raw_content: str         # 原始文本内容
    index: int               # 在原文档中的序号
    # 上下文
    context_before: str      # 前一个章节的标题
    context_after: str       # 后一个章节的标题
    # 附件引用（关联到 attachment_index 表）
    attachment_refs: list[dict] = field(default_factory=list)
    # 代码块（关联到代码块分类）
    code_blocks: list[dict] = field(default_factory=list)
    # 交叉引用
    cross_refs: list[dict] = field(default_factory=list)
    # 编译状态
    compiled_version: int = 0
    compiled_at: str | None = None
    # 元数据
    metadata: dict = field(default_factory=dict)


@dataclass
class SectionSplitResult:
    """章节拆分结果"""
    sections: list[Section]
    orphan_content: str       # 未被分配到任何章节的内容（如文档开头的摘要）
    total_count: int
    stats: dict = field(default_factory=dict)


class SectionSplitter:
    """章节拆分器

    将 ParsedDocument 拆分为独立的 Section 列表。
    支持三种拆分策略：
    1. 标题树拆分（优先）
    2. 推断结构拆分（无标题时）
    3. 段落分类拆分（完全无结构时）
    """

    def __init__(self):
        self._counter = 0

    def split(
        self,
        doc: ParsedDocument,
        cleaned: CleanedDocument | None = None,
    ) -> SectionSplitResult:
        """拆分文档为章节

        Args:
            doc: 解析后的文档
            cleaned: 清洗后的文档（可选，用于获取附件引用和代码块分类）

        Returns:
            SectionSplitResult
        """
        self._counter = 0

        # 策略 1: 标题树拆分
        if doc.heading_tree:
            sections = self._split_by_headings(doc, cleaned)
            if sections:
                return SectionSplitResult(
                    sections=sections,
                    orphan_content='',
                    total_count=len(sections),
                    stats={'strategy': 'heading_tree', 'count': len(sections)},
                )

        # 策略 2: 推断结构拆分
        if cleaned and cleaned.inferred_headings:
            sections = self._split_by_inferred(doc, cleaned)
            if sections:
                return SectionSplitResult(
                    sections=sections,
                    orphan_content='',
                    total_count=len(sections),
                    stats={'strategy': 'inferred_structure', 'count': len(sections)},
                )

        # 策略 3: 段落分类拆分
        if cleaned and cleaned.paragraph_classes:
            sections = self._split_by_classes(doc, cleaned)
            return SectionSplitResult(
                sections=sections,
                orphan_content='',
                total_count=len(sections),
                stats={'strategy': 'paragraph_classes', 'count': len(sections)},
            )

        # 兜底：整个文档作为一个章节
        section = Section(
            section_id=self._make_section_id(doc.doc_id),
            source_doc_id=doc.doc_id,
            parent_section_id=None,
            title=doc.title or '正文',
            level=1,
            semantic_role='general',
            raw_content=self._extract_text(doc),
            index=0,
            context_before='',
            context_after='',
        )
        return SectionSplitResult(
            sections=[section],
            orphan_content='',
            total_count=1,
            stats={'strategy': 'fallback', 'count': 1},
        )

    def _split_by_headings(
        self, doc: ParsedDocument, cleaned: CleanedDocument | None,
    ) -> list[Section]:
        """按标题树拆分"""
        sections: list[Section] = []
        elements = doc.elements
        heading_map = self._build_heading_position_map(elements)

        for i, (heading_text, level, elem_idx) in enumerate(heading_map):
            # 计算该章节的内容范围
            start_idx = elem_idx
            if i + 1 < len(heading_map):
                end_idx = heading_map[i + 1][2]
            else:
                end_idx = len(elements)

            # 提取该章节的内容
            content_parts = []
            for j in range(start_idx, end_idx):
                elem = elements[j]
                if elem.type == 'HEADING':
                    # 子标题：保留但降低层级
                    if elem.section and elem.section > level:
                        content_parts.append('#' * min(elem.section, 6) + ' ' + elem.content)
                    elif elem.section == level and j > start_idx:
                        break  # 遇到同级标题，停止
                else:
                    content_parts.append(elem.content)

            content = '\n\n'.join(content_parts).strip()
            if not content:
                continue

            section = Section(
                section_id=self._make_section_id(doc.doc_id),
                source_doc_id=doc.doc_id,
                parent_section_id=None,
                title=heading_text,
                level=level,
                semantic_role=self._infer_role(heading_text, content),
                raw_content=content,
                index=i,
                context_before=heading_map[i - 1][0] if i > 0 else '',
                context_after=heading_map[i + 1][0] if i + 1 < len(heading_map) else '',
            )
            sections.append(section)

        # 绑定附件引用和代码块
        if cleaned:
            self._bind_attachments_and_code(sections, cleaned)

        return sections

    def _split_by_inferred(
        self, doc: ParsedDocument, cleaned: CleanedDocument,
    ) -> list[Section]:
        """按推断结构拆分"""
        sections: list[Section] = []
        paragraphs = cleaned.paragraphs
        inferred = cleaned.inferred_headings

        # 按推断标题分组段落
        heading_indices = sorted(set(h['para_idx'] for h in inferred))

        for i, para_idx in enumerate(heading_indices):
            heading = next(h for h in inferred if h['para_idx'] == para_idx)
            # 内容范围：从当前标题到下一个标题
            next_idx = heading_indices[i + 1] if i + 1 < len(heading_indices) else len(paragraphs)
            content_paras = paragraphs[para_idx + 1:next_idx]
            content = '\n\n'.join(content_paras).strip()

            if not content and i == len(heading_indices) - 1:
                continue

            section = Section(
                section_id=self._make_section_id(doc.doc_id),
                source_doc_id=doc.doc_id,
                parent_section_id=None,
                title=heading['text'],
                level=heading['level'],
                semantic_role=self._infer_role(heading['text'], content),
                raw_content=content,
                index=i,
                context_before=heading_indices[i - 1] if i > 0 else '',
                context_after=heading_indices[i + 1] if i + 1 < len(heading_indices) else '',
            )
            sections.append(section)

        self._bind_attachments_and_code(sections, cleaned)
        return sections

    def _split_by_classes(
        self, doc: ParsedDocument, cleaned: CleanedDocument,
    ) -> list[Section]:
        """按段落语义分类拆分"""
        sections: list[Section] = []
        paragraphs = cleaned.paragraphs
        classes = cleaned.paragraph_classes

        # 按分类分组段落
        class_groups: dict[str, list[int]] = {}
        for pc in classes:
            cls = pc['class']
            if cls not in class_groups:
                class_groups[cls] = []
            class_groups[cls].append(pc['para_idx'])

        # 优先顺序：overview → cause → analysis → solution → config → steps → warning
        priority = ['overview', 'cause', 'analysis', 'solution', 'config', 'steps', 'warning', 'general']
        idx = 0

        for cls in priority:
            if cls not in class_groups:
                continue
            para_indices = class_groups[cls]
            content_paras = [paragraphs[pi] for pi in para_indices if pi < len(paragraphs)]
            content = '\n\n'.join(content_paras).strip()
            if not content:
                continue

            # 用第一个段落的前 60 字作为标题
            title = content_paras[0][:60] if content_paras else cls
            section = Section(
                section_id=self._make_section_id(doc.doc_id),
                source_doc_id=doc.doc_id,
                parent_section_id=None,
                title=title,
                level=2,
                semantic_role=cls,
                raw_content=content,
                index=idx,
                context_before='',
                context_after='',
            )
            sections.append(section)
            idx += 1

        self._bind_attachments_and_code(sections, cleaned)
        return sections

    def _build_heading_position_map(
        self, elements: list[ParsedElement],
    ) -> list[tuple[str, int, int]]:
        """构建标题位置映射: [(标题文本, 层级, 元素索引)]"""
        result = []
        for i, elem in enumerate(elements):
            if elem.type == 'HEADING':
                result.append((elem.content, elem.section or 1, i))
        return result

    def _extract_text(self, doc: ParsedDocument) -> str:
        """提取文档的纯文本内容"""
        return '\n\n'.join(e.content for e in doc.elements)

    def _infer_role(self, title: str, content: str) -> str:
        """从标题和内容推断语义角色"""
        text = (title + ' ' + content[:200]).lower()

        role_keywords = [
            ('overview', ['概述', '简介', '背景', '介绍', '前言', '总览', '概览', 'overview', 'introduction']),
            ('cause', ['原因', '成因', '起因', '根源', 'cause', 'root cause']),
            ('analysis', ['分析', '排查', '诊断', '定位', '调查', 'analysis', 'troubleshoot', 'diagnosis']),
            ('solution', ['解决', '方案', '处置', '修复', '处理', 'solution', 'fix', 'resolve']),
            ('config', ['配置', '参数', '设置', '选项', 'config', 'parameter', 'setting']),
            ('steps', ['步骤', '流程', '操作', 'step', 'procedure', 'process']),
            ('warning', ['注意', '警告', '重要', '危险', '须知', 'warning', 'caution', 'important']),
            ('reference', ['参考', '参见', '引用', '来源', 'reference']),
        ]

        for role, keywords in role_keywords:
            for kw in keywords:
                if kw in text:
                    return role

        return 'general'

    def _bind_attachments_and_code(
        self, sections: list[Section], cleaned: CleanedDocument,
    ) -> None:
        """将附件引用和代码块绑定到对应章节"""
        for section in sections:
            content_lower = section.raw_content.lower()

            # 绑定附件引用（通过占位符匹配）
            for ref in cleaned.attachment_refs:
                placeholder = ref.get('placeholder', '')
                if placeholder and placeholder in section.raw_content:
                    section.attachment_refs.append(ref)

            # 绑定代码块（通过上下文匹配）
            for cb in cleaned.code_block_classes:
                cb_context = cb.get('context', '').lower()
                if cb_context and cb_context in content_lower:
                    section.code_blocks.append(cb)

    def _make_section_id(self, doc_id: str) -> str:
        """生成唯一 section_id"""
        self._counter += 1
        # 简化 doc_id 为安全的文件名
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', doc_id)[:40]
        return f'sec_{safe_id}_{self._counter:03d}'