"""解析器接口与统一数据模型（F1）

所有解析器必须输出统一的 ParsedDocument，保留源文件追溯链。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class ElementType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    CODE = "code"
    LIST = "list"
    SQL_STATEMENT = "sql_statement"
    IMAGE = "image"
    METADATA = "metadata"


@dataclass
class ParsedElement:
    type: ElementType
    content: str
    page: int | None = None
    section: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ParsedDocument:
    doc_id: str
    source_path: str
    format: str
    checksum: str
    title: str | None = None
    elements: list[ParsedElement] = field(default_factory=list)
    imported_at: str | None = None


@runtime_checkable
class DocumentParser(Protocol):
    format: str
    def parse(self, path: str, doc_id: str) -> ParsedDocument: ...