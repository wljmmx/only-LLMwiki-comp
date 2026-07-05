"""解析器注册中心。新增格式：实现 DocumentParser 后调用 register_parser() 即可。"""
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


# 注册内置解析器（延迟导入，避免未安装依赖时启动崩溃）
def _register_builtin() -> None:
    try:
        from app.parsers.markdown_parser import MarkdownParser
        register_parser("markdown", lambda: MarkdownParser())
    except ImportError: pass
    try:
        from app.parsers.sql_parser import SQLParser
        register_parser("sql", lambda: SQLParser())
    except ImportError: pass
    try:
        from app.parsers.text_parser import TextParser
        register_parser("txt", lambda: TextParser())
    except ImportError: pass
    try:
        from app.parsers.html_parser import HTMLParser
        register_parser("html", lambda: HTMLParser())
    except ImportError: pass
    try:
        from app.parsers.word_parser import WordParser
        register_parser("word", lambda: WordParser())
        register_parser("docx", lambda: WordParser())
    except ImportError: pass
    try:
        from app.parsers.excel_parser import ExcelParser
        register_parser("excel", lambda: ExcelParser())
        register_parser("xlsx", lambda: ExcelParser())
    except ImportError: pass


_register_builtin()