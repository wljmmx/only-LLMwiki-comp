"""文档持久化存储（P0-1）

文件存储到 data/uploads/，元数据存到 SQLite。
解析后的文件不再删除，支持后续检索和版本控制。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# 存储根目录
STORAGE_ROOT = Path(__file__).parent.parent.parent / "data"
UPLOADS_DIR = STORAGE_ROOT / "uploads"
DB_PATH = STORAGE_ROOT / "documents.db"


def _ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def _get_db() -> sqlite3.Connection:
    _ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            format TEXT NOT NULL,
            ext TEXT NOT NULL,
            checksum TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            title TEXT,
            status TEXT DEFAULT 'uploaded' CHECK(status IN ('uploaded', 'parsed', 'extracted', 'compiled', 'error')),
            parse_result TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_doc_checksum ON documents(checksum);
        CREATE INDEX IF NOT EXISTS idx_doc_format ON documents(format);
        CREATE INDEX IF NOT EXISTS idx_doc_status ON documents(status);
        CREATE INDEX IF NOT EXISTS idx_doc_filename ON documents(filename);
    """)


class DocumentStore:
    """文档持久化存储"""

    def save(
        self,
        filename: str,
        content: bytes,
        fmt: str,
        doc_id: str | None = None,
    ) -> dict:
        """保存上传的文档，返回元数据"""
        _ensure_dirs()
        checksum = hashlib.sha256(content).hexdigest()

        # 重复检查（基于 checksum）
        existing = self._find_by_checksum(checksum)
        if existing:
            logger.info(
                "doc_dedup_skip", checksum=checksum, existing_id=existing["doc_id"]
            )
            return existing

        # 生成 doc_id 和存储路径
        if not doc_id:
            doc_id = f"doc-{checksum[:12]}"
        ext = Path(filename).suffix.lower()
        stored_name = f"{doc_id}{ext}"
        stored_path = UPLOADS_DIR / stored_name
        stored_path.write_bytes(content)

        now = datetime.now(timezone.utc).isoformat()
        conn = _get_db()
        conn.execute(
            """INSERT INTO documents
               (doc_id, filename, format, ext, checksum, stored_path,
                size_bytes, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'uploaded', ?, ?)""",
            (
                doc_id,
                filename,
                fmt,
                ext,
                checksum,
                str(stored_path),
                len(content),
                now,
                now,
            ),
        )
        conn.commit()
        logger.info("doc_saved", doc_id=doc_id, filename=filename, size=len(content))
        return self.get(doc_id)

    def get(self, doc_id: str) -> dict | None:
        """获取文档元数据"""
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_id(self, internal_id: int) -> dict | None:
        """按内部 ID 获取"""
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (internal_id,)
        ).fetchone()
        return dict(row) if row else None

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        fmt: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """列出文档"""
        conn = _get_db()
        query = "SELECT * FROM documents WHERE 1=1"
        params: list[Any] = []
        if fmt:
            query += " AND format = ?"
            params.append(fmt)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def read_content(self, doc_id: str) -> bytes | None:
        """读取文档原始内容"""
        doc = self.get(doc_id)
        if not doc:
            return None
        path = Path(doc["stored_path"])
        if not path.exists():
            return None
        return path.read_bytes()

    def update_status(
        self,
        doc_id: str,
        status: str,
        title: str | None = None,
        parse_result: dict | None = None,
    ) -> bool:
        """更新文档状态"""
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, now]
        if title:
            sets.append("title = ?")
            params.append(title)
        if parse_result:
            sets.append("parse_result = ?")
            params.append(json.dumps(parse_result, ensure_ascii=False))
        params.append(doc_id)
        conn.execute(f"UPDATE documents SET {', '.join(sets)} WHERE doc_id = ?", params)
        conn.commit()
        return conn.total_changes > 0

    def delete(self, doc_id: str) -> bool:
        """删除文档（文件+元数据）"""
        doc = self.get(doc_id)
        if not doc:
            return False
        # 删除文件
        path = Path(doc["stored_path"])
        if path.exists():
            path.unlink()
        # 删除元数据
        conn = _get_db()
        conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
        conn.commit()
        logger.info("doc_deleted", doc_id=doc_id)
        return True

    def search(self, keyword: str, limit: int = 20) -> list[dict]:
        """按文件名/标题搜索"""
        conn = _get_db()
        rows = conn.execute(
            """SELECT * FROM documents
               WHERE filename LIKE ? OR title LIKE ? OR doc_id LIKE ?
               ORDER BY created_at DESC LIMIT ?""",
            (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """文档统计"""
        conn = _get_db()
        total = conn.execute("SELECT COUNT(*) as cnt FROM documents").fetchone()
        by_format = conn.execute(
            "SELECT format, COUNT(*) as cnt FROM documents GROUP BY format ORDER BY cnt DESC"
        ).fetchall()
        by_status = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM documents GROUP BY status"
        ).fetchall()
        total_size = conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) as sz FROM documents"
        ).fetchone()
        return {
            "total": total["cnt"] if total else 0,
            "total_size_mb": round(
                (total_size["sz"] if total_size else 0) / 1024 / 1024, 2
            ),
            "by_format": [dict(r) for r in by_format],
            "by_status": [dict(r) for r in by_status],
        }

    def _find_by_checksum(self, checksum: str) -> dict | None:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM documents WHERE checksum = ?", (checksum,)
        ).fetchone()
        return dict(row) if row else None


# 全局单例
_store: DocumentStore | None = None


def get_document_store() -> DocumentStore:
    global _store
    if _store is None:
        _store = DocumentStore()
    return _store
