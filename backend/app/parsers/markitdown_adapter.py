"""MarkItDown 适配器（Word/Excel/PPT/HTML/EPUB/PDF → Markdown）

基于 Microsoft markitdown（MIT），将 Office/HTML 等格式转为 Markdown，
再通过自研 MarkdownParser 提取结构化元素。

优势：纯 Python，无 GPU 依赖，180+ 文件/秒，MIT 许可。
"""

from __future__ import annotations

import hashlib
import os
from typing import Callable

from markitdown import MarkItDown

from app.parsers.base import DocumentParser, ParsedDocument
from app.parsers.markdown_parser import MarkdownParser

# 单例复用
_md_converter = MarkItDown()
_md_parser = MarkdownParser()


class MarkItDownAdapter:
    """统一适配器，将任意格式 → Markdown → ParsedDocument"""

    def __init__(self, source_format: str) -> None:
        self.format = source_format
        self._md_converter = _md_converter
        self._md_parser = _md_parser

    def parse(self, path: str, doc_id: str) -> ParsedDocument:
        # 计算原文件 checksum
        with open(path, "rb") as f:
            checksum = hashlib.sha256(f.read()).hexdigest()

        # MarkItDown 转换 → Markdown
        result = self._md_converter.convert(path)
        md_text = result.text_content

        # 通过 Markdown 解析器提取结构化元素
        # 先写入临时 md 文件
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=".md", mode="w", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(md_text)
            tmp_path = tmp.name

        try:
            doc = self._md_parser.parse(tmp_path, doc_id)
            # 覆盖为原始格式信息
            doc.format = self.format
            doc.checksum = checksum
            doc.source_path = path
            return doc
        finally:
            os.unlink(tmp_path)


def make_markitdown_factory(fmt: str) -> Callable[[], DocumentParser]:
    return lambda: MarkItDownAdapter(fmt)
