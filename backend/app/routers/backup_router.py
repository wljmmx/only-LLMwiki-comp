"""系统备份 API（P1-7: SQLite 热备 + checksum 校验）

端点：
- POST /system/backup                      触发热备（admin）
- GET  /system/backups                     列出所有备份（admin）
- GET  /system/backups/{timestamp}/verify  校验备份完整性（admin）
- POST /system/backups/{timestamp}/restore 从备份恢复（admin）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.token_auth import require_role
from app.ha.backup import (
    backup_all_databases,
    list_backups,
    restore_from_backup,
    verify_backup,
)

router = APIRouter()


class RestoreRequest(BaseModel):
    """恢复请求"""
    db_name: str | None = None  # 指定恢复的数据库文件名；None=恢复全部


@router.post("/system/backup", dependencies=[Depends(require_role("admin"))])
async def create_backup() -> dict:
    """触发 SQLite 热备（P1-7）

    使用 SQLite Online Backup API，不阻塞写入。
    生成带 SHA256 checksum 的 manifest.json。
    """
    try:
        manifest = backup_all_databases()
        return {"ok": True, "manifest": manifest}
    except Exception as e:
        raise HTTPException(500, f"备份失败: {e}") from e


@router.get("/system/backups", dependencies=[Depends(require_role("admin"))])
async def get_backups() -> dict:
    """列出所有可用备份"""
    backups = list_backups()
    return {"backups": backups, "count": len(backups)}


@router.get(
    "/system/backups/{timestamp}/verify",
    dependencies=[Depends(require_role("admin"))],
)
async def verify_backup_endpoint(timestamp: str) -> dict:
    """校验备份完整性（SHA256 checksum 逐文件校验）"""
    try:
        result = verify_backup(timestamp)
        return result
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


@router.post(
    "/system/backups/{timestamp}/restore",
    dependencies=[Depends(require_role("admin"))],
)
async def restore_backup(timestamp: str, req: RestoreRequest | None = None) -> dict:
    """从备份恢复数据库（P1-7）

    警告：恢复操作会覆盖当前数据库文件。
    恢复前自动备份当前文件到 .db.pre-restore。
    """
    db_name = req.db_name if req else None
    try:
        result = restore_from_backup(timestamp, db_name)
        return result
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
