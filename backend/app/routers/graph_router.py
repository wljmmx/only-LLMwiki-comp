"""图谱存储 API（W5）。

端点：
- POST /graph/upload
- GET  /graph/stats
- GET  /graph/entity/{name}
- GET  /graph/search
- GET  /graph/by-type/{entity_type}
- GET  /graph/visualize
- DELETE /graph/entity/{name}
- DELETE /graph/clear
- GET  /graph/maintenance/overview
- GET  /graph/maintenance/orphan-entities
- GET  /graph/maintenance/low-confidence
- GET  /graph/maintenance/by-source/{doc_id}
- GET  /graph/maintenance/duplicates
- POST /graph/maintenance/bulk-delete
- POST /graph/maintenance/cleanup-low-confidence
- POST /graph/maintenance/cleanup-orphans
- DELETE /graph/by-source/{doc_id}
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.auth import verify_token
from app.extraction import KnowledgeExtractor
from app.knowledge import (
    GraphEntity,
    GraphRelation,
    get_compiler,
    get_graph_store,
    get_review_queue,
)
from app.knowledge.graph_store import _to_jsonable
from app.parsers import get_parser, supported_formats
from app.routers.parsers_router import EXT_FMT_MAP

logger = structlog.get_logger()
router = APIRouter()


@router.post("/graph/upload", dependencies=[Depends(verify_token)])
async def graph_upload(file: UploadFile = File(...)) -> dict:
    """解析文档 → 抽取知识 → 编译 → 写入图谱（全流水线）"""
    import os
    import tempfile

    ext = (
        (file.filename or "").rsplit(".", 1)[-1].lower()
        if "." in (file.filename or "")
        else ""
    )
    fmt = EXT_FMT_MAP.get(ext, ext)
    if fmt not in supported_formats():
        raise HTTPException(400, f"不支持的格式: {fmt}")

    logger.info("graph_upload_start", filename=file.filename, format=fmt)

    suffix = os.path.splitext(file.filename or "")[1] or f".{fmt}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        # 解析
        parser = get_parser(fmt)
        logger.debug("graph_upload_parsing", tmp_path=tmp_path, format=fmt)
        doc = parser.parse(tmp_path, file.filename or "unknown")
        logger.info("graph_upload_parsed", doc_id=doc.doc_id, title=doc.title, elements=len(doc.elements))

        # 抽取
        extractor = KnowledgeExtractor()
        logger.debug("graph_upload_extracting", doc_id=doc.doc_id)
        result = await extractor.extract(doc)
        logger.info("graph_upload_extracted", doc_id=doc.doc_id,
                     auto_accepted=len(result.auto_accepted_entities),
                     review=len(result.review_entities),
                     discarded=result.discarded_count)

        # 转换为 GraphEntity/GraphRelation
        # 同时接受 auto_accepted（高置信度）和 review（中置信度，需人工审查）实体，
        # 与 wiki_compiler._compile_to_graph 保持一致。
        # 修复：旧版仅取 auto_accepted，LLM 不可用时 fallback 实体 confidence=0.65
        # 只进 review，导致图谱写入 0 实体。
        all_entities = list(result.auto_accepted_entities) + list(result.review_entities)
        all_relations = list(result.auto_accepted_relations) + list(result.review_relations)
        entities = [
            GraphEntity(
                entity_type=e.entity_type,
                name=e.name,
                properties=e.properties,
                source_doc_id=doc.doc_id,
                confidence=e.confidence,
            )
            for e in all_entities
        ]
        relations = [
            GraphRelation(
                relation_type=r.relation_type,
                from_entity=r.from_entity,
                to_entity=r.to_entity,
                properties=r.properties,
                source_doc_id=doc.doc_id,
                confidence=r.confidence,
            )
            for r in all_relations
        ]

        # 编译 + 写入
        compiler = get_compiler()
        logger.debug("graph_upload_compiling", doc_id=doc.doc_id, entities=len(entities), relations=len(relations))
        compile_result = compiler.compile_and_store(entities, relations)
        logger.info("graph_upload_compiled", doc_id=doc.doc_id,
                    input_entities=compile_result.input_entities,
                    after_dedup=compile_result.after_dedup,
                    merged=compile_result.merged)

        # 审查项存入审查队列
        review_queue = get_review_queue()
        review_entities_data = [
            {
                "entity_type": e.entity_type,
                "name": e.name,
                "properties": e.properties,
                "confidence": e.confidence,
                "evidence_span": e.evidence_span,
                "source_doc_id": doc.doc_id,
            }
            for e in result.review_entities
        ]
        review_relations_data = [
            {
                "relation_type": r.relation_type,
                "from_entity": r.from_entity,
                "to_entity": r.to_entity,
                "properties": r.properties,
                "confidence": r.confidence,
                "evidence_span": r.evidence_span,
                "source_doc_id": doc.doc_id,
            }
            for r in result.review_relations
        ]
        review_result = review_queue.batch_add(
            review_entities_data, review_relations_data
        )
        logger.info("graph_upload_review_queued", doc_id=doc.doc_id,
                    review_entities=len(result.review_entities),
                    review_relations=len(result.review_relations))

        logger.info("graph_upload_done", doc_id=doc.doc_id,
                    total_entities=len(entities),
                    total_relations=len(relations),
                    graph_written=len(entities))
        return {
            "doc_id": doc.doc_id,
            "title": doc.title,
            "format": doc.format,
            "parsed_elements": len(doc.elements),
            "extracted_entities": len(entities),
            "extracted_relations": len(relations),
            "auto_accepted_entities": len(result.auto_accepted_entities),
            "review_entities": len(result.review_entities),
            "graph_written_entities": len(entities),
            "compile": {
                "input": compile_result.input_entities,
                "after_dedup": compile_result.after_dedup,
                "merged": compile_result.merged,
                "scored": compile_result.scored,
            },
            "review_entities_queued": len(result.review_entities),
            "review_relations_queued": len(result.review_relations),
            "review_queued": review_result,
            "discarded": result.discarded_count,
        }
    except Exception as e:
        logger.error("graph_upload_failed", filename=file.filename, format=fmt, error=str(e))
        raise
    finally:
        os.unlink(tmp_path)


@router.get("/graph/stats")
async def graph_stats() -> dict:
    """获取图谱统计信息"""
    try:
        store = get_graph_store()
        return store.get_stats()
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用"}


@router.get("/graph/entity/{name}")
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


@router.get("/graph/entity/{name}/wiki-pages")
async def graph_entity_wiki_pages(name: str) -> dict:
    """KNOW-14: 查询引用该实体的 wiki 页面列表（wiki↔graph 双向关联可视化）

    查询策略（两级）：
    1. backlink 索引：实体名作为 slug 的 backlink 查询
    2. 全文扫描：wiki 页面正文/标题中包含实体名称的页面

    Returns:
        {"entity_name": "...", "wiki_pages": [{slug, title, match_type}]}
    """
    from app.knowledge.wiki_index import list_wiki_pages
    from app.knowledge.wikilink import get_backlinks
    from app.storage.version_control import get_version_control

    pages: list[dict] = []
    seen_slugs: set[str] = set()

    # 1. backlink 查询（实体名直接作为 slug）
    try:
        backlinks = get_backlinks(name)
        for bl in backlinks:
            if bl.source_slug not in seen_slugs:
                # 获取页面标题
                vc = get_version_control()
                latest = vc.get_latest(f"wiki:{bl.source_slug}")
                title = bl.source_slug
                if latest:
                    # 从 frontmatter 提取 title
                    content = latest.get("content", "")
                    for line in content.split("\n"):
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip().strip('"').strip("'")
                            break
                pages.append({
                    "slug": bl.source_slug,
                    "title": title,
                    "match_type": "backlink",
                })
                seen_slugs.add(bl.source_slug)
    except Exception:  # noqa: BLE001
        pass

    # 2. 全文扫描：标题或正文包含实体名
    try:
        all_pages = list_wiki_pages(limit=500)
        vc = get_version_control()
        name_lower = name.lower()
        for p in all_pages:
            slug = p.get("slug", "")
            if slug in seen_slugs:
                continue
            title = p.get("title", slug)
            # 标题匹配
            if name_lower in title.lower():
                pages.append({"slug": slug, "title": title, "match_type": "title"})
                seen_slugs.add(slug)
                continue
            # 正文匹配（需加载内容）
            try:
                latest = vc.get_latest(f"wiki:{slug}")
                if latest and name_lower in latest.get("content", "").lower():
                    pages.append({"slug": slug, "title": title, "match_type": "content"})
                    seen_slugs.add(slug)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

    return {"entity_name": name, "wiki_pages": pages}


@router.get("/graph/search")
async def graph_search(q: str = Query(..., min_length=1), limit: int = 20) -> dict:
    """搜索图谱实体"""
    try:
        store = get_graph_store()
        results = store.search_entities(q, limit)
        return {"query": q, "results": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用"}


@router.get("/graph/by-type/{entity_type}")
async def graph_by_type(entity_type: str, limit: int = 50) -> dict:
    """按类型查询实体"""
    try:
        store = get_graph_store()
        results = store.query_by_type(entity_type, limit)
        return {"entity_type": entity_type, "results": results, "count": len(results)}
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用"}


@router.get("/graph/visualize")
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
                    type=entity_type,
                    limit=limit,
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

            records = [_to_jsonable(dict(r)) for r in result]

            # 构建 D3.js 格式：{nodes: [...], links: [...]}
            node_map = {}
            links = []
            for rec in records:
                src_id = rec["source"]
                tgt_id = rec["target"]
                if src_id not in node_map:
                    node_map[src_id] = {
                        "id": src_id,
                        "type": rec["source_type"],
                        "group": _entity_group(rec["source_type"]),
                    }
                if tgt_id not in node_map:
                    node_map[tgt_id] = {
                        "id": tgt_id,
                        "type": rec["target_type"],
                        "group": _entity_group(rec["target_type"]),
                    }
                links.append(
                    {
                        "source": src_id,
                        "target": tgt_id,
                        "type": rec["relation"],
                        "confidence": rec["confidence"],
                    }
                )

            return {
                "nodes": list(node_map.values()),
                "links": links,
                "node_count": len(node_map),
                "link_count": len(links),
            }
    except Exception as e:
        return {
            "error": str(e),
            "hint": "Neo4j 未连接或不可用",
            "nodes": [],
            "links": [],
        }


def _entity_group(entity_type: str) -> int:
    """实体类型 → D3.js 颜色分组"""
    groups = {
        "Host": 1,
        "Service": 2,
        "Component": 3,
        "Parameter": 4,
        "Command": 5,
        "Procedure": 6,
        "Incident": 7,
        "Symptom": 8,
        "Experience": 9,
        "Concept": 10,
        "Document": 11,
    }
    return groups.get(entity_type, 0)


@router.delete("/graph/entity/{name}", dependencies=[Depends(verify_token)])
async def graph_delete_entity(name: str) -> dict:
    """删除图谱实体及其所有关联关系"""
    logger.info("graph_delete_entity_start", name=name)
    try:
        store = get_graph_store()
        result = store.delete_entity(name)
        logger.info("graph_delete_entity_done", name=name)
        return result
    except Exception as e:
        logger.error("graph_delete_entity_failed", name=name, error=str(e))
        raise HTTPException(500, f"删除实体失败: {e}")


@router.delete("/graph/clear", dependencies=[Depends(verify_token)])
async def graph_clear() -> dict:
    """清空所有图谱数据"""
    logger.warning("graph_clear_start")
    try:
        store = get_graph_store()
        result = store.clear_all()
        logger.info("graph_clear_done")
        return result
    except Exception as e:
        logger.error("graph_clear_failed", error=str(e))
        raise HTTPException(500, f"清空图谱失败: {e}")


# ────────── 维护工具：查询与清理无效数据 ──────────


class BulkDeleteEntitiesRequest(BaseModel):
    """POST /graph/maintenance/bulk-delete 请求体"""

    names: list[str] = Field(..., description="要删除的实体名称列表")


class CleanupLowConfidenceRequest(BaseModel):
    """POST /graph/maintenance/cleanup-low-confidence 请求体"""

    threshold: float = Field(0.5, description="置信度阈值（小于此值将被删除）", ge=0.0, le=1.0)
    limit: int = Field(500, description="最多删除数量", ge=1, le=10000)
    dry_run: bool = Field(False, description="只预览要删除的实体，不实际删除")


class CleanupOrphanEntitiesRequest(BaseModel):
    """POST /graph/maintenance/cleanup-orphans 请求体"""

    limit: int = Field(500, description="最多删除数量", ge=1, le=10000)
    dry_run: bool = Field(False, description="只预览要删除的实体，不实际删除")


@router.get("/graph/maintenance/overview", dependencies=[Depends(verify_token)])
async def graph_maintenance_overview(
    low_confidence_threshold: float = Query(0.5, ge=0.0, le=1.0),
) -> dict:
    """图谱维护总览 — 一站式查看所有需要维护的指标

    返回：
    - stats: 图谱基本统计
    - orphan_count: 孤立实体数（无关系）
    - low_confidence_count: 低置信度实体数
    - duplicate_count: 重复实体组数（同名不同源）
    - sample_orphans: 孤立实体样本（前 20）
    - sample_low_confidence: 低置信度实体样本（前 20）
    """
    try:
        store = get_graph_store()
        stats = store.get_stats()
        orphans = store.query_orphan_entities(limit=20)
        low_confidence = store.query_low_confidence_entities(
            threshold=low_confidence_threshold, limit=20,
        )
        duplicates = store.query_duplicate_entities(limit=20)

        return {
            "stats": stats,
            "orphan_count": len(orphans),
            "low_confidence_count": len(low_confidence),
            "duplicate_count": len(duplicates),
            "low_confidence_threshold": low_confidence_threshold,
            "sample_orphans": orphans,
            "sample_low_confidence": low_confidence,
            "sample_duplicates": duplicates,
        }
    except Exception as e:
        return {
            "error": str(e),
            "hint": "Neo4j 未连接或不可用",
            "stats": {},
            "orphan_count": 0,
            "low_confidence_count": 0,
            "duplicate_count": 0,
        }


@router.get(
    "/graph/maintenance/orphan-entities",
    dependencies=[Depends(verify_token)],
)
async def graph_maintenance_orphan_entities(
    limit: int = Query(200, ge=1, le=10000),
) -> dict:
    """查询孤立实体（无任何关系的实体节点）"""
    try:
        store = get_graph_store()
        entities = store.query_orphan_entities(limit=limit)
        return {
            "count": len(entities),
            "entities": entities,
        }
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用", "count": 0, "entities": []}


@router.get(
    "/graph/maintenance/low-confidence",
    dependencies=[Depends(verify_token)],
)
async def graph_maintenance_low_confidence(
    threshold: float = Query(0.5, ge=0.0, le=1.0),
    limit: int = Query(200, ge=1, le=10000),
) -> dict:
    """查询低置信度实体（confidence < threshold）"""
    try:
        store = get_graph_store()
        entities = store.query_low_confidence_entities(
            threshold=threshold, limit=limit,
        )
        return {
            "threshold": threshold,
            "count": len(entities),
            "entities": entities,
        }
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用", "count": 0, "entities": []}


@router.get(
    "/graph/maintenance/by-source/{doc_id}",
    dependencies=[Depends(verify_token)],
)
async def graph_maintenance_by_source(doc_id: str) -> dict:
    """查询某源文档的所有实体（用于审计与清理）"""
    try:
        store = get_graph_store()
        entities = store.query_entities_by_source(doc_id, limit=10000)
        return {
            "doc_id": doc_id,
            "count": len(entities),
            "entities": entities,
        }
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用", "count": 0, "entities": []}


@router.get(
    "/graph/maintenance/duplicates",
    dependencies=[Depends(verify_token)],
)
async def graph_maintenance_duplicates(
    limit: int = Query(100, ge=1, le=1000),
) -> dict:
    """查询重复实体（同名称不同 source_doc_id）"""
    try:
        store = get_graph_store()
        duplicates = store.query_duplicate_entities(limit=limit)
        return {
            "count": len(duplicates),
            "duplicates": duplicates,
        }
    except Exception as e:
        return {"error": str(e), "hint": "Neo4j 未连接或不可用", "count": 0, "duplicates": []}


@router.post(
    "/graph/maintenance/bulk-delete",
    dependencies=[Depends(verify_token)],
)
async def graph_maintenance_bulk_delete(body: BulkDeleteEntitiesRequest) -> dict:
    """批量删除实体（按名称列表）"""
    if not body.names:
        raise HTTPException(400, "names 不能为空")
    logger.info("graph_bulk_delete_start", count=len(body.names))
    try:
        store = get_graph_store()
        result = store.batch_delete_entities(body.names)
        logger.info("graph_bulk_delete_done", count=len(body.names))
        return result
    except Exception as e:
        logger.error("graph_bulk_delete_failed", count=len(body.names), error=str(e))
        raise HTTPException(500, f"批量删除失败: {e}")


@router.post(
    "/graph/maintenance/cleanup-low-confidence",
    dependencies=[Depends(verify_token)],
)
async def graph_maintenance_cleanup_low_confidence(
    body: CleanupLowConfidenceRequest,
) -> dict:
    """清理低置信度实体

    dry_run=True 仅返回将删除的实体列表，不实际删除。
    """
    logger.info("graph_cleanup_low_confidence_start", threshold=body.threshold, dry_run=body.dry_run)
    try:
        store = get_graph_store()
        if body.dry_run:
            entities = store.query_low_confidence_entities(
                threshold=body.threshold, limit=body.limit,
            )
            logger.info("graph_cleanup_low_confidence_dry_run", would_delete=len(entities))
            return {
                "dry_run": True,
                "threshold": body.threshold,
                "would_delete": [e["name"] for e in entities if e.get("name")],
                "count": len(entities),
            }
        result = store.cleanup_low_confidence(
            threshold=body.threshold, limit=body.limit,
        )
        result["threshold"] = body.threshold
        logger.info("graph_cleanup_low_confidence_done", deleted=result.get("deleted", 0))
        return result
    except Exception as e:
        logger.error("graph_cleanup_low_confidence_failed", error=str(e))
        raise HTTPException(500, f"清理低置信度实体失败: {e}")


@router.post(
    "/graph/maintenance/cleanup-orphans",
    dependencies=[Depends(verify_token)],
)
async def graph_maintenance_cleanup_orphans(
    body: CleanupOrphanEntitiesRequest,
) -> dict:
    """清理孤立实体（无任何关系）

    dry_run=True 仅返回将删除的实体列表，不实际删除。
    """
    logger.info("graph_cleanup_orphans_start", dry_run=body.dry_run)
    try:
        store = get_graph_store()
        if body.dry_run:
            entities = store.query_orphan_entities(limit=body.limit)
            logger.info("graph_cleanup_orphans_dry_run", would_delete=len(entities))
            return {
                "dry_run": True,
                "would_delete": [e["name"] for e in entities if e.get("name")],
                "count": len(entities),
            }
        result = store.cleanup_orphan_entities(limit=body.limit)
        logger.info("graph_cleanup_orphans_done", deleted=result.get("deleted", 0))
        return result
    except Exception as e:
        logger.error("graph_cleanup_orphans_failed", error=str(e))
        raise HTTPException(500, f"清理孤立实体失败: {e}")


@router.delete(
    "/graph/by-source/{doc_id}",
    dependencies=[Depends(verify_token)],
)
async def graph_delete_by_source(doc_id: str) -> dict:
    """删除某源文档对应的所有实体和关系

    用于：删除文档后清理残留图谱数据。
    """
    logger.info("graph_delete_by_source_start", doc_id=doc_id)
    try:
        store = get_graph_store()
        result = store.delete_by_source(doc_id)
        logger.info("graph_delete_by_source_done", doc_id=doc_id)
        return result
    except Exception as e:
        logger.error("graph_delete_by_source_failed", doc_id=doc_id, error=str(e))
        raise HTTPException(500, f"按源文档删除失败: {e}")
