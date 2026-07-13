"""解析器接口与统一数据模型（F1）

所有解析器必须输出统一的 ParsedDocument，保留源文件追溯链。

核心数据结构：
- ParsedElement：扁平化元素列表（兼容现有 LLM 抽取流程）
- HeadingNode：标题层级树（新增，支持结构化知识组织）
- ParsedDocument：同时包含 flat elements 和 heading_tree

标题层级约定：
- H1：文档主标题（通常不生成单独 wiki 页面）
- H2：一级章节（生成 wiki 页面，slug: {parent}-{section}）
- H3：二级章节（可生成子页面或作为内容段落）
- H4-H6：深层章节（作为内容段落或折叠展示）
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
    parent_section: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class HeadingNode:
    level: int
    title: str
    slug: str | None = None
    children: list[HeadingNode] = field(default_factory=list)
    elements: list[ParsedElement] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "title": self.title,
            "slug": self.slug,
            "children": [c.to_dict() for c in self.children],
            "element_count": len(self.elements),
        }


@dataclass
class ParsedDocument:
    doc_id: str
    source_path: str
    format: str
    checksum: str
    title: str | None = None
    elements: list[ParsedElement] = field(default_factory=list)
    heading_tree: list[HeadingNode] = field(default_factory=list)
    imported_at: str | None = None

    def get_heading_tree_dict(self) -> list[dict]:
        return [node.to_dict() for node in self.heading_tree]


@runtime_checkable
class DocumentParser(Protocol):
    format: str

    def parse(self, path: str, doc_id: str) -> ParsedDocument: ...
