"""文档管理 API（P0-1）。

端点：
- GET    /documents
- GET    /documents/stats
- GET    /documents/search
- GET    /documents/{doc_id}
- GET    /documents/{doc_id}/content
- GET    /documents/{doc_id}/parse-tree
- DELETE /documents/{doc_id}
"""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Depends, HTTPException

from app.aiops import get_topology_builder
from app.auth import verify_token
from app.schemas import PaginatedData, PaginatedResponse
from app.search import get_search_engine
from app.storage import get_document_store

logger = structlog.get_logger()
router = APIRouter()

# 二进制格式列表（无法直接 UTF-8 解码，需解析后返回文本）
_BINARY_FORMATS = frozenset({"word", "excel", "ppt", "pdf", "png", "jpg", "gif", "bmp", "epub"})


def _render_parsed_elements_to_text(elements: list[dict]) -> str:
    """将解析后的结构化元素渲染为可读文本"""
    lines: list[str] = []
    for e in elements:
        etype = e.get("type", "")
        content = e.get("content", "")
        section = e.get("section", "")
        if etype == "heading":
            prefix = "#" * min(e.get("metadata", {}).get("level", 1), 6)
            lines.append(f"{prefix} {content}")
        elif etype == "table":
            lines.append(content)
        elif etype == "code":
            lang = e.get("metadata", {}).get("language", "")
            lines.append(f"```{lang}\n{content}\n```")
        elif etype == "list_item":
            lines.append(f"- {content}")
        elif etype == "paragraph":
            lines.append(content)
        else:
            lines.append(content)
        if section:
            lines.append(f"  [章节: {section}]")
        lines.append("")
    return "\n".join(lines)


@router.get("/documents")
async def list_documents(
    page: int = 1,
    page_size: int = 20,
    format: str | None = None,
    status: str | None = None,
    # Deprecated: 向后兼容旧 limit/offset 参数
    limit: int | None = None,
    offset: int | None = None,
) -> dict:
    """列出所有存储的文档（分页）

    使用 page/page_size 分页（推荐），同时兼容旧的 limit/offset 参数。
    当 limit 显式传入时，优先使用 limit/offset 模式。
    """
    store = get_document_store()

    # 向后兼容：如果显式传入 limit，使用 limit/offset 模式
    if limit is not None:
        _limit, _offset = limit, offset or 0
        _page, _page_size = 1, _limit  # 近似映射
    else:
        _limit = page_size
        _offset = (page - 1) * page_size
        _page, _page_size = page, page_size

    docs = store.list(_limit, _offset, format, status)
    total = store.count(format, status)
    stats = store.get_stats()

    result = PaginatedResponse[dict](
        data=PaginatedData(
            items=docs,
            total=total,
            page=_page,
            page_size=_page_size,
        ),
        message="",
    ).model_dump()
    # 向后兼容：保留 stats 字段
    result["stats"] = stats
    return result


@router.get("/documents/stats")
async def document_stats() -> dict:
    """文档统计"""
    store = get_document_store()
    return store.get_stats()


@router.get("/documents/search")
async def search_documents(q: str, limit: int = 20) -> dict:
    """搜索文档（按文件名/标题）"""
    store = get_document_store()
    results = store.search(q, limit)
    return {"query": q, "results": results, "count": len(results)}


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str) -> dict:
    """获取文档元数据"""
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    return doc


@router.get("/documents/{doc_id}/content", dependencies=[Depends(verify_token)])
async def get_document_content(doc_id: str) -> dict:
    """获取文档内容（文本），优先返回解析后的文本，二进制格式自动解析

    返回 JSON ``{"content": str, "format": str, "source": str}`` 供前端展示。
    - source="parsed": 从持久化的解析结果渲染
    - source="raw": 原始 UTF-8 文本
    """
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")

    doc_fmt = doc.get("format", "")

    # P0-1: 优先从持久化解析结果获取文本
    if doc.get("parse_result"):
        try:
            parsed = json.loads(doc["parse_result"])
            elements = parsed.get("elements", [])
            if elements:
                text = _render_parsed_elements_to_text(elements)
                return {"content": text, "format": doc_fmt, "source": "parsed"}
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("parse_result_decode_failed", doc_id=doc_id, error=str(e))

    # 读取原始内容
    content = store.read_content(doc_id)
    if content is None:
        raise HTTPException(404, "文件内容不存在（可能已被删除）")

    # 尝试 UTF-8 解码
    try:
        text = content.decode("utf-8")
        return {"content": text, "format": doc_fmt, "source": "raw"}
    except (UnicodeDecodeError, AttributeError):
        pass

    # P0-1: 二进制格式，尝试重新解析
    if doc_fmt in _BINARY_FORMATS:
        try:
            from app.parsers import get_parser
            parser = get_parser(doc_fmt)
            parsed = parser.parse(doc["stored_path"], doc_id)
            elements = [{
                "type": e.type.value,
                "content": e.content[:2000],
                "section": e.section,
                "metadata": e.metadata,
            } for e in parsed.elements[:200]]
            text = _render_parsed_elements_to_text(elements)
            return {"content": text, "format": doc_fmt, "source": "parsed_on_demand"}
        except Exception as e:
            logger.warning("on_demand_parse_failed", doc_id=doc_id, error=str(e))

    return {
        "content": f"[二进制内容，共 {len(content)} 字节，解析失败]",
        "format": doc_fmt,
        "source": "binary_fallback",
    }


@router.get("/documents/{doc_id}/parse-tree", dependencies=[Depends(verify_token)])
async def get_document_parse_tree(doc_id: str) -> dict:
    """获取文档解析树结构（标题层级 + 元素归属）

    返回文档的完整解析树，包含：
    - heading_tree: 标题层级树（含层级关系、slug、子章节）
    - elements: 所有元素及其章节归属
    - statistics: 解析统计信息

    用于文档结构可视化和完整性校验。
    """
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")

    doc_fmt = doc.get("format", "")

    # 优先从持久化解析结果获取
    if doc.get("parse_result"):
        try:
            parsed = json.loads(doc["parse_result"])
            heading_tree = parsed.get("heading_tree", [])
            elements = parsed.get("elements", [])
            if heading_tree or elements:
                stats = _compute_parse_tree_stats(heading_tree, elements)
                return {
                    "doc_id": doc_id,
                    "title": parsed.get("title"),
                    "format": doc_fmt,
                    "heading_tree": heading_tree,
                    "elements": elements,
                    "statistics": stats,
                    "source": "persisted",
                }
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("parse_result_decode_failed", doc_id=doc_id, error=str(e))

    # 重新解析文档获取完整结构
    try:
        from app.parsers import get_parser
        parser = get_parser(doc_fmt)
        parsed_doc = parser.parse(doc["stored_path"], doc_id)

        heading_tree = parsed_doc.get_heading_tree_dict()
        elements = [{
            "type": e.type.value,
            "content": e.content[:2000],
            "section": e.section,
            "parent_section": e.parent_section,
            "metadata": e.metadata,
        } for e in parsed_doc.elements]

        stats = _compute_parse_tree_stats(heading_tree, elements)

        return {
            "doc_id": doc_id,
            "title": parsed_doc.title,
            "format": doc_fmt,
            "heading_tree": heading_tree,
            "elements": elements,
            "statistics": stats,
            "source": "reparsed",
        }
    except Exception as e:
        logger.error("parse_tree_reparse_failed", doc_id=doc_id, error=str(e))
        raise HTTPException(500, f"解析树生成失败: {e}")


def _compute_parse_tree_stats(heading_tree: list[dict], elements: list[dict]) -> dict:
    """计算解析树统计信息"""
    heading_counts = {}
    element_counts = {}

    def count_headings(nodes: list[dict]) -> None:
        for node in nodes:
            level = node.get("level", 1)
            heading_counts[level] = heading_counts.get(level, 0) + 1
            if node.get("children"):
                count_headings(node["children"])

    count_headings(heading_tree)

    for e in elements:
        etype = e.get("type", "")
        element_counts[etype] = element_counts.get(etype, 0) + 1

    return {
        "total_headings": sum(heading_counts.values()),
        "heading_by_level": heading_counts,
        "total_elements": len(elements),
        "elements_by_type": element_counts,
        "sections_without_elements": _count_sections_without_elements(heading_tree),
    }


def _count_sections_without_elements(heading_tree: list[dict]) -> int:
    """统计没有内容元素的章节数"""
    count = 0

    def check_node(nodes: list[dict]) -> None:
        nonlocal count
        for node in nodes:
            if node.get("element_count", 0) == 0:
                count += 1
            if node.get("children"):
                check_node(node["children"])

    check_node(heading_tree)
    return count


@router.delete("/documents/{doc_id}", dependencies=[Depends(verify_token)])
async def delete_document(doc_id: str) -> dict:
    """删除文档（文件+元数据+索引+拓扑引用）"""
    store = get_document_store()
    ok = store.delete(doc_id)
    if not ok:
        raise HTTPException(404, f"文档不存在: {doc_id}")
    get_search_engine().remove_index(doc_id)
    # P2-4.5 清理拓扑中该文档的引用
    topo_cleanup = get_topology_builder().remove_doc(doc_id)

    # 触发 webhook：document.deleted
    from app.webhooks import dispatch_event

    dispatch_event(
        "document.deleted",
        {"doc_id": doc_id, "topology_cleanup": topo_cleanup},
    )
    return {"deleted": True, "doc_id": doc_id, "topology_cleanup": topo_cleanup}


# ────────── P1-4: 流水线状态 API ──────────

@router.get("/documents/{doc_id}/pipeline-status")
async def get_pipeline_status(doc_id: str) -> dict:
    """获取文档的流水线处理状态（步骤级）

    返回每个处理步骤的状态、耗时、错误信息，供前端 Stepper 可视化。

    Returns:
        {
            "doc_id": str,
            "current_status": "uploaded"|"parsed"|"extracted"|"compiled"|"error",
            "steps": [
                {"name": "upload", "label": "上传", "status": "done"|"running"|"pending"|"error",
                 "started_at": str|None, "duration_ms": int|None, "error": str|None},
                ...
            ],
            "retryable": bool,   # 是否可从失败步骤重试
            "failed_step": str|None,  # 失败的步骤名
        }
    """
    store = get_document_store()
    doc = store.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")

    current_status = doc.get("status", "uploaded")
    steps = _build_pipeline_steps(doc, current_status)
    failed_step = _find_failed_step(steps)
    retryable = failed_step is not None

    return {
        "doc_id": doc_id,
        "current_status": current_status,
        "steps": steps,
        "retryable": retryable,
        "failed_step": failed_step,
        "title": doc.get("title", ""),
        "format": doc.get("format", ""),
    }


def _build_pipeline_steps(doc: dict, current_status: str) -> list[dict]:
    """构建流水线步骤列表"""
    # 从文档元数据中提取时间信息
    created_at = doc.get("created_at", "")
    parsed_at = doc.get("parsed_at", "")
    extracted_at = doc.get("extracted_at", "")
    compiled_at = doc.get("compiled_at", "")
    error_msg = doc.get("error_message", "")

    # 状态顺序：uploaded → parsed → extracted → compiled
    status_order = {"uploaded": 0, "parsed": 1, "extracted": 2, "compiled": 3, "error": -1}
    current_idx = status_order.get(current_status, 0)

    steps = [
        {
            "name": "upload",
            "label": "上传",
            "status": "done",
            "started_at": created_at,
            "duration_ms": None,
            "error": None,
        },
        {
            "name": "parse",
            "label": "解析",
            "status": _step_status(0, current_idx, current_status, parsed_at),
            "started_at": parsed_at or None,
            "duration_ms": None,
            "error": error_msg if current_status == "error" and not parsed_at else None,
        },
        {
            "name": "extract",
            "label": "知识抽取",
            "status": _step_status(1, current_idx, current_status, extracted_at),
            "started_at": extracted_at or None,
            "duration_ms": None,
            "error": error_msg if current_status == "error" and parsed_at and not extracted_at else None,
        },
        {
            "name": "compile",
            "label": "编译 Wiki",
            "status": _step_status(2, current_idx, current_status, compiled_at),
            "started_at": compiled_at or None,
            "duration_ms": None,
            "error": error_msg if current_status == "error" and extracted_at and not compiled_at else None,
        },
        {
            "name": "index",
            "label": "重建索引",
            "status": _step_status(3, current_idx, current_status, compiled_at),
            "started_at": None,
            "duration_ms": None,
            "error": None,
        },
    ]
    return steps


def _step_status(step_idx: int, current_idx: int, current_status: str, has_timestamp: str | None) -> str:
    """判断单个步骤的状态"""
    if current_status == "error":
        if step_idx < current_idx and has_timestamp:
            return "done"
        if step_idx == current_idx:
            return "error"
        return "pending"
    if step_idx < current_idx:
        return "done"
    if step_idx == current_idx and has_timestamp:
        return "done"
    if step_idx == current_idx:
        return "running"
    return "pending"


def _find_failed_step(steps: list[dict]) -> str | None:
    """找到第一个失败的步骤"""
    for s in steps:
        if s["status"] == "error":
            return s["name"]
    return None
