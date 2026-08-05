"""Markdown 解析器（F1）

提取标题、段落、代码块、表格、列表，保留层级关系。

新增功能：
- 构建标题层级树（H1-H6）
- 段落归属到最近的标题
- 支持父级章节引用
- 生成层级化 Slug 候选
- 支持编号标题识别（如 "1.1 章节标题"）
- P1: 章节重构（基于内容语义重新划分，而非直接使用原始标题）
"""

from __future__ import annotations

import hashlib
import re

from app.parsers.base import ElementType, HeadingNode, ParsedDocument, ParsedElement
from app.parsers.section_reconstructor import ReconstructedDocument, SectionReconstructor
from app.parsers.text_cleaner import CleanedDocument, TextCleaner


class MarkdownParser:
    format = "markdown"

    def __init__(self) -> None:
        # P0: 文本清洗器，在解析前预处理混乱格式文档
        self.cleaner = TextCleaner()
        # P1: 章节重构器，基于内容语义重新划分章节
        self.reconstructor = SectionReconstructor()

    def parse(self, path: str, doc_id: str, clean_text: bool = True, reconstruct_sections: bool = True) -> ParsedDocument:
        with open(path, encoding="utf-8") as f:
            raw = f.read()

        # P0: 文本清洗管道 — 规范化空白、去除 HTML 残留、检测标题/段落/表格
        cleaned: CleanedDocument | None = None
        if clean_text:
            cleaned = self.cleaner.clean(raw)
            text = cleaned.cleaned_text
        else:
            text = raw

        # P1: 章节重构 — 基于内容语义重新划分章节，而非直接使用原始标题
        reconstructed: ReconstructedDocument | None = None
        if reconstruct_sections and cleaned is not None:
            reconstructed = self.reconstructor.reconstruct(
                cleaned, original_headings=cleaned.detected_headings,
            )
            text = reconstructed.reconstructed_text
        else:
            # P0: 当清洗器推断出标题，注入推断标题（补充显式标题不足的情况）
            if cleaned and cleaned.inferred_headings:
                text = self._inject_inferred_headings(
                    text, cleaned.paragraphs, cleaned.inferred_headings,
                    existing_headings=cleaned.detected_headings,
                )

        checksum = hashlib.sha256(text.encode()).hexdigest()
        title = self._extract_title(text)
        elements, heading_tree = self._parse_markdown(text)

        # P0: 将清洗器检测到的标题/段落/推断结构/段落分类作为解析提示存入 metadata
        if cleaned is not None:
            for elem in elements:
                if elem.metadata is None:
                    elem.metadata = {}
            if cleaned.detected_headings:
                for elem in elements:
                    elem.metadata.setdefault('cleaner_headings', cleaned.detected_headings)
            if cleaned.paragraphs:
                for elem in elements:
                    elem.metadata.setdefault('cleaner_paragraph_count', len(cleaned.paragraphs))
            if cleaned.inferred_headings:
                for elem in elements:
                    elem.metadata.setdefault('inferred_headings', cleaned.inferred_headings)
            if cleaned.paragraph_classes:
                for elem in elements:
                    elem.metadata.setdefault('paragraph_classes', cleaned.paragraph_classes)

        # P1: 将章节重构统计信息添加到第一个元素的 metadata
        if reconstructed is not None and elements:
            first_elem = elements[0]
            if first_elem.metadata is None:
                first_elem.metadata = {}
            first_elem.metadata['section_reconstruction'] = reconstructed.stats
            # 标记重构章节数量
            first_elem.metadata['reconstructed_sections'] = len(reconstructed.sections)
            # 保存原始章节与重构章节的映射（用于追踪）
            if reconstructed.sections:
                first_elem.metadata['section_titles'] = [
                    {'title': s.title, 'level': s.level, 'original_title': s.original_title}
                    for s in reconstructed.sections[:10]  # 最多保存前 10 个
                ]

        return ParsedDocument(
            doc_id=doc_id,
            source_path=path,
            format="markdown",
            checksum=checksum,
            title=title,
            elements=elements,
            heading_tree=heading_tree,
        )

    async def aparse(
        self,
        path: str,
        doc_id: str,
        clean_text: bool = True,
        reconstruct_sections: bool = True,
        use_llm_classification: bool = True,
    ) -> ParsedDocument:
        """异步解析 — 支持 LLM 段落分类

        Args:
            path: 文件路径
            doc_id: 文档 ID
            clean_text: 是否清洗文本
            reconstruct_sections: 是否重构章节
            use_llm_classification: 是否使用 LLM 段落分类

        Returns:
            ParsedDocument
        """
        with open(path, encoding="utf-8") as f:
            raw = f.read()

        # 异步清洗（含 LLM 分类）
        cleaned: CleanedDocument | None = None
        if clean_text:
            cleaned = await self.cleaner.aclean(
                raw,
                use_llm_classification=use_llm_classification,
            )
            text = cleaned.cleaned_text
        else:
            text = raw

        # 章节重构
        reconstructed: ReconstructedDocument | None = None
        if reconstruct_sections and cleaned is not None:
            reconstructed = self.reconstructor.reconstruct(
                cleaned, original_headings=cleaned.detected_headings,
            )
            text = reconstructed.reconstructed_text
        else:
            if cleaned and cleaned.inferred_headings:
                text = self._inject_inferred_headings(
                    text, cleaned.paragraphs, cleaned.inferred_headings,
                    existing_headings=cleaned.detected_headings,
                )

        checksum = hashlib.sha256(text.encode()).hexdigest()
        title = self._extract_title(text)
        elements, heading_tree = self._parse_markdown(text)

        # metadata 注入（与同步版本一致）
        if cleaned is not None:
            for elem in elements:
                if elem.metadata is None:
                    elem.metadata = {}
            if cleaned.detected_headings:
                for elem in elements:
                    elem.metadata.setdefault('cleaner_headings', cleaned.detected_headings)
            if cleaned.paragraphs:
                for elem in elements:
                    elem.metadata.setdefault('cleaner_paragraph_count', len(cleaned.paragraphs))
            if cleaned.inferred_headings:
                for elem in elements:
                    elem.metadata.setdefault('inferred_headings', cleaned.inferred_headings)
            if cleaned.paragraph_classes:
                for elem in elements:
                    elem.metadata.setdefault('paragraph_classes', cleaned.paragraph_classes)

            # LLM 分类标记
            if cleaned.stats.get('llm_classification_used'):
                for elem in elements:
                    elem.metadata.setdefault('llm_classification_used', True)

        if reconstructed is not None and elements:
            first_elem = elements[0]
            if first_elem.metadata is None:
                first_elem.metadata = {}
            first_elem.metadata['section_reconstruction'] = reconstructed.stats
            first_elem.metadata['reconstructed_sections'] = len(reconstructed.sections)
            if reconstructed.sections:
                first_elem.metadata['section_titles'] = [
                    {'title': s.title, 'level': s.level, 'original_title': s.original_title}
                    for s in reconstructed.sections[:10]
                ]

        return ParsedDocument(
            doc_id=doc_id,
            source_path=path,
            format="markdown",
            checksum=checksum,
            title=title,
            elements=elements,
            heading_tree=heading_tree,
        )

    def _extract_title(self, text: str) -> str | None:
        m = re.match(r"^#\s+(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip()
        m = re.match(r"^(\d+\.)+\s+(.+)$", text, re.MULTILINE)
        if m:
            return m.group(2).strip()[:120]
        first = text.strip().split("\n")[0].strip()
        if first:
            return re.sub(r"\*+", "", first).strip()[:120]
        return None

    def _parse_markdown(self, text: str) -> tuple[list[ParsedElement], list[HeadingNode]]:
        elements: list[ParsedElement] = []
        lines = text.split("\n")
        i = 0

        heading_stack: list[tuple[int, HeadingNode]] = []
        heading_tree: list[HeadingNode] = []
        current_section: str | None = None
        current_parent_section: str | None = None

        while i < len(lines):
            line = lines[i]

            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                self._process_heading(
                    heading_match, elements, heading_stack, heading_tree,
                    current_section, current_parent_section, level, title
                )
                if level <= 2:
                    current_section = title
                if level >= 2:
                    parents = [h[1].title for h in heading_stack[:-1]]
                    current_parent_section = parents[-1] if parents else None
                i += 1
                continue

            numbered_heading_match = re.match(r"^(\d+(?:\.\d+)*)\.\s+(.+)$", line)
            if numbered_heading_match:
                number_str = numbered_heading_match.group(1)
                title = numbered_heading_match.group(2).strip()
                # 过滤：颜色名/过短标题不作为章节
                if self._is_color_name(title) or len(title) < 3:
                    i += 1
                    continue
                level = len(number_str.split("."))
                self._process_heading(
                    numbered_heading_match, elements, heading_stack, heading_tree,
                    current_section, current_parent_section, level, title
                )
                if level <= 2:
                    current_section = title
                if level >= 2:
                    parents = [h[1].title for h in heading_stack[:-1]]
                    current_parent_section = parents[-1] if parents else None
                i += 1
                continue

            code_match = re.match(r"^```(\w*)$", line)
            if code_match:
                lang = code_match.group(1) or ""
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                i += 1
                code_element = ParsedElement(
                    type=ElementType.CODE,
                    content="\n".join(code_lines),
                    section=current_section,
                    parent_section=current_parent_section,
                    metadata={"language": lang},
                )
                elements.append(code_element)
                if heading_stack:
                    heading_stack[-1][1].elements.append(code_element)
                continue

            if (
                "|" in line
                and i + 1 < len(lines)
                and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1])
            ):
                table_lines = [line]
                i += 1
                table_lines.append(lines[i])
                i += 1
                while i < len(lines) and "|" in lines[i]:
                    table_lines.append(lines[i])
                    i += 1
                table_element = ParsedElement(
                    type=ElementType.TABLE,
                    content="\n".join(table_lines),
                    section=current_section,
                    parent_section=current_parent_section,
                )
                elements.append(table_element)
                if heading_stack:
                    heading_stack[-1][1].elements.append(table_element)
                continue

            list_match = re.match(r"^(\s*)([-*+]|\d+\.)\s+", line)
            if list_match:
                list_lines = [line]
                i += 1
                while i < len(lines) and re.match(r"^(\s*)([-*+]|\d+\.)\s+", lines[i]):
                    list_lines.append(lines[i])
                    i += 1
                list_element = ParsedElement(
                    type=ElementType.LIST,
                    content="\n".join(list_lines),
                    section=current_section,
                    parent_section=current_parent_section,
                )
                elements.append(list_element)
                if heading_stack:
                    heading_stack[-1][1].elements.append(list_element)
                continue

            if not line.strip():
                i += 1
                continue

            para_lines = [line]
            i += 1
            while (
                i < len(lines)
                and lines[i].strip()
                and not self._is_special_line(lines[i])
            ):
                para_lines.append(lines[i])
                i += 1
            para_element = ParsedElement(
                type=ElementType.PARAGRAPH,
                content=" ".join(para_lines),
                section=current_section,
                parent_section=current_parent_section,
            )
            elements.append(para_element)
            if heading_stack:
                heading_stack[-1][1].elements.append(para_element)

        return elements, heading_tree

    def _process_heading(
        self,
        match,
        elements: list[ParsedElement],
        heading_stack: list[tuple[int, HeadingNode]],
        heading_tree: list[HeadingNode],
        current_section: str | None,
        current_parent_section: str | None,
        level: int,
        title: str,
    ) -> None:
        heading_element = ParsedElement(
            type=ElementType.HEADING,
            content=title,
            section=title,
            parent_section=current_parent_section,
            metadata={"level": level},
        )
        elements.append(heading_element)

        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()

        new_node = HeadingNode(level=level, title=title)

        if heading_stack:
            heading_stack[-1][1].children.append(new_node)
        else:
            heading_tree.append(new_node)

        heading_stack.append((level, new_node))

    def _is_special_line(self, line: str) -> bool:
        return bool(re.match(r"^(#{1,6}\s|```|[-*+]\s|\d+\.\s|\||\d+(?:\.\d+)*\.\s)", line))

    # 不应作为章节标题的颜色名
    _COLOR_NAMES = frozenset([
        '白橙', '橙', '白绿', '绿', '白蓝', '蓝', '白棕', '棕',
        'white-orange', 'orange', 'white-green', 'green',
        'white-blue', 'blue', 'white-brown', 'brown',
    ])

    @classmethod
    def _is_color_name(cls, title: str) -> bool:
        """判断标题是否是颜色名（不应作为章节）"""
        return title.lower() in cls._COLOR_NAMES

    def _inject_inferred_headings(
        self, text: str, paragraphs: list[str], inferred: list[dict],
        existing_headings: list[dict] | None = None,
    ) -> str:
        """将推断的标题注入原文本，转换为 Markdown 标题语法

        当文档无显式标题或标题不足以覆盖文档时，将推断出的标题候选行
        转换为 `#` 前缀标题，使后续 `_parse_markdown` 能正常构建标题树。

        Args:
            text: 原始清洗后文本
            paragraphs: 段落列表
            inferred: 推断的标题列表 [{para_idx, text, level, ...}]
            existing_headings: 已有的检测标题列表，用于跳过已覆盖的段落

        Returns:
            注入标题后的文本
        """
        if not inferred:
            return text

        # 构建已有标题覆盖的段落索引集合（通过文本匹配）
        covered_paras: set[int] = set()
        if existing_headings:
            for h in existing_headings:
                h_text = h.get('text', '')
                for pi, para in enumerate(paragraphs):
                    if h_text in para or para in h_text:
                        covered_paras.add(pi)

        # 按段落索引排序，过滤已覆盖的段落
        inferred_sorted = sorted(
            [h for h in inferred if h['para_idx'] not in covered_paras],
            key=lambda h: h['para_idx'],
        )

        if not inferred_sorted:
            return text

        # 逐段重建文本，在推断标题前插入 Markdown 标题行
        lines = text.split('\n')
        para_to_heading: dict[int, dict] = {}
        for h in inferred_sorted:
            para_to_heading[h['para_idx']] = h

        result: list[str] = []
        para_idx = -1
        buffer: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if buffer:
                    para_idx += 1
                    if para_idx in para_to_heading:
                        heading_info = para_to_heading[para_idx]
                        prefix = '#' * heading_info['level']
                        result.append(f'\n{prefix} {heading_info["text"]}\n')
                    else:
                        result.append('\n'.join(buffer))
                    buffer = []
                result.append('')
                continue

            buffer.append(line)

        # 处理最后一段
        if buffer:
            para_idx += 1
            if para_idx in para_to_heading:
                heading_info = para_to_heading[para_idx]
                prefix = '#' * heading_info['level']
                result.append(f'\n{prefix} {heading_info["text"]}\n')
            else:
                result.append('\n'.join(buffer))

        return '\n'.join(result)
