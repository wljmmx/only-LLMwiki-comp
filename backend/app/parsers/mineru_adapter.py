"""MinerU 适配器（PDF/图片 → Markdown）

基于上海人工智能实验室 mineru（69k+ stars），VLM+OCR 双引擎，
OmniDocBench 综合准确率 90.7%，公式/表格/阅读顺序全面领先。

支持格式：PDF、DOCX、PPTX、XLSX、图片（PNG/JPG等）
不支持：HTML、Markdown、TXT

要求：Python 3.10–3.13，GPU 8GB+（pipeline 模式 CPU 可用但慢）。
若环境不支持，自动降级到 MarkItDown。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

import structlog

from app.parsers.base import DocumentParser, ParsedDocument

logger = structlog.get_logger()

# MinerU 支持的格式（扩展名）
MINERU_SUPPORTED = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tiff",
    ".tif",
}


def _find_mineru_bin() -> str | None:
    """查找 mineru CLI 可执行文件路径"""
    # 优先查找 pyenv 安装的版本
    for pyver in ["3.12.13", "3.12", "3.11", "3.10"]:
        candidate = Path(f"/root/.pyenv/versions/{pyver}/bin/mineru")
        if candidate.exists():
            return str(candidate)
    # 回退到 PATH
    found = shutil.which("mineru")
    return found


class MinerUAdapter:
    """MinerU PDF/Office 解析适配器"""

    def __init__(self, source_format: str = "pdf") -> None:
        self.format = source_format

    def parse(self, path: str, doc_id: str) -> ParsedDocument:
        with open(path, "rb") as f:
            checksum = hashlib.sha256(f.read()).hexdigest()

        ext = Path(path).suffix.lower()
        if ext not in MINERU_SUPPORTED:
            logger.info("mineru_format_not_supported", ext=ext, fallback="markitdown")
            return self._fallback_markitdown(path, doc_id, checksum)

        mineru_bin = _find_mineru_bin()
        if mineru_bin is None:
            logger.warning("mineru_not_found", fallback="markitdown")
            return self._fallback_markitdown(path, doc_id, checksum)

        try:
            output_dir = tempfile.mkdtemp(prefix="mineru_")
            cmd = [
                mineru_bin,
                "-p",
                str(path),
                "-o",
                str(output_dir),
                "-b",
                "pipeline",
            ]
            logger.info("mineru_parse_start", cmd=cmd)
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip()[:200])

            # 读取 MinerU 输出的 Markdown 文件
            md_files = list(Path(output_dir).rglob("*.md"))
            if not md_files:
                raise RuntimeError("MinerU 未生成 Markdown 输出")

            md_text = md_files[0].read_text(encoding="utf-8")

            # 写入临时文件供 MarkdownParser 解析
            with tempfile.NamedTemporaryFile(
                suffix=".md", mode="w", encoding="utf-8", delete=False
            ) as tmp:
                tmp.write(md_text)
                tmp_path = tmp.name

            from app.parsers.markdown_parser import MarkdownParser

            md_parser = MarkdownParser()
            doc = md_parser.parse(tmp_path, doc_id)
            doc.format = self.format
            doc.checksum = checksum
            doc.source_path = path
            os.unlink(tmp_path)

            # 清理临时输出目录
            shutil.rmtree(output_dir, ignore_errors=True)
            return doc

        except (ImportError, FileNotFoundError):
            logger.warning("mineru_not_installed", fallback="markitdown")
            return self._fallback_markitdown(path, doc_id, checksum)
        except subprocess.TimeoutExpired:
            logger.error("mineru_timeout", fallback="markitdown")
            return self._fallback_markitdown(path, doc_id, checksum)
        except Exception as e:
            logger.error("mineru_parse_failed", error=str(e), fallback="markitdown")
            return self._fallback_markitdown(path, doc_id, checksum)

    def _fallback_markitdown(
        self, path: str, doc_id: str, checksum: str
    ) -> ParsedDocument:
        """降级到 MarkItDown"""
        from app.parsers.markitdown_adapter import MarkItDownAdapter

        adapter = MarkItDownAdapter("pdf")
        doc = adapter.parse(path, doc_id)
        doc.checksum = checksum
        return doc


def make_mineru_factory(fmt: str = "pdf") -> Callable[[], DocumentParser]:
    return lambda: MinerUAdapter(fmt)
