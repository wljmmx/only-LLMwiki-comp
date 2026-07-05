"""FastAPI 应用入口 + 解析器 API（W3） + 抽取 API（W4） + 图谱 API（W5） + 编译 API（W6） + 文档存储 API（P0）"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response

from app.config import get_settings
from app.parsers import get_parser, supported_formats
from app.parsers.base import ParsedDocument, ParsedElement
from app.extraction import KnowledgeExtractor, ExtractionStats
from app.knowledge import (
    GraphEntity, GraphRelation, get_graph_store, get_compiler, get_pipeline,
    get_review_queue,
)
from app.storage import get_document_store, get_version_control
from app.auth import verify_token
from app.search import get_search_engine
from app.templates import get_template_manager
from app.export import get_exporter
from app.aiops import get_event_correlator

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


@app.post("/parsers/parse/batch", dependencies=[Depends(verify_token)])
async def parse_batch(files: list[UploadFile] = File(..., alias="files")) -> dict:
    """批量解析，自动检测格式（文件持久化存储）"""
    results = []
    formats = set(supported_formats())
    store = get_document_store()

    for file in files:
        ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
        fmt = EXT_FMT_MAP.get(ext, ext)
        if fmt not in formats:
            results.append({"filename": file.filename, "error": f"不支持的格式: {fmt}"})
            continue

        content = await file.read()
        # 持久化存储
        doc_meta = store.save(file.filename or "unknown", content, fmt)
        stored_path = doc_meta["stored_path"]

        try:
            parser = get_parser(fmt)
            doc = parser.parse(stored_path, doc_meta["doc_id"])
            store.update_status(doc_meta["doc_id"], "parsed", title=doc.title)
            results.append({
                "filename": file.filename, "format": fmt,
                "doc_id": doc_meta["doc_id"],
                "doc": _serialize_doc(doc),
            })
        except Exception as e:
            store.update_status(doc_meta["doc_id"], "error")
            results.append({"filename": file.filename, "format": fmt, "error": str(e)})

    return {"results": results}


@app.post("/parsers/parse/{fmt}", dependencies=[Depends(verify_token)])
async def parse_file(fmt: str, file: UploadFile = File(...)) -> dict:
    """解析单个文件（持久化存储），返回 ParsedDocument 结构"""
    if fmt not in supported_formats():
        raise HTTPException(400, f"不支持的格式: {fmt}。支持: {supported_formats()}")

    content = await file.read()
    store = get_document_store()
    doc_meta = store.save(file.filename or "unknown", content, fmt)
    stored_path = doc_meta["stored_path"]

    try:
        parser = get_parser(fmt)
        doc = parser.parse(stored_path, doc_meta["doc_id"])
        store.update_status(doc_meta["doc_id"], "parsed", title=doc.title)
        # 建立搜索索引
        content_text = " ".join(e.content for e in doc.elements if e.content)
        get_search_engine().index_document(doc_meta["doc_id"], doc.title, content_text, fmt)
        result = _serialize_doc(doc)
        result["doc_id"] = doc_meta["doc_id"]
        result["stored"] = True
        return result
    except Exception as e:
        store.update_status(doc_meta["doc_id"], "error")
        raise HTTPException(500, f"解析失败: {e}")


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

@app.post("/extract", response_model=dict, dependencies=[Depends(verify_token)])
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


# ────────── W8 审查队列 API ──────────

@app.get("/review/queue")
async def list_review_queue(
    status: str = "pending",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """获取审查队列"""
    queue = get_review_queue()
    if status == "pending":
        items = queue.list_pending(limit, offset)
    else:
        items = []  # 其他状态通过 stats 查询
    stats = queue.get_stats()
    return {"items": items, "stats": stats, "limit": limit, "offset": offset}


@app.get("/review/stats")
async def review_stats() -> dict:
    """审查队列统计"""
    queue = get_review_queue()
    return queue.get_stats()


@app.post("/review/{item_id}/approve", dependencies=[Depends(verify_token)])
async def review_approve(item_id: int, note: str = "") -> dict:
    """批准审查项"""
    queue = get_review_queue()
    ok = queue.approve(item_id, note)
    if not ok:
        raise HTTPException(404, f"审查项不存在: {item_id}")
    return {"id": item_id, "status": "approved"}


@app.post("/review/{item_id}/reject", dependencies=[Depends(verify_token)])
async def review_reject(item_id: int, note: str = "") -> dict:
    """驳回审查项"""
    queue = get_review_queue()
    ok = queue.reject(item_id, note)
    if not ok:
        raise HTTPException(404, f"审查项不存在: {item_id}")
    return {"id": item_id, "status": "rejected"}


@app.post("/review/batch-approve", dependencies=[Depends(verify_token)])
async def review_batch_approve(item_ids: list[int]) -> dict:
    """批量批准"""
    queue = get_review_queue()
    count = queue.batch_approve(item_ids)
    return {"approved": count}


# ────────── W5 图谱存储 API ──────────

@app.post("/graph/upload", dependencies=[Depends(verify_token)])
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

        # 审查项存入审查队列
        review_queue = get_review_queue()
        review_entities_data = [
            {"entity_type": e.entity_type, "name": e.name,
             "properties": e.properties, "confidence": e.confidence,
             "evidence_span": e.evidence_span, "source_doc_id": doc.doc_id}
            for e in result.review_entities
        ]
        review_relations_data = [
            {"relation_type": r.relation_type, "from_entity": r.from_entity,
             "to_entity": r.to_entity, "properties": r.properties,
             "confidence": r.confidence, "evidence_span": r.evidence_span,
             "source_doc_id": doc.doc_id}
            for r in result.review_relations
        ]
        review_result = review_queue.batch_add(review_entities_data, review_relations_data)

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
            "review_queued": review_result,
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


@app.get("/graph/visualize")
async def graph_visualize(entity_type: str | None = None, limit: int = 100) -> dict:
    """图谱可视化数据（D3.js/vis.js force-directed graph 格式）"""
    try:
        store = get_graph_store()
        driver = store.driver

        with driver.session() as session:
            if entity_type:
                result = session.run(
                    """
                    MATCH (n:Entity {entity_type: $type})-[r]-(m:Entity)
                    RETURN n.name AS source, n.entity_type AS source_type,
                           type(r) AS relation, m.name AS target,
                           m.entity_type AS target_type, r.confidence AS confidence
                    LIMIT $limit
                    """,
                    type=entity_type, limit=limit,
                )
            else:
                result = session.run(
                    """
                    MATCH (n:Entity)-[r]-(m:Entity)
                    RETURN n.name AS source, n.entity_type AS source_type,
                           type(r) AS relation, m.name AS target,
                           m.entity_type AS target_type, r.confidence AS confidence
                    LIMIT $limit
                    """,
                    limit=limit,
                )

            records = [dict(r) for r in result]

            # 构建 D3.js 格式：{nodes: [...], links: [...]}
            node_map = {}
            links = []
            for rec in records:
                src_id = rec["source"]
                tgt_id = rec["target"]
                if src_id not in node_map:
                    node_map[src_id] = {"id": src_id, "type": rec["source_type"], "group": _entity_group(rec["source_type"])}
                if tgt_id not in node_map:
                    node_map[tgt_id] = {"id": tgt_id, "type": rec["target_type"], "group": _entity_group(rec["target_type"])}
                links.append({
                    "source": src_id, "target": tgt_id,
                    "type": rec["relation"], "confidence": rec["confidence"],
                })

            return {
                "nodes": list(node_map.values()),
                "links": links,
                "node_count": len(node_map),
                "link_count": len(links),
            }
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用", "nodes": [], "links": []}


def _entity_group(entity_type: str) -> int:
    """实体类型 → D3.js 颜色分组"""
    groups = {
        "Host": 1, "Service": 2, "Component": 3, "Parameter": 4,
        "Command": 5, "Procedure": 6, "Incident": 7, "Symptom": 8,
        "Experience": 9, "Concept": 10, "Document": 11,
    }
    return groups.get(entity_type, 0)


# ────────── W6 编译 API ──────────

@app.post("/compile", dependencies=[Depends(verify_token)])
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


@app.post("/doc/generate", dependencies=[Depends(verify_token)])
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


@app.post("/doc/generate-from-knowledge", dependencies=[Depends(verify_token)])
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


# ──────────────────────────────────────────────────────────────────
# P0-1: 文档管理 API
# ──────────────────────────────────────────────────────────────────

@app.get("/documents")
async def list_documents(
    limit: int = 50,
    offset: int = 0,
    format: str | None = None,
    status: str | None = None,
) -> dict:
    """列出所有存储的文档"""
    store = get_document_store()
    docs = store.list(limit, offset, format, status)
    stats = store.get_stats()
    return {"documents": docs, "stats": stats, "limit": limit, "offset": offset}


@app.get("/documents/stats")
async def document_stats() -> dict:
    """文档统计"""
    store = get_document_store()
    return store.get_stats()


@app.get("/documents/search")
async def search_documents(q: str, limit: int = 20) -> dict:
    """搜索文档（按文件名/标题）"""
    store = get_document_store()
    results = store.search(q, limit)
    return {"query": q, "results": results, "count": len(results)}


@app.get("/documents/{doc_id}")
async def get_document(doc_id: str) -> dict:
    """获取文档元数据"""
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    return doc


@app.get("/documents/{doc_id}/content")
async def get_document_content(doc_id: str) -> Response:
    """下载文档原始内容"""
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    content = store.read_content(doc_id)
    if content is None:
        raise HTTPException(404, "文件内容不存在（可能已被删除）")
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{doc["filename"]}"'},
    )


@app.delete("/documents/{doc_id}", dependencies=[Depends(verify_token)])
async def delete_document(doc_id: str) -> dict:
    """删除文档（文件+元数据+索引）"""
    store = get_document_store()
    ok = store.delete(doc_id)
    if not ok:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    get_search_engine().remove_index(doc_id)
    return {"deleted": True, "doc_id": doc_id}


# ──────────────────────────────────────────────────────────────────
# P1-1: 搜索 API
# ──────────────────────────────────────────────────────────────────

@app.get("/search")
async def search(
    q: str,
    limit: int = 20,
    use_vector: bool = False,
) -> dict:
    """混合检索（关键字 + 向量）

    - q: 搜索关键词
    - use_vector: 是否启用向量检索（需配合 LLM embedding，默认关闭）
    """
    engine = get_search_engine()
    query_embedding = None
    if use_vector:
        # 通过 LLM 生成 query embedding（如果后端支持）
        try:
            from app.core.llm import get_llm_client, ChatMessage
            client = get_llm_client()
            # 简单实现：用 LLM 生成文本的 embedding（如果支持）
            # 实际生产中应调用 embedding API
        except Exception:
            pass

    results = engine.search(q, limit, query_embedding)
    return {
        "query": q,
        "results": results,
        "count": len(results),
        "vector_enabled": use_vector,
    }


@app.get("/search/stats")
async def search_stats() -> dict:
    """搜索索引统计"""
    engine = get_search_engine()
    return engine.get_stats()


@app.post("/search/reindex/{doc_id}", dependencies=[Depends(verify_token)])
async def reindex_document(doc_id: str) -> dict:
    """重新索引指定文档"""
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    # 重新解析并索引
    try:
        fmt = doc["format"]
        parser = get_parser(fmt)
        parsed = parser.parse(doc["stored_path"], doc_id)
        content_text = " ".join(e.content for e in parsed.elements if e.content)
        get_search_engine().index_document(doc_id, parsed.title, content_text, fmt)
        return {"reindexed": True, "doc_id": doc_id, "title": parsed.title}
    except Exception as e:
        raise HTTPException(500, f"重新索引失败: {e}")


# ──────────────────────────────────────────────────────────────────
# P1-2: 版本控制 API
# ──────────────────────────────────────────────────────────────────

@app.get("/versions/{doc_key}")
async def list_versions(doc_key: str) -> dict:
    """列出文档的所有版本"""
    vc = get_version_control()
    versions = vc.list_versions(doc_key)
    return {"doc_key": doc_key, "versions": versions, "count": len(versions)}


@app.get("/versions/{doc_key}/{version}")
async def get_version(doc_key: str, version: int) -> dict:
    """获取指定版本内容"""
    vc = get_version_control()
    v = vc.get_version(doc_key, version)
    if not v:
        raise HTTPException(404, f"版本不存在: {doc_key} v{version}")
    return v


@app.get("/versions/{doc_key}/diff/{v1}/{v2}")
async def diff_versions(doc_key: str, v1: int, v2: int) -> dict:
    """对比两个版本"""
    vc = get_version_control()
    return vc.diff(doc_key, v1, v2)


@app.post("/versions/{doc_key}/rollback/{target_version}", dependencies=[Depends(verify_token)])
async def rollback_version(doc_key: str, target_version: int) -> dict:
    """回滚到指定版本"""
    vc = get_version_control()
    result = vc.rollback(doc_key, target_version)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.post("/versions/{doc_key}/save", dependencies=[Depends(verify_token)])
async def save_version(doc_key: str, title: str, content: str, change_summary: str = "") -> dict:
    """保存新版本"""
    vc = get_version_control()
    return vc.save_version(doc_key, title, content, change_summary=change_summary)


# ──────────────────────────────────────────────────────────────────
# P1-3: 模板管理 API
# ──────────────────────────────────────────────────────────────────

@app.get("/templates")
async def list_templates(category: str | None = None) -> dict:
    """列出模板"""
    mgr = get_template_manager()
    templates = mgr.list(category)
    return {"templates": templates, "count": len(templates)}


@app.get("/templates/{slug}")
async def get_template(slug: str) -> dict:
    """获取模板"""
    mgr = get_template_manager()
    tpl = mgr.get(slug)
    if not tpl:
        raise HTTPException(404, f"模板不存在: {slug}")
    return tpl


@app.post("/templates", dependencies=[Depends(verify_token)])
async def create_template(
    slug: str, name: str, content: str,
    category: str = "custom", description: str = "",
) -> dict:
    """创建自定义模板"""
    mgr = get_template_manager()
    try:
        return mgr.create(slug, name, content, category, description)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.put("/templates/{slug}", dependencies=[Depends(verify_token)])
async def update_template(
    slug: str,
    name: str | None = None,
    content: str | None = None,
    category: str | None = None,
    description: str | None = None,
) -> dict:
    """更新模板"""
    mgr = get_template_manager()
    try:
        result = mgr.update(slug, name, content, category, description)
        if not result:
            raise HTTPException(404, f"模板不存在: {slug}")
        return result
    except ValueError as e:
        raise HTTPException(403, str(e))


@app.delete("/templates/{slug}", dependencies=[Depends(verify_token)])
async def delete_template(slug: str) -> dict:
    """删除模板（仅自定义）"""
    mgr = get_template_manager()
    try:
        ok = mgr.delete(slug)
        if not ok:
            raise HTTPException(404, f"模板不存在: {slug}")
        return {"deleted": True, "slug": slug}
    except ValueError as e:
        raise HTTPException(403, str(e))


@app.post("/templates/{slug}/render")
async def render_template(slug: str, variables: dict) -> dict:
    """渲染模板"""
    mgr = get_template_manager()
    try:
        rendered = mgr.render(slug, variables)
        return {"slug": slug, "rendered": rendered, "length": len(rendered)}
    except ValueError as e:
        raise HTTPException(404, str(e))


# ──────────────────────────────────────────────────────────────────
# P1-4: 导出 API
# ──────────────────────────────────────────────────────────────────

@app.post("/export", dependencies=[Depends(verify_token)])
async def export_document(payload: dict) -> Response:
    """导出文档

    format: markdown | html | text | pdf
    """
    title = payload.get("title", "untitled")
    content = payload.get("content", "")
    fmt = payload.get("format", "markdown")
    exporter = get_exporter()
    try:
        content_bytes, media_type, ext = exporter.export(title, content, fmt)
        safe_title = title.replace("/", "_").replace("\\", "_")[:50]
        # RFC 5987: 支持 non-ASCII 文件名
        from urllib.parse import quote
        quoted = quote(safe_title)
        return Response(
            content=content_bytes,
            media_type=media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{quoted}{ext}"; '
                    f"filename*=UTF-8''{quoted}{ext}"
                ),
            },
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))


# ──────────────────────────────────────────────────────────────────
# P1-5: Wiki 发布工作流
# ──────────────────────────────────────────────────────────────────

@app.get("/wiki")
async def wiki_list(limit: int = 50, offset: int = 0) -> dict:
    """列出已发布的 Wiki 文档"""
    vc = get_version_control()
    # Wiki 文档以 wiki: 前缀存储
    conn = vc._get_db() if hasattr(vc, "_get_db") else None
    from app.storage.version_control import _get_db as get_vc_db
    conn = get_vc_db()
    rows = conn.execute(
        """SELECT DISTINCT doc_key FROM document_versions
           WHERE doc_key LIKE 'wiki:%' ORDER BY doc_key LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    wiki_docs = []
    for r in rows:
        latest = vc.get_latest(r["doc_key"])
        if latest:
            wiki_docs.append({
                "slug": r["doc_key"].replace("wiki:", "", 1),
                "title": latest["title"],
                "version": latest["version"],
                "updated_at": latest["created_at"],
            })
    return {"documents": wiki_docs, "count": len(wiki_docs)}


@app.get("/wiki/{slug}")
async def wiki_get(slug: str) -> dict:
    """获取 Wiki 文档内容"""
    vc = get_version_control()
    doc_key = f"wiki:{slug}"
    latest = vc.get_latest(doc_key)
    if not latest:
        raise HTTPException(404, f"Wiki 文档不存在: {slug}")
    versions = vc.list_versions(doc_key)
    return {
        "slug": slug,
        "title": latest["title"],
        "content": latest["content"],
        "version": latest["version"],
        "versions": versions,
        "updated_at": latest["created_at"],
    }


@app.post("/wiki/{slug}", dependencies=[Depends(verify_token)])
async def wiki_publish(
    slug: str,
    title: str,
    content: str,
    change_summary: str = "",
) -> dict:
    """发布/更新 Wiki 文档（自动创建新版本）"""
    vc = get_version_control()
    doc_key = f"wiki:{slug}"
    result = vc.save_version(doc_key, title, content, change_summary=change_summary)
    # 同时建立搜索索引
    get_search_engine().index_document(doc_key, title, content, "wiki")
    return result


@app.delete("/wiki/{slug}", dependencies=[Depends(verify_token)])
async def wiki_delete(slug: str) -> dict:
    """删除 Wiki 文档"""
    vc = get_version_control()
    doc_key = f"wiki:{slug}"
    count = vc.delete_all(doc_key)
    if count == 0:
        raise HTTPException(404, f"Wiki 文档不存在: {slug}")
    get_search_engine().remove_index(doc_key)
    return {"deleted": True, "slug": slug, "versions_removed": count}


# ────────── P2-1 Runbook 自动生成 API ──────────

@app.post("/runbook/generate", dependencies=[Depends(verify_token)])
async def runbook_generate(payload: dict) -> dict:
    """基于知识库自动生成故障处理 Runbook

    Body:
        symptom: 故障现象描述（必填）
        service: 受影响服务（可选）
        host: 受影响主机（可选）
        max_docs: 检索文档数上限（默认 5）
        publish: 是否同时发布为 Wiki（默认 false）
        wiki_slug: Wiki slug（默认 auto:runbook-<timestamp>）
    """
    from app.knowledge import get_runbook_generator

    symptom = (payload.get("symptom") or "").strip()
    if not symptom:
        raise HTTPException(400, "symptom 不能为空")
    service = payload.get("service", "") or ""
    host = payload.get("host", "") or ""
    max_docs = int(payload.get("max_docs", 5))
    publish = bool(payload.get("publish", False))

    gen = get_runbook_generator()
    try:
        result = gen.generate(symptom, service, host, max_docs)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # 可选：发布为 Wiki
    if publish:
        import time
        slug = payload.get("wiki_slug") or f"runbook-{int(time.time())}"
        vc = get_version_control()
        title = f"Runbook: {symptom[:60]}"
        vc.save_version(
            doc_key=f"wiki:{slug}",
            title=title,
            content=result["runbook_md"],
            author="runbook-generator",
            change_summary=f"自动生成: {symptom}",
        )
        get_search_engine().index_document(
            f"wiki:{slug}", title, result["runbook_md"], "runbook",
        )
        result["wiki_slug"] = slug
        result["wiki_published"] = True

    return result


@app.get("/runbook/preview")
async def runbook_preview(
    symptom: str,
    service: str = "",
    host: str = "",
    max_docs: int = 5,
) -> dict:
    """预览 Runbook（GET 版本，便于浏览器/curl 快速调用）

    返回 Markdown 原文 + 来源统计，不写入 Wiki。
    """
    from app.knowledge import get_runbook_generator

    if not symptom.strip():
        raise HTTPException(400, "symptom 不能为空")
    gen = get_runbook_generator()
    return gen.generate(symptom.strip(), service, host, max_docs)


# ────────── P2-2 事件关联 API ──────────

@app.post("/events/ingest", dependencies=[Depends(verify_token)])
async def events_ingest(payload: dict) -> dict:
    """接收告警事件流

    Body:
        events: [Event]  事件列表，每个事件包含:
            - id (可选，缺省自动生成)
            - timestamp (可选，缺省用当前时间)
            - host, service, component (可选)
            - severity: info|low|warning|high|critical|fatal
            - message
            - tags, source, attributes (可选)
    """
    events = payload.get("events") or []
    if not isinstance(events, list) or not events:
        raise HTTPException(400, "events 必须是非空数组")
    corr = get_event_correlator()
    return corr.ingest(events)


@app.post("/events/correlate", dependencies=[Depends(verify_token)])
async def events_correlate(payload: dict = None) -> dict:
    """关联最近事件，输出 incident 分组

    Body (可选):
        since_minutes: 关联时间窗口起点（默认 60）
        max_events: 最多处理事件数（默认 500）
    """
    payload = payload or {}
    since = int(payload.get("since_minutes", 60))
    max_ev = int(payload.get("max_events", 500))
    corr = get_event_correlator()
    return corr.correlate(since_minutes=since, max_events=max_ev)


@app.get("/events/incidents")
async def events_list_incidents(
    status: str = "open",
    limit: int = 50,
) -> dict:
    """列出 incident"""
    corr = get_event_correlator()
    items = corr.list_incidents(status, limit)
    return {"incidents": items, "count": len(items)}


@app.get("/events/incidents/{incident_id}")
async def events_get_incident(incident_id: str) -> dict:
    """获取 incident 详情"""
    corr = get_event_correlator()
    inc = corr.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, f"incident 不存在: {incident_id}")
    return inc


@app.post("/events/incidents/{incident_id}/close", dependencies=[Depends(verify_token)])
async def events_close_incident(incident_id: str, note: str = "") -> dict:
    """关闭 incident"""
    corr = get_event_correlator()
    ok = corr.close_incident(incident_id, note)
    if not ok:
        raise HTTPException(404, f"incident 不存在或已关闭: {incident_id}")
    return {"incident_id": incident_id, "status": "closed"}


@app.post("/events/incidents/{incident_id}/runbook", dependencies=[Depends(verify_token)])
async def events_incident_to_runbook(incident_id: str, publish: bool = False) -> dict:
    """基于 incident 自动生成 Runbook"""
    from app.knowledge import get_runbook_generator

    corr = get_event_correlator()
    inc = corr.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, f"incident 不存在: {incident_id}")

    hint_str = inc.get("runbook_hint") or "{}"
    try:
        hint = json.loads(hint_str)
    except (json.JSONDecodeError, TypeError):
        hint = {}

    symptom = hint.get("symptom") or inc.get("suspected_root_cause") or "未知故障"
    service = hint.get("service", "")
    host = hint.get("host", "")

    gen = get_runbook_generator()
    result = gen.generate(symptom, service, host, max_docs=5)

    # 在 Runbook 顶部附加 incident 元信息
    incident_header = (
        f"> 关联 incident: `{incident_id}` 严重度: {inc['severity']}\n"
        f"> 告警数: {inc.get('alert_count', 0)}  根因推断: {inc.get('suspected_root_cause', '')}\n\n"
    )
    result["runbook_md"] = incident_header + result["runbook_md"]
    result["incident_id"] = incident_id

    if publish:
        import time
        slug = f"incident-{incident_id}"
        vc = get_version_control()
        title = f"Incident Runbook: {symptom[:60]}"
        vc.save_version(
            doc_key=f"wiki:{slug}",
            title=title,
            content=result["runbook_md"],
            author="incident-runbook-generator",
            change_summary=f"基于 incident {incident_id} 自动生成",
        )
        get_search_engine().index_document(
            f"wiki:{slug}", title, result["runbook_md"], "incident-runbook",
        )
        result["wiki_slug"] = slug
        result["wiki_published"] = True

    return result