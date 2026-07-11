"""SQLite 在线热备 + checksum 校验（P1-7）

使用 SQLite Online Backup API（sqlite3.Connection.backup()）实现热备：
- 不阻塞写入（WAL 模式下读写可并发）
- 原子性拷贝（备份过程中的一致性快照）
- SHA256 checksum 校验完整性

备份目录结构：
    data/backups/
    ├── 2026-07-11T10-00-00Z/
    │   ├── manifest.json          # 备份清单（文件列表 + checksum + 元数据）
    │   ├── documents.db
    │   ├── versions.db
    │   ├── auth.db
    │   └── ...
    └── 2026-07-11T12-00-00Z/
        └── ...
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# 数据目录与备份目录
DATA_DIR = Path(__file__).parent.parent.parent / "data"
BACKUP_DIR = DATA_DIR / "backups"


def _sha256_file(filepath: Path) -> str:
    """计算文件的 SHA256 校验和"""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _online_backup(src_path: Path, dst_path: Path) -> None:
    """使用 SQLite Online Backup API 执行热备

    优势：
    - 不阻塞源数据库的写入操作（WAL 模式下）
    - 原子性：备份是数据库的一致性快照
    - 比文件拷贝更安全（避免 WAL 文件不一致问题）
    """
    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def backup_all_databases() -> dict[str, Any]:
    """热备所有 SQLite 数据库

    扫描 data/ 目录下所有 .db 文件，逐一执行在线热备。
    生成 manifest.json 记录文件列表、checksum、时间戳。

    返回备份清单 dict。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    backup_subdir = BACKUP_DIR / timestamp
    backup_subdir.mkdir(parents=True, exist_ok=False)

    files_backed_up: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    # 扫描所有 .db 文件（排除 backups/ 子目录和临时文件）
    db_files = sorted(
        f for f in DATA_DIR.glob("*.db")
        if f.is_file() and not f.name.startswith(".")
    )

    for db_file in db_files:
        try:
            dst_file = backup_subdir / db_file.name
            _online_backup(db_file, dst_file)
            checksum = _sha256_file(dst_file)
            file_size = dst_file.stat().st_size
            files_backed_up.append({
                "file": db_file.name,
                "size_bytes": file_size,
                "sha256": checksum,
            })
            logger.info("backup.file_ok", file=db_file.name, size=file_size)
        except Exception as e:  # noqa: BLE001
            errors.append({"file": db_file.name, "error": str(e)})
            logger.error("backup.file_failed", file=db_file.name, error=str(e))

    manifest = {
        "timestamp": timestamp,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": files_backed_up,
        "file_count": len(files_backed_up),
        "errors": errors,
        "error_count": len(errors),
        "total_size_bytes": sum(f["size_bytes"] for f in files_backed_up),
    }

    manifest_path = backup_subdir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    logger.info(
        "backup.completed",
        timestamp=timestamp,
        files=len(files_backed_up),
        errors=len(errors),
        total_size=manifest["total_size_bytes"],
    )
    return manifest


def verify_backup(timestamp: str) -> dict[str, Any]:
    """校验备份完整性

    读取 manifest.json，逐一校验文件是否存在 + SHA256 是否匹配。

    返回校验结果 dict。
    """
    backup_subdir = BACKUP_DIR / timestamp
    manifest_path = backup_subdir / "manifest.json"

    if not manifest_path.exists():
        raise FileNotFoundError(f"备份不存在: {timestamp}")

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    results: list[dict[str, Any]] = []
    all_ok = True

    for entry in manifest.get("files", []):
        filename = entry["file"]
        expected_checksum = entry["sha256"]
        filepath = backup_subdir / filename

        if not filepath.exists():
            results.append({
                "file": filename,
                "ok": False,
                "error": "文件不存在",
            })
            all_ok = False
            continue

        actual_checksum = _sha256_file(filepath)
        ok = actual_checksum == expected_checksum
        results.append({
            "file": filename,
            "ok": ok,
            "expected": expected_checksum,
            "actual": actual_checksum,
        })
        if not ok:
            all_ok = False

    return {
        "timestamp": timestamp,
        "all_ok": all_ok,
        "files": results,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def list_backups() -> list[dict[str, Any]]:
    """列出所有可用备份"""
    if not BACKUP_DIR.exists():
        return []

    backups: list[dict[str, Any]] = []
    for entry in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = json.load(f)
                backups.append({
                    "timestamp": entry.name,
                    "file_count": manifest.get("file_count", 0),
                    "total_size_bytes": manifest.get("total_size_bytes", 0),
                    "error_count": manifest.get("error_count", 0),
                    "created_at": manifest.get("created_at", ""),
                })
            except Exception:  # noqa: BLE001
                backups.append({
                    "timestamp": entry.name,
                    "file_count": 0,
                    "error": "manifest.json 解析失败",
                })
        else:
            backups.append({
                "timestamp": entry.name,
                "file_count": 0,
                "error": "manifest.json 不存在",
            })
    return backups


def restore_from_backup(timestamp: str, db_name: str | None = None) -> dict[str, Any]:
    """从备份恢复数据库

    警告：恢复操作会覆盖当前数据库文件，建议先停止写入。

    参数：
        timestamp: 备份时间戳目录名
        db_name: 指定恢复的数据库文件名（如 "auth.db"）；None=恢复全部

    返回恢复结果 dict。
    """
    backup_subdir = BACKUP_DIR / timestamp
    if not backup_subdir.exists():
        raise FileNotFoundError(f"备份不存在: {timestamp}")

    restored: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    # 恢复指定的或全部 .db 文件
    db_files = sorted(backup_subdir.glob("*.db"))
    if db_name:
        db_files = [f for f in db_files if f.name == db_name]
        if not db_files:
            raise FileNotFoundError(f"备份中不存在数据库: {db_name}")

    for backup_file in db_files:
        target = DATA_DIR / backup_file.name
        try:
            # 先校验 checksum（如果 manifest 存在）
            manifest_path = backup_subdir / "manifest.json"
            if manifest_path.exists():
                with open(manifest_path, encoding="utf-8") as f:
                    manifest = json.load(f)
                for entry in manifest.get("files", []):
                    if entry["file"] == backup_file.name:
                        actual = _sha256_file(backup_file)
                        if actual != entry["sha256"]:
                            raise ValueError(f"checksum 不匹配: {actual} != {entry['sha256']}")

            # 恢复前先备份当前文件（防止误恢复）
            if target.exists():
                backup_current = target.with_suffix(".db.pre-restore")
                shutil.copy2(target, backup_current)

            # 用 SQLite 恢复（确保文件完整性）
            src = sqlite3.connect(str(backup_file))
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()

            restored.append({"file": backup_file.name, "status": "ok"})
            logger.info("restore.file_ok", file=backup_file.name)
        except Exception as e:  # noqa: BLE001
            errors.append({"file": backup_file.name, "error": str(e)})
            logger.error("restore.file_failed", file=backup_file.name, error=str(e))

    return {
        "timestamp": timestamp,
        "restored": restored,
        "errors": errors,
        "restored_at": datetime.now(timezone.utc).isoformat(),
    }
