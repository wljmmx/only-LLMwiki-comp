"""纯文本解析器（TXT）

按空行分段，保留原始文本结构。
"""

from __future__ import annotations

import hashlib

from app.parsers.base import ElementType, ParsedDocument, ParsedElement


class TextParser:
    format = "txt"

    def parse(self, path: str, doc_id: str) -> ParsedDocument:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()

        checksum = hashlib.sha256(text.encode()).hexdigest()
        title = text.split("\n")[0].strip()[:100] if text.strip() else None
        elements = self._parse_text(text)

        return ParsedDocument(
            doc_id=doc_id,
            source_path=path,
            format="txt",
            checksum=checksum,
            title=title,
            elements=elements,
            heading_tree=[],
        )

    def _parse_text(self, text: str) -> list[ParsedElement]:
        elements: list[ParsedElement] = []
        paragraphs = text.split("\n\n")
        for para in paragraphs:
            content = para.strip()
            if not content:
                continue
            elements.append(
                ParsedElement(
                    type=ElementType.PARAGRAPH,
                    content=content,
                )
            )
        return elements
