"""Markdown 解析器（F1）

提取标题、段落、代码块、表格、列表，保留层级关系。

新增功能：
- 构建标题层级树（H1-H6）
- 段落归属到最近的标题
- 支持父级章节引用
- 生成层级化 Slug 候选
- 支持编号标题识别（如 "1.1 章节标题"）
"""

from __future__ import annotations

import hashlib
import re

from app.parsers.base import ElementType, HeadingNode, ParsedDocument, ParsedElement
from app.parsers.text_cleaner import CleanedDocument, TextCleaner


class MarkdownParser:
    format = "markdown"

    def __init__(self) -> None:
        # P0: 文本清洗器，在解析前预处理混乱格式文档
        self.cleaner = TextCleaner()

    def parse(self, path: str, doc_id: str, clean_text: bool = True) -> ParsedDocument:
        with open(path, encoding="utf-8") as f:
            raw = f.read()

        # P0: 文本清洗管道 — 规范化空白、去除 HTML 残留、检测标题/段落/表格
        cleaned: CleanedDocument | None = None
        if clean_text:
            cleaned = self.cleaner.clean(raw)
            text = cleaned.cleaned_text
        else:
            text = raw

        checksum = hashlib.sha256(text.encode()).hexdigest()
        title = self._extract_title(text)
        elements, heading_tree = self._parse_markdown(text)

        # P0: 将清洗器检测到的标题/段落作为解析提示存入 metadata
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

            numbered_heading_match = re.match(r"^(\d+(?:\.\d+)*)\s+(.+)$", line)
            if numbered_heading_match:
                number_str = numbered_heading_match.group(1)
                title = numbered_heading_match.group(2).strip()
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
        return bool(re.match(r"^(#{1,6}\s|```|[-*+]\s|\d+\.\s|\||\d+(?:\.\d+)*\s)", line))
