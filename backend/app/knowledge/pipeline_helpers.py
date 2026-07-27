"""流水线产物序列化/反序列化辅助

将 ParsedDocument / ExtractionResult 等 dataclass 转换为 JSON 可序列化的 dict，
支持从持久化的 artifact 重建对象以支持"从任意阶段重处理"。

阶段对应关系：
- parse 阶段输出 → serialize_parsed_doc → 反序列化为 ParsedDocument
- extract 阶段输出 → serialize_extraction_result → 反序列化为 ExtractionResult
- compile 阶段输入 → serialize_parsed_doc + serialize_extraction_result
- compile 阶段输出 → serialize_compile_result（仅展示用，不可重建）
"""

from __future__ import annotations

from typing import Any

from app.extraction.types import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)
from app.parsers.base import ElementType, HeadingNode, ParsedDocument, ParsedElement

# ────────── 序列化 ──────────


def serialize_parsed_doc(doc: ParsedDocument) -> dict:
    """ParsedDocument → JSON 可序列化 dict"""
    return {
        "doc_id": doc.doc_id,
        "source_path": doc.source_path,
        "format": doc.format,
        "checksum": doc.checksum,
        "title": doc.title,
        "imported_at": doc.imported_at,
        "element_count": len(doc.elements),
        "elements": [_serialize_element(e) for e in doc.elements],
        "heading_tree": [n.to_dict() for n in doc.heading_tree],
    }


def serialize_extraction_result(extraction: ExtractionResult) -> dict:
    """ExtractionResult → JSON 可序列化 dict"""
    return {
        "doc_id": extraction.doc_id,
        "entities": [_serialize_entity(e) for e in extraction.entities],
        "relations": [_serialize_relation(r) for r in extraction.relations],
        "auto_accepted_entities": [
            _serialize_entity(e) for e in extraction.auto_accepted_entities
        ],
        "review_entities": [
            _serialize_entity(e) for e in extraction.review_entities
        ],
        "auto_accepted_relations": [
            _serialize_relation(r) for r in extraction.auto_accepted_relations
        ],
        "review_relations": [
            _serialize_relation(r) for r in extraction.review_relations
        ],
        "discarded_count": extraction.discarded_count,
    }


def serialize_compile_result_summary(result: Any) -> dict:
    """WikiCompileResult → 可展示的摘要 dict（不保留全部页面内容）"""
    return {
        "doc_id": getattr(result, "doc_id", ""),
        "pages_created": getattr(result, "pages_created", 0),
        "pages_updated": getattr(result, "pages_updated", 0),
        "pages_unchanged": getattr(result, "pages_unchanged", 0),
        "slugs": list(getattr(result, "slugs", []) or []),
        "review_needed": list(getattr(result, "review_needed", []) or []),
        "stale_marked": list(getattr(result, "stale_marked", []) or []),
        "errors": list(getattr(result, "errors", []) or []),
        "index_rebuilt": getattr(result, "index_rebuilt", False),
        "graph_compiled": getattr(result, "graph_compiled", False),
        "paragraph_count": getattr(result, "paragraph_count", 0),
    }


def _serialize_entity(e: ExtractedEntity) -> dict:
    return {
        "entity_type": e.entity_type,
        "name": e.name,
        "properties": e.properties,
        "confidence": e.confidence,
        "evidence_span": e.evidence_span,
        "source_doc_id": e.source_doc_id,
    }


def _serialize_relation(r: ExtractedRelation) -> dict:
    return {
        "relation_type": r.relation_type,
        "from_entity": r.from_entity,
        "to_entity": r.to_entity,
        "properties": r.properties,
        "confidence": r.confidence,
        "evidence_span": r.evidence_span,
        "source_doc_id": r.source_doc_id,
    }


def _serialize_element(e: ParsedElement) -> dict:
    return {
        "type": e.type.value if isinstance(e.type, ElementType) else str(e.type),
        "content": e.content,
        "page": e.page,
        "section": e.section,
        "parent_section": e.parent_section,
        "metadata": e.metadata,
    }


# ────────── 反序列化 ──────────


def deserialize_parsed_doc(data: dict) -> ParsedDocument:
    """从 dict 重建 ParsedDocument（用于从 extract 阶段重处理）"""
    elements = [_deserialize_element(e) for e in data.get("elements", [])]
    heading_tree = [_deserialize_heading_node(n) for n in data.get("heading_tree", [])]
    return ParsedDocument(
        doc_id=data["doc_id"],
        source_path=data.get("source_path", ""),
        format=data.get("format", ""),
        checksum=data.get("checksum", ""),
        title=data.get("title"),
        elements=elements,
        heading_tree=heading_tree,
        imported_at=data.get("imported_at"),
    )


def deserialize_extraction_result(data: dict) -> ExtractionResult:
    """从 dict 重建 ExtractionResult（用于从 compile 阶段重处理）"""
    return ExtractionResult(
        doc_id=data.get("doc_id", ""),
        entities=[_deserialize_entity(e) for e in data.get("entities", [])],
        relations=[_deserialize_relation(r) for r in data.get("relations", [])],
        auto_accepted_entities=[
            _deserialize_entity(e) for e in data.get("auto_accepted_entities", [])
        ],
        review_entities=[
            _deserialize_entity(e) for e in data.get("review_entities", [])
        ],
        auto_accepted_relations=[
            _deserialize_relation(r)
            for r in data.get("auto_accepted_relations", [])
        ],
        review_relations=[
            _deserialize_relation(r) for r in data.get("review_relations", [])
        ],
        discarded_count=data.get("discarded_count", 0),
    )


def _deserialize_element(d: dict) -> ParsedElement:
    type_val = d.get("type", "text")
    try:
        etype = ElementType(type_val)
    except ValueError:
        etype = ElementType.TEXT
    return ParsedElement(
        type=etype,
        content=d.get("content", ""),
        page=d.get("page"),
        section=d.get("section"),
        parent_section=d.get("parent_section"),
        metadata=d.get("metadata", {}),
    )


def _deserialize_heading_node(d: dict) -> HeadingNode:
    node = HeadingNode(
        level=d.get("level", 1),
        title=d.get("title", ""),
        slug=d.get("slug"),
    )
    node.children = [_deserialize_heading_node(c) for c in d.get("children", [])]
    node.elements = [_deserialize_element(e) for e in d.get("elements", [])]
    return node


def _deserialize_entity(d: dict) -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=d.get("entity_type", "Concept"),
        name=d.get("name", ""),
        properties=d.get("properties", {}),
        confidence=d.get("confidence", 0.0),
        evidence_span=d.get("evidence_span", ""),
        source_doc_id=d.get("source_doc_id", ""),
    )


def _deserialize_relation(d: dict) -> ExtractedRelation:
    return ExtractedRelation(
        relation_type=d.get("relation_type", "RELATED_TO"),
        from_entity=d.get("from_entity", ""),
        to_entity=d.get("to_entity", ""),
        properties=d.get("properties", {}),
        confidence=d.get("confidence", 0.0),
        evidence_span=d.get("evidence_span", ""),
        source_doc_id=d.get("source_doc_id", ""),
    )
