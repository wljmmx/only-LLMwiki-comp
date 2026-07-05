"""HTML 解析器（基于 BeautifulSoup）

提取标题、段落、表格、代码块、列表，去除标签。
"""
from __future__ import annotations

import hashlib

from bs4 import BeautifulSoup
from markdownify import markdownify as md

from app.parsers.base import DocumentParser, ElementType, ParsedDocument, ParsedElement


class HTMLParser:
    format = "html"

    def parse(self, path: str, doc_id: str) -> ParsedDocument:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()

        checksum = hashlib.sha256(text.encode()).hexdigest()
        soup = BeautifulSoup(text, "lxml")
        title = soup.title.string if soup.title else None
        elements = self._parse_html(soup)

        return ParsedDocument(
            doc_id=doc_id, source_path=path, format="html",
            checksum=checksum, title=title, elements=elements,
        )

    def _parse_html(self, soup: BeautifulSoup) -> list[ParsedElement]:
        elements: list[ParsedElement] = []
        body = soup.body or soup
        current_section: str | None = None

        for tag in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "code", "table", "ul", "ol"]):
            if tag.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(tag.name[1])
                content = tag.get_text(strip=True)
                elements.append(ParsedElement(
                    type=ElementType.HEADING, content=content,
                    metadata={"level": level},
                ))
                if level <= 2:
                    current_section = content
                continue

            # 代码块：仅独立的 pre 或没有 pre 父元素的 code
            if tag.name == "pre":
                content = tag.get_text()
                if content.strip():
                    elements.append(ParsedElement(
                        type=ElementType.CODE, content=content.strip(),
                        section=current_section,
                    ))
                continue
            if tag.name == "code" and tag.parent and tag.parent.name != "pre":
                content = tag.get_text()
                if content.strip():
                    elements.append(ParsedElement(
                        type=ElementType.CODE, content=content.strip(),
                        section=current_section,
                    ))
                continue

            if tag.name == "table":
                elements.append(ParsedElement(
                    type=ElementType.TABLE,
                    content=md(str(tag)) if callable(md) else str(tag),
                    section=current_section,
                ))
                continue

            if tag.name in ("ul", "ol"):
                items = [li.get_text(strip=True) for li in tag.find_all("li")]
                elements.append(ParsedElement(
                    type=ElementType.LIST, content="\n".join(items),
                    section=current_section,
                ))
                continue

            if tag.name == "p":
                content = tag.get_text(strip=True)
                if content:
                    elements.append(ParsedElement(
                        type=ElementType.PARAGRAPH, content=content,
                        section=current_section,
                    ))

        return elements