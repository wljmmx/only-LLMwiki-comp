"""抽取 API（W4）+ 编译 API（W6）。

端点：
- POST /extract  解析文档 → 抽取知识 → 置信度门控
- POST /compile  解析 → 抽取 → 编译（不写入图谱）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.auth import verify_token
from app.extraction import KnowledgeExtractor
from app.knowledge import GraphEntity, get_compiler
from app.parsers import get_parser, supported_formats
from app.routers.parsers_router import EXT_FMT_MAP

router = APIRouter()


# ────────── W4 抽取 API ──────────


@router.post("/extract", response_model=dict, dependencies=[Depends(verify_token)])
async def extract_knowledge(file: UploadFile = File(...)) -> dict:
    """解析文档 → 抽取知识 → 置信度门控，返回完整结果"""
    import tempfile
    import os

    ext = (
        (file.filename or "").rsplit(".", 1)[-1].lower()
        if "." in (file.filename or "")
        else ""
    )
    fmt = EXT_FMT_MAP.get(ext, ext)

    if fmt not in supported_formats():
        raise HTTPException(400, f"不支持的格式: {fmt}。支持: {supported_formats()}")

    suffix = os.path.splitext(file.filename or "")[1] or f".{fmt}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        # 解析
        parser = get_parser(fmt)
        doc = parser.parse(tmp_path, file.filename or "unknown")

        # 抽取
        extractor = KnowledgeExtractor()
        result = await extractor.extract(doc)
        stats = extractor.get_stats(result)

        return {
            "doc_id": doc.doc_id,
            "title": doc.title,
            "format": doc.format,
            "element_count": len(doc.elements),
            "stats": {
                "total_entities": stats.total_entities,
                "auto_accepted": stats.auto_accepted,
                "review_needed": stats.review_needed,
                "discarded": stats.discarded,
                "confidence_avg": round(stats.confidence_avg, 3),
            },
            "auto_accepted": [
                {
                    "type": e.entity_type,
                    "name": e.name,
                    "confidence": e.confidence,
                    "evidence": e.evidence_span[:200],
                }
                for e in result.auto_accepted_entities[:30]
            ],
            "review_needed": [
                {
                    "type": e.entity_type,
                    "name": e.name,
                    "confidence": e.confidence,
                    "evidence": e.evidence_span[:200],
                }
                for e in result.review_entities[:30]
            ],
            "auto_accepted_relations": [
                {
                    "type": r.relation_type,
                    "from": r.from_entity,
                    "to": r.to_entity,
                    "confidence": r.confidence,
                }
                for r in result.auto_accepted_relations[:20]
            ],
        }
    finally:
        os.unlink(tmp_path)


# ────────── W6 编译 API ──────────


@router.post("/compile", dependencies=[Depends(verify_token)])
async def compile_knowledge(file: UploadFile = File(...)) -> dict:
    """解析 → 抽取 → 编译（不写入图谱，仅返回编译结果）"""
    import tempfile
    import os

    ext = (
        (file.filename or "").rsplit(".", 1)[-1].lower()
        if "." in (file.filename or "")
        else ""
    )
    fmt = EXT_FMT_MAP.get(ext, ext)
    if fmt not in supported_formats():
        raise HTTPException(400, f"不支持的格式: {fmt}")

    suffix = os.path.splitext(file.filename or "")[1] or f".{fmt}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        parser = get_parser(fmt)
        doc = parser.parse(tmp_path, file.filename or "unknown")

        extractor = KnowledgeExtractor()
        result = await extractor.extract(doc)

        entities = [
            GraphEntity(
                entity_type=e.entity_type,
                name=e.name,
                properties=e.properties,
                source_doc_id=doc.doc_id,
                confidence=e.confidence,
            )
            for e in result.auto_accepted_entities
        ]

        compiler = get_compiler()
        compile_result = compiler.compile(entities, [])

        return {
            "doc_id": doc.doc_id,
            "title": doc.title,
            "extracted": len(entities),
            "compile": {
                "input": compile_result.input_entities,
                "after_dedup": compile_result.after_dedup,
                "merged": compile_result.merged,
                "scored": compile_result.scored,
            },
            "review": len(result.review_entities),
            "discarded": result.discarded_count,
        }
    finally:
        os.unlink(tmp_path)
