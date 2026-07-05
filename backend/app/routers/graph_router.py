"""图谱存储 API（W5）。

端点：
- POST /graph/upload
- GET  /graph/stats
- GET  /graph/entity/{name}
- GET  /graph/search
- GET  /graph/by-type/{entity_type}
- GET  /graph/visualize
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.auth import verify_token
from app.extraction import KnowledgeExtractor
from app.knowledge import (
    GraphEntity,
    GraphRelation,
    get_graph_store,
    get_compiler,
    get_review_queue,
)
from app.parsers import get_parser, supported_formats
from app.routers.parsers_router import EXT_FMT_MAP

router = APIRouter()


@router.post("/graph/upload", dependencies=[Depends(verify_token)])
async def graph_upload(file: UploadFile = File(...)) -> dict:
    """解析文档 → 抽取知识 → 编译 → 写入图谱（全流水线）"""
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
        # 解析
        parser = get_parser(fmt)
        doc = parser.parse(tmp_path, file.filename or "unknown")

        # 抽取
        extractor = KnowledgeExtractor()
        result = await extractor.extract(doc)

        # 转换为 GraphEntity/GraphRelation
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
        relations = [
            GraphRelation(
                relation_type=r.relation_type,
                from_entity=r.from_entity,
                to_entity=r.to_entity,
                properties=r.properties,
                source_doc_id=doc.doc_id,
                confidence=r.confidence,
            )
            for r in result.auto_accepted_relations
        ]

        # 编译 + 写入
        compiler = get_compiler()
        compile_result = compiler.compile_and_store(entities, relations)

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

            records = [dict(r) for r in result]

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
