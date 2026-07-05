"""FastAPI 应用入口 + 解析器 API（W3） + 抽取 API（W4） + 图谱 API（W5） + 编译 API（W6）"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.parsers import get_parser, supported_formats
from app.parsers.base import ParsedDocument, ParsedElement
from app.extraction import KnowledgeExtractor, ExtractionStats
from app.knowledge import (
    GraphEntity, GraphRelation, get_graph_store, get_compiler, get_pipeline,
)

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


# ────────── W5 图谱存储 API ──────────

@app.post("/graph/upload")
async def graph_upload(file: UploadFile = File(...)) -> dict:
    """解析文档 → 抽取知识 → 编译 → 写入图谱（全流水线）"""
    import tempfile, os

    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    fmt = EXT_FMT_MAP.get(ext, ext)
    if fmt not in supported_formats():
        raise HTTPException(400, f"不支持的格式: {fmt}")

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

        # 转换为 GraphEntity/GraphRelation
        entities = [
            GraphEntity(
                entity_type=e.entity_type, name=e.name,
                properties=e.properties, source_doc_id=doc.doc_id,
                confidence=e.confidence,
            )
            for e in result.auto_accepted_entities
        ]
        relations = [
            GraphRelation(
                relation_type=r.relation_type, from_entity=r.from_entity,
                to_entity=r.to_entity, properties=r.properties,
                source_doc_id=doc.doc_id, confidence=r.confidence,
            )
            for r in result.auto_accepted_relations
        ]

        # 编译 + 写入
        compiler = get_compiler()
        compile_result = compiler.compile_and_store(entities, relations)

        return {
            "doc_id": doc.doc_id,
            "title": doc.title,
            "format": doc.format,
            "parsed_elements": len(doc.elements),
            "extracted_entities": len(entities),
            "extracted_relations": len(relations),
            "compile": {
                "input": compile_result.input_entities,
                "after_dedup": compile_result.after_dedup,
                "merged": compile_result.merged,
                "scored": compile_result.scored,
            },
            "review_entities": len(result.review_entities),
            "review_relations": len(result.review_relations),
            "discarded": result.discarded_count,
        }
    finally:
        os.unlink(tmp_path)


@app.get("/graph/stats")
async def graph_stats() -> dict:
    """获取图谱统计信息"""
    try:
        store = get_graph_store()
        return store.get_stats()
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用"}


@app.get("/graph/entity/{name}")
async def graph_entity(name: str) -> dict:
    """查询实体及其邻居"""
    try:
        store = get_graph_store()
        entity = store.query_entity(name)
        if not entity:
            raise HTTPException(404, f"实体不存在: {name}")
        related = store.query_related(name)
        return {"entity": entity, "related": related}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用"}


@app.get("/graph/search")
async def graph_search(q: str = Query(..., min_length=1), limit: int = 20) -> dict:
    """搜索图谱实体"""
    try:
        store = get_graph_store()
        results = store.search_entities(q, limit)
        return {"query": q, "results": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用"}


@app.get("/graph/by-type/{entity_type}")
async def graph_by_type(entity_type: str, limit: int = 50) -> dict:
    """按类型查询实体"""
    try:
        store = get_graph_store()
        results = store.query_by_type(entity_type, limit)
        return {"entity_type": entity_type, "results": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用"}


# ────────── W6 编译 API ──────────

@app.post("/compile")
async def compile_knowledge(file: UploadFile = File(...)) -> dict:
    """解析 → 抽取 → 编译（不写入图谱，仅返回编译结果）"""
    import tempfile, os

    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
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
                entity_type=e.entity_type, name=e.name,
                properties=e.properties, source_doc_id=doc.doc_id,
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


# ────────── W7 文档生成 API ──────────

from pydantic import BaseModel


class DocGenRequest(BaseModel):
    request: str
    context: str = ""
    max_iterations: int | None = None


@app.post("/doc/generate")
async def generate_document(req: DocGenRequest) -> dict:
    """多智能体文档生成（完整流水线）"""
    pipeline = get_pipeline()
    state = await pipeline.generate(
        request=req.request,
        context=req.context,
        max_iterations=req.max_iterations,
    )
    return {
        "document": state.get("final_document", ""),
        "outline": state.get("outline", []),
        "sections": [
            {"title": s.get("title", ""), "content": s.get("content", "")[:500]}
            for s in state.get("sections", [])
        ],
        "iterations": state.get("iteration", 0),
        "token_usage": state.get("token_usage", 0),
        "error": state.get("error", ""),
    }


@app.post("/doc/generate-from-knowledge")
async def generate_from_knowledge(req: DocGenRequest) -> dict:
    """从知识图谱检索上下文 → 文档生成"""
    pipeline = get_pipeline()

    # 尝试从图谱检索相关上下文
    context = req.context
    if not context:
        try:
            store = get_graph_store()
            # 搜索与请求相关的实体
            search_results = store.search_entities(req.request[:50], limit=10)
            if search_results:
                context_parts = []
                for r in search_results:
                    entity = store.query_entity(r["name"])
                    if entity:
                        context_parts.append(f"实体: {r['name']} (类型: {r['type']})")
                        for k, v in entity.items():
                            if k not in ("name", "entity_type", "updated_at"):
                                context_parts.append(f"  {k}: {v}")
                context = "\n".join(context_parts)
        except Exception:
            pass  # Neo4j 不可用时跳过

    state = await pipeline.generate(
        request=req.request,
        context=context,
        max_iterations=req.max_iterations,
    )
    return {
        "document": state.get("final_document", ""),
        "outline": state.get("outline", []),
        "context_used": bool(context),
        "iterations": state.get("iteration", 0),
        "token_usage": state.get("token_usage", 0),
        "error": state.get("error", ""),
    }