"""解析器 API（W3）。

端点：
- GET  /parsers
- POST /parsers/parse/batch
- POST /parsers/parse/{fmt}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.auth import verify_token
from app.parsers import get_parser, supported_formats
from app.parsers.base import ParsedDocument
from app.search import get_search_engine
from app.storage import get_document_store

router = APIRouter()

# 扩展名 → 格式映射（统一）。多个 router 共用，故置于本模块导出。
EXT_FMT_MAP = {
    "md": "markdown",
    "sql": "sql",
    "txt": "txt",
    "docx": "word",
    "doc": "word",
    "xlsx": "excel",
    "xls": "excel",
    "pptx": "ppt",
    "ppt": "ppt",
    "html": "html",
    "htm": "html",
    "pdf": "pdf",
    "epub": "epub",
    "csv": "csv",
    "json": "json",
    "png": "png",
    "jpg": "jpg",
    "jpeg": "jpg",
    "gif": "gif",
    "bmp": "bmp",
}


def _serialize_doc(doc: ParsedDocument) -> dict:
    return {
        "doc_id": doc.doc_id,
        "source_path": doc.source_path,
        "format": doc.format,
        "checksum": doc.checksum,
        "title": doc.title,
        "element_count": len(doc.elements),
        "elements": [
            {
                "type": e.type.value,
                "content": e.content[:2000],
                "section": e.section,
                "metadata": e.metadata,
            }
            for e in doc.elements[:50]  # API 响应限制前 50 个元素
        ],
    }


@router.get("/parsers")
async def list_parsers() -> dict[str, list[str]]:
    return {"formats": supported_formats()}


@router.post("/parsers/parse/batch", dependencies=[Depends(verify_token)])
async def parse_batch(files: list[UploadFile] = File(..., alias="files")) -> dict:
    """批量解析，自动检测格式（文件持久化存储）"""
    results = []
    formats = set(supported_formats())
    store = get_document_store()

    for file in files:
        ext = (
            (file.filename or "").rsplit(".", 1)[-1].lower()
            if "." in (file.filename or "")
            else ""
        )
        fmt = EXT_FMT_MAP.get(ext, ext)
        if fmt not in formats:
            results.append({"filename": file.filename, "error": f"不支持的格式: {fmt}"})
            continue

        content = await file.read()
        # 持久化存储
        doc_meta = store.save(file.filename or "unknown", content, fmt)
        stored_path = doc_meta["stored_path"]

        # 触发 webhook：document.created
        from app.webhooks import dispatch_event

        dispatch_event(
            "document.created",
            {
                "doc_id": doc_meta["doc_id"],
                "filename": file.filename,
                "format": fmt,
                "size_bytes": doc_meta.get("size_bytes"),
                "checksum": doc_meta.get("checksum"),
            },
        )

        # 业务指标埋点
        from app.observability import record_business_metric

        record_business_metric(
            "documents_uploaded_total", 1.0, format=fmt
        )

        try:
            parser = get_parser(fmt)
            doc = parser.parse(stored_path, doc_meta["doc_id"])
            store.update_status(doc_meta["doc_id"], "parsed", title=doc.title)
            dispatch_event(
                "document.parsed",
                {
                    "doc_id": doc_meta["doc_id"],
                    "title": doc.title,
                    "elements": len(doc.elements),
                },
            )
            results.append(
                {
                    "filename": file.filename,
                    "format": fmt,
                    "doc_id": doc_meta["doc_id"],
                    "doc": _serialize_doc(doc),
                }
            )
        except Exception as e:
            store.update_status(doc_meta["doc_id"], "error")
            results.append({"filename": file.filename, "format": fmt, "error": str(e)})

    return {"results": results}


@router.post("/parsers/parse/{fmt}", dependencies=[Depends(verify_token)])
async def parse_file(fmt: str, file: UploadFile = File(...)) -> dict:
    """解析单个文件（持久化存储），返回 ParsedDocument 结构"""
    if fmt not in supported_formats():
        raise HTTPException(400, f"不支持的格式: {fmt}。支持: {supported_formats()}")

    content = await file.read()
    store = get_document_store()
    doc_meta = store.save(file.filename or "unknown", content, fmt)
    stored_path = doc_meta["stored_path"]

    # 触发 webhook：document.created
    from app.webhooks import dispatch_event

    dispatch_event(
        "document.created",
        {
            "doc_id": doc_meta["doc_id"],
            "filename": file.filename,
            "format": fmt,
            "size_bytes": doc_meta.get("size_bytes"),
            "checksum": doc_meta.get("checksum"),
        },
    )

    # 业务指标埋点
    from app.observability import record_business_metric

    record_business_metric(
        "documents_uploaded_total", 1.0, format=fmt
    )

    try:
        parser = get_parser(fmt)
        doc = parser.parse(stored_path, doc_meta["doc_id"])
        store.update_status(doc_meta["doc_id"], "parsed", title=doc.title)
        # 建立搜索索引
        content_text = " ".join(e.content for e in doc.elements if e.content)
        get_search_engine().index_document(
            doc_meta["doc_id"], doc.title, content_text, fmt
        )
        dispatch_event(
            "document.parsed",
            {
                "doc_id": doc_meta["doc_id"],
                "title": doc.title,
                "elements": len(doc.elements),
            },
        )
        result = _serialize_doc(doc)
        result["doc_id"] = doc_meta["doc_id"]
        result["stored"] = True
        return result
    except Exception as e:
        store.update_status(doc_meta["doc_id"], "error")
        raise HTTPException(500, f"解析失败: {e}")
