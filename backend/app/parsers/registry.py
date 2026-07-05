"""解析器注册中心。

策略（见 docs/解析器对比评测.md）：
  - Word/Excel/PPT/HTML → MarkItDown（MIT，无 GPU，180+ 文件/s）
  - PDF/图片 → MinerU（VLM 高精度，Python 3.10-3.13 环境）→ MarkItDown（fallback）
  - SQL → sqlparse（自研，语义识别）
  - Markdown → 自研（结构提取）
  - TXT → 自研（零依赖最快）
  - Unstructured → 备选 fallback
"""

from __future__ import annotations

from typing import Callable

from app.parsers.base import DocumentParser

_registry: dict[str, Callable[[], DocumentParser]] = {}


def register_parser(fmt: str, factory: Callable[[], DocumentParser]) -> None:
    fmt = fmt.lower()
    if fmt in _registry:
        raise ValueError(f"解析器已注册: {fmt}")
    _registry[fmt] = factory


def get_parser(fmt: str) -> DocumentParser:
    fmt = fmt.lower()
    if fmt not in _registry:
        raise KeyError(f"无可用解析器: {fmt}（已注册: {list(_registry)}）")
    return _registry[fmt]()


def supported_formats() -> list[str]:
    return sorted(_registry.keys())


def _register_builtin() -> None:
    # ── 自研（必须） ──
    try:
        from app.parsers.markdown_parser import MarkdownParser

        register_parser("markdown", lambda: MarkdownParser())
        register_parser("md", lambda: MarkdownParser())
    except ImportError:
        pass
    try:
        from app.parsers.sql_parser import SQLParser

        register_parser("sql", lambda: SQLParser())
    except ImportError:
        pass
    try:
        from app.parsers.text_parser import TextParser

        register_parser("txt", lambda: TextParser())
    except ImportError:
        pass

    # ── MarkItDown（主力，MIT 许可） ──
    try:
        from app.parsers.markitdown_adapter import make_markitdown_factory

        for fmt in (
            "word",
            "docx",
            "doc",
            "excel",
            "ppt",
            "pptx",
            "html",
            "htm",
            "epub",
            "csv",
            "json",
        ):
            register_parser(fmt, make_markitdown_factory(fmt))
    except ImportError:
        pass

    # ── MinerU（PDF/Excel/图片 专项，Python 3.10-3.13，GPU 推荐） ──
    # 实测：Excel 合并单元格处理优于 MarkItDown（colspan 保留 vs Unnamed:X）
    try:
        from app.parsers.mineru_adapter import make_mineru_factory

        register_parser("pdf", make_mineru_factory("pdf"))
        register_parser("xlsx", make_mineru_factory("xlsx"))
        register_parser("xls", make_mineru_factory("xls"))
    except ImportError:
        # MinerU 不可用时，降级为 MarkItDown
        try:
            from app.parsers.markitdown_adapter import make_markitdown_factory

            register_parser("pdf", make_markitdown_factory("pdf"))
            register_parser("xlsx", make_markitdown_factory("xlsx"))
            register_parser("xls", make_markitdown_factory("xls"))
        except ImportError:
            pass
    try:
        from app.parsers.mineru_adapter import make_mineru_factory

        for fmt in ("png", "jpg", "jpeg", "gif", "bmp"):
            register_parser(fmt, make_mineru_factory(fmt))
    except ImportError:
        pass

    # ── Unstructured（备选 fallback） ──
    try:
        from app.parsers.unstructured_adapter import make_unstructured_factory

        for fmt in ("rst", "xml", "odt", "msg", "eml"):
            register_parser(fmt, make_unstructured_factory(fmt))
    except ImportError:
        pass


_register_builtin()
