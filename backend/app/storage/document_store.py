"""文档持久化存储（P0-1）

文件存储到 data/uploads/，元数据存到 SQLite。
解析后的文件不再删除，支持后续检索和版本控制。

P3-1: 新增 pipeline_runs 表，追踪流水线步骤级执行状态。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
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

        -- P3-1: PipelineRun 状态机
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','running','done','error','cancelled')),
            current_step TEXT,
            steps_json TEXT NOT NULL DEFAULT '[]',
            started_at TEXT,
            finished_at TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
        );
        CREATE INDEX IF NOT EXISTS idx_pipeline_doc ON pipeline_runs(doc_id);
        CREATE INDEX IF NOT EXISTS idx_pipeline_status ON pipeline_runs(status);
    """)


def _normalize_doc(doc: dict) -> dict:
    """统一文档字段名，兼容前端

    DB 字段 size_bytes，前端读 size（DocumentMeta.size）。
    DB 字段 doc_id（string UUID），前端读 id（DocumentMeta.id）。
    这里加 size 别名和 id 别名，避免字段不匹配导致前端大小显示空、
    以及 API 路径参数用错整型 id 导致 404。
    """
    if "size" not in doc and "size_bytes" in doc:
        doc["size"] = doc["size_bytes"]
    if "doc_id" in doc:
        doc["id"] = doc["doc_id"]
    return doc


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
        return _normalize_doc(dict(row)) if row else None

    def get_by_id(self, internal_id: int) -> dict | None:
        """按内部 ID 获取"""
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (internal_id,)
        ).fetchone()
        return _normalize_doc(dict(row)) if row else None

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
        return [_normalize_doc(dict(r)) for r in rows]

    def count(
        self,
        fmt: str | None = None,
        status: str | None = None,
    ) -> int:
        """统计文档总数（支持按 format/status 过滤）"""
        conn = _get_db()
        query = "SELECT COUNT(*) as cnt FROM documents WHERE 1=1"
        params: list[Any] = []
        if fmt:
            query += " AND format = ?"
            params.append(fmt)
        if status:
            query += " AND status = ?"
            params.append(status)
        row = conn.execute(query, params).fetchone()
        return row["cnt"] if row else 0

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
        return [_normalize_doc(dict(r)) for r in rows]

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

    # ────────── P3-1: PipelineRun 状态机 ──────────

    def create_pipeline_run(self, doc_id: str) -> str:
        """创建流水线执行记录，返回 run_id"""
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        steps = json.dumps([
            {"name": "parse", "label": "解析", "status": "pending"},
            {"name": "extract", "label": "知识抽取", "status": "pending"},
            {"name": "compile", "label": "编译 Wiki", "status": "pending"},
            {"name": "index", "label": "重建索引", "status": "pending"},
        ])
        conn = _get_db()
        conn.execute(
            """INSERT INTO pipeline_runs (run_id, doc_id, status, steps_json, created_at)
               VALUES (?, ?, 'pending', ?, ?)""",
            (run_id, doc_id, steps, now),
        )
        conn.commit()
        return run_id

    def start_pipeline_run(self, run_id: str, current_step: str) -> bool:
        """标记流水线开始执行"""
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE pipeline_runs
               SET status='running', current_step=?, started_at=?
               WHERE run_id=?""",
            (current_step, now, run_id),
        )
        conn.commit()
        return conn.total_changes > 0

    def update_pipeline_step(
        self, run_id: str, step_name: str, step_status: str,
        error: str | None = None, duration_ms: int | None = None,
    ) -> bool:
        """更新流水线中某个步骤的状态"""
        conn = _get_db()
        row = conn.execute(
            "SELECT steps_json FROM pipeline_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if not row:
            return False
        steps = json.loads(row["steps_json"])
        for s in steps:
            if s["name"] == step_name:
                s["status"] = step_status
                if error:
                    s["error"] = error
                if duration_ms is not None:
                    s["duration_ms"] = duration_ms
                break
        conn.execute(
            "UPDATE pipeline_runs SET steps_json=?, current_step=? WHERE run_id=?",
            (json.dumps(steps), step_name, run_id),
        )
        conn.commit()
        return True

    def finish_pipeline_run(self, run_id: str, status: str = "done") -> bool:
        """标记流水线完成"""
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE pipeline_runs SET status=?, finished_at=? WHERE run_id=?",
            (status, now, run_id),
        )
        conn.commit()
        return conn.total_changes > 0

    def fail_pipeline_run(self, run_id: str, error: str) -> bool:
        """标记流水线失败"""
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE pipeline_runs SET status='error', finished_at=?, error_message=? WHERE run_id=?",
            (now, error, run_id),
        )
        conn.commit()
        return conn.total_changes > 0

    def get_pipeline_run(self, run_id: str) -> dict | None:
        """获取流水线执行记录"""
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM pipeline_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["steps"] = json.loads(result["steps_json"])
        return result

    def get_latest_pipeline_run(self, doc_id: str) -> dict | None:
        """获取文档最近的流水线执行记录"""
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM pipeline_runs WHERE doc_id=? ORDER BY created_at DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["steps"] = json.loads(result["steps_json"])
        return result


# 全局单例
_store: DocumentStore | None = None


def get_document_store() -> DocumentStore:
    global _store
    if _store is None:
        _store = DocumentStore()
    return _store
