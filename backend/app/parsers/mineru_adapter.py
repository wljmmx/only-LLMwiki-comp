"""MinerU 适配器（PDF/图片 → Markdown）

基于上海人工智能实验室 mineru（69k+ stars），VLM+OCR 双引擎，
OmniDocBench 综合准确率 90.7%，公式/表格/阅读顺序全面领先。

要求：Python 3.10–3.13，GPU 8GB+（CPU pipeline 模式可用但慢）。
若环境不支持，自动降级到 MarkItDown。
"""
from __future__ import annotations

import hashlib
import os
import structlog
from typing import Callable

from app.parsers.base import DocumentParser, ParsedDocument

logger = structlog.get_logger()


class MinerUAdapter:
    """MinerU PDF 解析适配器"""

    def __init__(self, source_format: str = "pdf") -> None:
        self.format = source_format

    def parse(self, path: str, doc_id: str) -> ParsedDocument:
        with open(path, "rb") as f:
            checksum = hashlib.sha256(f.read()).hexdigest()

        try:
            from mineru import parse_doc
            from pathlib import Path
            import tempfile

            output_dir = tempfile.mkdtemp(prefix="mineru_")
            parse_doc(
                path_list=[Path(path)],
                output_dir=output_dir,
                lang="ch",
                backend="pipeline",  # CPU 模式；GPU 环境用 "hybrid-auto-engine"
            )

            # 读取 MinerU 输出的 Markdown
            md_files = list(Path(output_dir).rglob("*.md"))
            if not md_files:
                raise RuntimeError("MinerU 未生成 Markdown 输出")

            md_text = md_files[0].read_text(encoding="utf-8")

            # 写入临时文件供 MarkdownParser 解析
            with tempfile.NamedTemporaryFile(suffix=".md", mode="w", encoding="utf-8", delete=False) as tmp:
                tmp.write(md_text)
                tmp_path = tmp.name

            from app.parsers.markdown_parser import MarkdownParser
            md_parser = MarkdownParser()
            doc = md_parser.parse(tmp_path, doc_id)
            doc.format = self.format
            doc.checksum = checksum
            doc.source_path = path
            os.unlink(tmp_path)
            return doc

        except ImportError:
            logger.warning("mineru_not_installed", fallback="markitdown")
            return self._fallback_markitdown(path, doc_id, checksum)
        except Exception as e:
            logger.error("mineru_parse_failed", error=str(e), fallback="markitdown")
            return self._fallback_markitdown(path, doc_id, checksum)

    def _fallback_markitdown(self, path: str, doc_id: str, checksum: str) -> ParsedDocument:
        """降级到 MarkItDown"""
        from app.parsers.markitdown_adapter import MarkItDownAdapter
        adapter = MarkItDownAdapter("pdf")
        doc = adapter.parse(path, doc_id)
        doc.checksum = checksum
        return doc


def make_mineru_factory(fmt: str = "pdf") -> Callable[[], DocumentParser]:
    return lambda: MinerUAdapter(fmt)