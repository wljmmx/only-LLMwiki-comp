"""解析器 API（W3）。

端点：
- GET  /parsers
- POST /parsers/parse/batch
- POST /parsers/parse/{fmt}
- POST /parsers/parse/{fmt}/stream   P2-5.5: SSE 流式解析进度
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

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
        "heading_tree": doc.get_heading_tree_dict(),
        "elements": [
            {
                "type": e.type.value,
                "content": e.content[:2000],
                "section": e.section,
                "parent_section": e.parent_section,
                "metadata": e.metadata,
            }
            for e in doc.elements[:50]
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
            # P0-3: 持久化解析结果
            store.update_status(
                doc_meta["doc_id"], "parsed",
                title=doc.title,
                parse_result=_serialize_doc(doc),
            )
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
        # P0-3: 持久化解析结果
        store.update_status(
            doc_meta["doc_id"], "parsed",
            title=doc.title,
            parse_result=_serialize_doc(doc),
        )
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


def _sse_event(event_type: str, data: dict) -> str:
    """格式化 SSE 事件帧"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _check_cancel(cancel_token) -> bool:
    """检查客户端是否断连"""
    try:
        return bool(await cancel_token())
    except Exception:  # noqa: BLE001
        return False


@router.post("/parsers/parse/{fmt}/stream", dependencies=[Depends(verify_token)])
async def parse_file_stream(fmt: str, file: UploadFile = File(...), request: Request = None):
    """P2-5.5: SSE 流式解析 — 实时推送 4 阶段进度

    由于 parsers 的"重活"在第三方库/子进程内（无细粒度回调钩子），
    本端点提供粗粒度阶段进度：upload → persist → parse → index → done。

    SSE 事件序列：
        event: step_start  — {"step":"upload", "message":"接收文件...", "filename":"..."}
        event: step_done   — {"step":"upload", "size_bytes":12345}
        event: step_start  — {"step":"persist", "message":"持久化存储..."}
        event: step_done   — {"step":"persist", "doc_id":"...", "stored_path":"..."}
        event: step_start  — {"step":"parse", "message":"解析文档...", "format":"pdf"}
        event: step_done   — {"step":"parse", "elements":156, "title":"..."}
        event: step_start  — {"step":"index", "message":"建立搜索索引..."}
        event: step_done   — {"step":"index"}
        event: done        — {"doc_id":"...", "doc":{...}, "stored":true, "total_ms":1234}
        event: error       — {"step":"...", "message":"...", "retryable":true}

    P2-4: 客户端断连时取消（通过 request.is_disconnected()）。
    parser.parse() 是同步阻塞，通过 run_in_executor 在工作线程执行，
    避免阻塞 event loop 导致 SSE 无法 yield 中间事件。
    """
    if fmt not in supported_formats():
        raise HTTPException(400, f"不支持的格式: {fmt}。支持: {supported_formats()}")

    cancel_token = request.is_disconnected if request else (lambda: False)

    async def event_gen():
        total_start = datetime.now(timezone.utc)
        loop = asyncio.get_event_loop()
        store = get_document_store()

        # 阶段 1: upload（读文件内容）
        yield _sse_event("step_start", {
            "step": "upload",
            "message": "接收文件...",
            "filename": file.filename,
        })
        if await _check_cancel(cancel_token):
            return
        try:
            content = await file.read()
        except Exception as e:  # noqa: BLE001
            yield _sse_event("error", {
                "step": "upload",
                "message": f"读取文件失败: {e}",
                "retryable": False,
            })
            return
        yield _sse_event("step_done", {
            "step": "upload",
            "size_bytes": len(content),
        })

        # 阶段 2: persist（持久化存储）
        yield _sse_event("step_start", {
            "step": "persist",
            "message": "持久化存储...",
        })
        if await _check_cancel(cancel_token):
            return
        try:
            doc_meta = store.save(file.filename or "unknown", content, fmt)
            stored_path = doc_meta["stored_path"]
        except Exception as e:  # noqa: BLE001
            yield _sse_event("error", {
                "step": "persist",
                "message": f"存储失败: {e}",
                "retryable": False,
            })
            return
        yield _sse_event("step_done", {
            "step": "persist",
            "doc_id": doc_meta["doc_id"],
            "stored_path": stored_path,
        })

        # 触发 webhook + 指标
        from app.observability import record_business_metric
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
        record_business_metric("documents_uploaded_total", 1.0, format=fmt)

        # 阶段 3: parse（解析，同步阻塞 → run_in_executor）
        yield _sse_event("step_start", {
            "step": "parse",
            "message": f"解析文档（格式: {fmt}）...",
            "format": fmt,
        })
        if await _check_cancel(cancel_token):
            return

        # 解析在工作线程执行，结果通过 queue 传回
        parse_result: dict = {}

        async def run_parse():
            try:
                parser = get_parser(fmt)
                doc = await loop.run_in_executor(
                    None, parser.parse, stored_path, doc_meta["doc_id"]
                )
                parse_result["doc"] = doc
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                parse_result["error"] = str(e)

        parse_task = asyncio.create_task(run_parse())
        # 等待解析完成，期间定期发心跳 + 检查取消
        while not parse_task.done():
            if await _check_cancel(cancel_token):
                parse_task.cancel()
                return
            # 心跳事件：表示仍在解析（对大 PDF/Excel 尤其重要）
            yield _sse_event("progress", {
                "step": "parse",
                "message": "解析中...",
                "elapsed_ms": int(
                    (datetime.now(timezone.utc) - total_start).total_seconds() * 1000
                ),
            })
            try:
                await asyncio.wait_for(asyncio.shield(parse_task), timeout=2.0)
            except asyncio.TimeoutError:
                continue  # 继续心跳循环
            except asyncio.CancelledError:
                return

        # 检查解析结果
        if "error" in parse_result:
            store.update_status(doc_meta["doc_id"], "error")
            yield _sse_event("error", {
                "step": "parse",
                "message": parse_result["error"],
                "retryable": True,
            })
            return

        doc: ParsedDocument = parse_result["doc"]
        store.update_status(
            doc_meta["doc_id"], "parsed",
            title=doc.title,
            parse_result=_serialize_doc(doc),
        )
        yield _sse_event("step_done", {
            "step": "parse",
            "elements": len(doc.elements),
            "title": doc.title,
        })

        # 阶段 4: index（建立搜索索引）
        yield _sse_event("step_start", {
            "step": "index",
            "message": "建立搜索索引...",
        })
        if await _check_cancel(cancel_token):
            return
        try:
            content_text = " ".join(e.content for e in doc.elements if e.content)
            get_search_engine().index_document(
                doc_meta["doc_id"], doc.title, content_text, fmt
            )
        except Exception as e:  # noqa: BLE001
            # 索引失败不阻塞解析完成，仅记录
            yield _sse_event("step_done", {
                "step": "index",
                "warning": f"索引建立失败: {e}",
            })
        else:
            yield _sse_event("step_done", {"step": "index"})

        dispatch_event(
            "document.parsed",
            {
                "doc_id": doc_meta["doc_id"],
                "title": doc.title,
                "elements": len(doc.elements),
            },
        )

        # 终态: done
        total_ms = int(
            (datetime.now(timezone.utc) - total_start).total_seconds() * 1000
        )
        result = _serialize_doc(doc)
        result["doc_id"] = doc_meta["doc_id"]
        result["stored"] = True
        yield _sse_event("done", {
            "doc_id": doc_meta["doc_id"],
            "doc": result,
            "stored": True,
            "total_ms": total_ms,
        })

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
