"""导出 API（P1-4）。

端点：
- POST /export  导出文档（markdown | html | text | pdf）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.auth import verify_token
from app.export import get_exporter

router = APIRouter()


@router.post("/export", dependencies=[Depends(verify_token)])
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
