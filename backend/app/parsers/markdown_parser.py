"""Markdown 解析器（F1）

提取标题、段落、代码块、表格、列表，保留层级关系。
"""

from __future__ import annotations

import re
import hashlib

from app.parsers.base import ElementType, ParsedDocument, ParsedElement


class MarkdownParser:
    format = "markdown"

    def parse(self, path: str, doc_id: str) -> ParsedDocument:
        with open(path, encoding="utf-8") as f:
            text = f.read()

        checksum = hashlib.sha256(text.encode()).hexdigest()
        title = self._extract_title(text)
        elements = self._parse_markdown(text)

        return ParsedDocument(
            doc_id=doc_id,
            source_path=path,
            format="markdown",
            checksum=checksum,
            title=title,
            elements=elements,
        )

    def _extract_title(self, text: str) -> str | None:
        m = re.match(r"^#\s+(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip()
        # 兜底：取首行非空内容（去除 Markdown 加粗标记）
        first = text.strip().split("\n")[0].strip()
        if first:
            return re.sub(r"\*+", "", first).strip()[:120]
        return None

    def _parse_markdown(self, text: str) -> list[ParsedElement]:
        elements: list[ParsedElement] = []
        lines = text.split("\n")
        i = 0
        current_section: str | None = None

        while i < len(lines):
            line = lines[i]

            # 标题
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                elements.append(
                    ParsedElement(
                        type=ElementType.HEADING,
                        content=title,
                        metadata={"level": level},
                    )
                )
                if level <= 2:
                    current_section = title
                i += 1
                continue

            # 代码块（围栏）
            code_match = re.match(r"^```(\w*)$", line)
            if code_match:
                lang = code_match.group(1) or ""
                code_lines = []
                i += 1
                while i < len(lines) and not lines[i].startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                i += 1  # skip closing ```
                elements.append(
                    ParsedElement(
                        type=ElementType.CODE,
                        content="\n".join(code_lines),
                        section=current_section,
                        metadata={"language": lang},
                    )
                )
                continue

            # 表格
            if (
                "|" in line
                and i + 1 < len(lines)
                and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1])
            ):
                table_lines = [line]
                i += 1
                # 分隔行
                table_lines.append(lines[i])
                i += 1
                while i < len(lines) and "|" in lines[i]:
                    table_lines.append(lines[i])
                    i += 1
                elements.append(
                    ParsedElement(
                        type=ElementType.TABLE,
                        content="\n".join(table_lines),
                        section=current_section,
                    )
                )
                continue

            # 列表（连续行）
            list_match = re.match(r"^(\s*)([-*+]|\d+\.)\s+", line)
            if list_match:
                list_lines = [line]
                i += 1
                while i < len(lines) and re.match(r"^(\s*)([-*+]|\d+\.)\s+", lines[i]):
                    list_lines.append(lines[i])
                    i += 1
                elements.append(
                    ParsedElement(
                        type=ElementType.LIST,
                        content="\n".join(list_lines),
                        section=current_section,
                    )
                )
                continue

            # 空行跳过
            if not line.strip():
                i += 1
                continue

            # 段落（连续非空行）
            para_lines = [line]
            i += 1
            while (
                i < len(lines)
                and lines[i].strip()
                and not self._is_special_line(lines[i])
            ):
                para_lines.append(lines[i])
                i += 1
            elements.append(
                ParsedElement(
                    type=ElementType.PARAGRAPH,
                    content=" ".join(para_lines),
                    section=current_section,
                )
            )

        return elements

    def _is_special_line(self, line: str) -> bool:
        return bool(re.match(r"^(#{1,6}\s|```|[-*+]\s|\d+\.\s|\|)", line))
