"""FastAPI 应用入口 + 解析器 API 路由（W3） + 抽取 API 路由（W4）"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.parsers import get_parser, supported_formats
from app.parsers.base import ParsedDocument, ParsedElement
from app.extraction import KnowledgeExtractor, ExtractionStats

logger = structlog.get_logger()

# 扩展名 → 格式映射（统一）
EXT_FMT_MAP = {
    "md": "markdown", "sql": "sql", "txt": "txt",
    "docx": "word", "doc": "word", "xlsx": "excel", "xls": "excel",
    "pptx": "ppt", "ppt": "ppt", "html": "html", "htm": "html",
    "pdf": "pdf", "epub": "epub", "csv": "csv", "json": "json",
    "png": "png", "jpg": "jpg", "jpeg": "jpg", "gif": "gif", "bmp": "bmp",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info("backend.starting", env=settings.env, llm_backend=settings.llm_backend)
    yield
    logger.info("backend.stopping")


app = FastAPI(title="OpsKG Backend", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/parsers")
async def list_parsers() -> dict[str, list[str]]:
    return {"formats": supported_formats()}


@app.post("/parsers/parse/batch")
async def parse_batch(files: list[UploadFile] = File(..., alias="files")) -> dict:
    """批量解析，自动检测格式"""
    results = []
    formats = set(supported_formats())

    for file in files:
        ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
        fmt = EXT_FMT_MAP.get(ext, ext)
        if fmt not in formats:
            results.append({"filename": file.filename, "error": f"不支持的格式: {fmt}"})
            continue

        import tempfile, os
        suffix = os.path.splitext(file.filename or "")[1] or f".{fmt}"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        try:
            parser = get_parser(fmt)
            doc = parser.parse(tmp_path, file.filename or "unknown")
            results.append({"filename": file.filename, "format": fmt, "doc": _serialize_doc(doc)})
        except Exception as e:
            results.append({"filename": file.filename, "format": fmt, "error": str(e)})
        finally:
            os.unlink(tmp_path)

    return {"results": results}


@app.post("/parsers/parse/{fmt}")
async def parse_file(fmt: str, file: UploadFile = File(...)) -> dict:
    """解析单个文件，返回 ParsedDocument 结构"""
    if fmt not in supported_formats():
        raise HTTPException(400, f"不支持的格式: {fmt}。支持: {supported_formats()}")

    import tempfile, os

    suffix = os.path.splitext(file.filename or "")[1] or f".{fmt}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        parser = get_parser(fmt)
        doc = parser.parse(tmp_path, file.filename or "unknown")
        return _serialize_doc(doc)
    finally:
        os.unlink(tmp_path)


def _serialize_doc(doc: ParsedDocument) -> dict:
    return {
        "doc_id": doc.doc_id,
        "source_path": doc.source_path,
        "format": doc.format,
        "checksum": doc.checksum,
        "title": doc.title,
        "element_count": len(doc.elements),
        "elements": [
            {"type": e.type.value, "content": e.content[:2000],
             "section": e.section, "metadata": e.metadata}
            for e in doc.elements[:50]  # API 响应限制前 50 个元素
        ],
    }


# ────────── W4 抽取 API ──────────

@app.post("/extract", response_model=dict)
async def extract_knowledge(file: UploadFile = File(...)) -> dict:
    """解析文档 → 抽取知识 → 置信度门控，返回完整结果"""
    import tempfile, os

    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
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
                {"type": e.entity_type, "name": e.name, "confidence": e.confidence,
                 "evidence": e.evidence_span[:200]}
                for e in result.auto_accepted_entities[:30]
            ],
            "review_needed": [
                {"type": e.entity_type, "name": e.name, "confidence": e.confidence,
                 "evidence": e.evidence_span[:200]}
                for e in result.review_entities[:30]
            ],
            "auto_accepted_relations": [
                {"type": r.relation_type, "from": r.from_entity, "to": r.to_entity,
                 "confidence": r.confidence}
                for r in result.auto_accepted_relations[:20]
            ],
        }
    finally:
        os.unlink(tmp_path)


@app.get("/extract/review-queue")
async def get_review_queue() -> dict:
    """获取建议审查队列（占位，W6 实现持久化后返回实际数据）"""
    return {"items": [], "note": "W6 实现：从 PostgreSQL 查询待审查条目"}