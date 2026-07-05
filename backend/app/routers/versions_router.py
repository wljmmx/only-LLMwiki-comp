"""版本控制 API（P1-2）。

端点：
- GET  /versions/{doc_key}
- GET  /versions/{doc_key}/{version}
- GET  /versions/{doc_key}/diff/{v1}/{v2}
- POST /versions/{doc_key}/rollback/{target_version}
- POST /versions/{doc_key}/save
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_token
from app.storage import get_version_control

router = APIRouter()


@router.get("/versions/{doc_key}")
async def list_versions(doc_key: str) -> dict:
    """列出文档的所有版本"""
    vc = get_version_control()
    versions = vc.list_versions(doc_key)
    return {"doc_key": doc_key, "versions": versions, "count": len(versions)}


@router.get("/versions/{doc_key}/{version}")
async def get_version(doc_key: str, version: int) -> dict:
    """获取指定版本内容"""
    vc = get_version_control()
    v = vc.get_version(doc_key, version)
    if not v:
        raise HTTPException(404, f"版本不存在: {doc_key} v{version}")
    return v


@router.get("/versions/{doc_key}/diff/{v1}/{v2}")
async def diff_versions(doc_key: str, v1: int, v2: int) -> dict:
    """对比两个版本"""
    vc = get_version_control()
    return vc.diff(doc_key, v1, v2)


@router.post(
    "/versions/{doc_key}/rollback/{target_version}",
    dependencies=[Depends(verify_token)],
)
async def rollback_version(doc_key: str, target_version: int) -> dict:
    """回滚到指定版本"""
    vc = get_version_control()
    result = vc.rollback(doc_key, target_version)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.post("/versions/{doc_key}/save", dependencies=[Depends(verify_token)])
async def save_version(
    doc_key: str, title: str, content: str, change_summary: str = ""
) -> dict:
    """保存新版本"""
    vc = get_version_control()
    return vc.save_version(doc_key, title, content, change_summary=change_summary)
