"""流水线阶段追踪与产物持久化（P3-1 扩展）

记录每次编译流水线运行，并持久化每个阶段的输入/输出产物，
支持事后查看阶段数据并从任意阶段重处理。

阶段定义（4 个标准阶段）:
- parse:    输入 = 原始文件元数据；输出 = ParsedDocument（序列化）
- extract:  输入 = ParsedDocument；           输出 = ExtractionResult（序列化）
- compile:  输入 = ParsedDocument + ExtractionResult；输出 = WikiCompileResult 摘要
- index:    输入 = 无；                        输出 = 重建索引结果

存储布局:
- SQLite 表 `pipeline_runs` 已存在（document_store.py 维护）
- 新增 SQLite 表 `pipeline_artifacts` 持久化每个阶段的产物（JSON 序列化）
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from app.storage.connection import ConnectionPool
from app.storage.document_store import DB_PATH

logger = structlog.get_logger()

ARTIFACTS_DB_PATH = DB_PATH  # 与 documents.db 同库，schema 一起初始化


def _get_db() -> sqlite3.Connection:
    """复用 document_store 的连接池（schema 在 document_store._init_schema 中初始化）"""
    # 注意：ConnectionPool 用首次注册的 init 函数；document_store 通常先被实例化，
    # 因此 pipeline_artifacts 表已由 document_store._init_schema 创建。
    # 为兼容首次调用即从 pipeline_tracker 入口的场景，这里也提供一个兜底 init。
    return ConnectionPool.get(str(ARTIFACTS_DB_PATH), _ensure_artifact_schema).get_connection()


def _ensure_artifact_schema(conn: sqlite3.Connection) -> None:
    """兜底初始化 pipeline_artifacts 表（不会与 document_store 的初始化冲突）"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pipeline_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            direction TEXT NOT NULL,
            payload TEXT NOT NULL,
            payload_size INTEGER NOT NULL DEFAULT 0,
            mime_type TEXT DEFAULT 'application/json',
            created_at TEXT NOT NULL,
            UNIQUE(run_id, stage, direction)
        );
        CREATE INDEX IF NOT EXISTS idx_artifact_run ON pipeline_artifacts(run_id);
        CREATE INDEX IF NOT EXISTS idx_artifact_doc ON pipeline_artifacts(doc_id);
        CREATE INDEX IF NOT EXISTS idx_artifact_stage ON pipeline_artifacts(stage);
    """)


# 4 个标准阶段（与 document_store.create_pipeline_run 一致）
PIPELINE_STAGES = ["parse", "extract", "compile", "index"]


class PipelineTracker:
    """流水线阶段追踪器

    提供：
    - 持久化每个阶段的输入/输出产物
    - 查询历史运行及阶段数据
    - 加载指定阶段的输入/输出（用于查看或重处理）
    """

    # ────────── 写入 ──────────

    def save_artifact(
        self,
        run_id: str,
        doc_id: str,
        stage: str,
        direction: str,  # input | output
        payload: Any,
    ) -> None:
        """持久化阶段产物（覆盖式写入，同一 run/stage/direction 仅保留最新版本）

        Args:
            run_id: 流水线运行 ID
            doc_id: 文档 ID
            stage: parse | extract | compile | index
            direction: input | output
            payload: 任意 JSON 可序列化对象（dict/list/str）
        """
        if stage not in PIPELINE_STAGES:
            logger.warning("pipeline_tracker_unknown_stage", stage=stage)
        if direction not in ("input", "output"):
            raise ValueError(f"direction must be 'input' or 'output', got: {direction}")

        try:
            payload_str = json.dumps(payload, ensure_ascii=False, default=_json_default)
        except (TypeError, ValueError) as e:
            logger.error("pipeline_tracker_serialize_failed",
                         run_id=run_id, stage=stage, error=str(e))
            payload_str = json.dumps({
                "_serialize_error": str(e),
                "_preview": str(payload)[:500],
            })

        payload_size = len(payload_str)
        now = datetime.now(timezone.utc).isoformat()
        conn = _get_db()
        conn.execute(
            """
            INSERT INTO pipeline_artifacts
                (run_id, doc_id, stage, direction, payload, payload_size, mime_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'application/json', ?)
            ON CONFLICT(run_id, stage, direction) DO UPDATE SET
                payload = excluded.payload,
                payload_size = excluded.payload_size,
                created_at = excluded.created_at
            """,
            (run_id, doc_id, stage, direction, payload_str, payload_size, now),
        )
        conn.commit()

    # ────────── 读取 ──────────

    def get_artifact(
        self, run_id: str, stage: str, direction: str,
    ) -> dict | list | None:
        """获取指定阶段的输入/输出产物（反序列化为 Python 对象）"""
        conn = _get_db()
        row = conn.execute(
            """SELECT payload FROM pipeline_artifacts
               WHERE run_id=? AND stage=? AND direction=?""",
            (run_id, stage, direction),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            return None

    def get_artifact_meta(
        self, run_id: str, stage: str, direction: str,
    ) -> dict | None:
        """获取产物元数据（不含 payload，节省传输）"""
        conn = _get_db()
        row = conn.execute(
            """SELECT run_id, doc_id, stage, direction, payload_size,
                      mime_type, created_at
               FROM pipeline_artifacts
               WHERE run_id=? AND stage=? AND direction=?""",
            (run_id, stage, direction),
        ).fetchone()
        return dict(row) if row else None

    def list_stage_artifacts(self, run_id: str) -> list[dict]:
        """列出某次运行的所有阶段产物元数据"""
        conn = _get_db()
        rows = conn.execute(
            """SELECT run_id, doc_id, stage, direction, payload_size,
                      mime_type, created_at
               FROM pipeline_artifacts
               WHERE run_id=?
               ORDER BY id ASC""",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_runs(
        self,
        doc_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """列出流水线运行记录（含 step 状态摘要）"""
        conn = _get_db()
        clauses = []
        params: list[Any] = []
        if doc_id:
            clauses.append("doc_id=?")
            params.append(doc_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        rows = conn.execute(
            f"""SELECT run_id, doc_id, status, current_step, steps_json,
                       started_at, finished_at, error_message, created_at
                FROM pipeline_runs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?""",
            params,
        ).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            try:
                item["steps"] = json.loads(item.pop("steps_json", "[]"))
            except (json.JSONDecodeError, TypeError):
                item["steps"] = []
            result.append(item)
        return result

    def count_runs(
        self, doc_id: str | None = None, status: str | None = None,
    ) -> int:
        """统计运行总数"""
        conn = _get_db()
        clauses = []
        params: list[Any] = []
        if doc_id:
            clauses.append("doc_id=?")
            params.append(doc_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM pipeline_runs {where_clause}",
            params,
        ).fetchone()
        return row["cnt"] if row else 0

    # ────────── 删除 ──────────

    def delete_run(self, run_id: str) -> int:
        """删除一次运行的产物（保留 pipeline_runs 状态记录）"""
        conn = _get_db()
        cur = conn.execute(
            "DELETE FROM pipeline_artifacts WHERE run_id=?", (run_id,),
        )
        conn.commit()
        return cur.rowcount

    def delete_doc_artifacts(self, doc_id: str) -> int:
        """删除文档的所有阶段产物（保留 pipeline_runs 状态记录）"""
        conn = _get_db()
        cur = conn.execute(
            "DELETE FROM pipeline_artifacts WHERE doc_id=?", (doc_id,),
        )
        conn.commit()
        return cur.rowcount


def _json_default(obj: Any) -> Any:
    """JSON 序列化兜底：dataclass → __dict__，对象 → str"""
    if hasattr(obj, "__dict__"):
        # dataclass 实例
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, (datetime, Path)):
        return str(obj)
    return str(obj)


# 全局单例
_tracker: PipelineTracker | None = None


def get_pipeline_tracker() -> PipelineTracker:
    global _tracker
    if _tracker is None:
        _tracker = PipelineTracker()
    return _tracker
