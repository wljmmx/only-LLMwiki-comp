"""版本控制（P1-2）

为生成的文档和编辑过的内容提供修订历史。
每次保存创建新版本，支持 diff 对比和回滚。
"""

from __future__ import annotations

import difflib
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent.parent / "data" / "versions.db"


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS document_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_key TEXT NOT NULL,
            version INTEGER NOT NULL,
            title TEXT,
            content TEXT NOT NULL,
            checksum TEXT NOT NULL,
            author TEXT DEFAULT 'user',
            change_summary TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(doc_key, version)
        );
        CREATE INDEX IF NOT EXISTS idx_ver_key ON document_versions(doc_key);
        CREATE INDEX IF NOT EXISTS idx_ver_version ON document_versions(doc_key, version DESC);
    """)


class VersionControl:
    """文档版本控制"""

    def save_version(
        self,
        doc_key: str,
        title: str,
        content: str,
        author: str = "user",
        change_summary: str = "",
    ) -> dict:
        """保存新版本

        Args:
            doc_key: 文档标识（如 doc_id 或 wiki slug）
            title: 标题
            content: 完整内容
            author: 作者
            change_summary: 变更摘要

        并发安全：
        - 使用 BEGIN IMMEDIATE 事务在 WAL 模式下获取排他写锁
        - 防止 SELECT MAX(version) → INSERT 之间的 TOCTOU 竞态
        - UNIQUE(doc_key, version) 约束作为兜底保护
        """
        conn = _get_db()
        now = datetime.now(timezone.utc).isoformat()

        # BEGIN IMMEDIATE：在 WAL 模式下获取排他写锁，序列化并发写入
        conn.execute("BEGIN IMMEDIATE")
        try:
            # 获取当前最大版本号
            row = conn.execute(
                "SELECT MAX(version) as max_ver FROM document_versions WHERE doc_key = ?",
                (doc_key,),
            ).fetchone()
            next_version = (row["max_ver"] or 0) + 1

            checksum = hashlib.sha256(content.encode()).hexdigest()[:16]

            # 检查内容是否变化（与上一版本对比）
            if row["max_ver"]:
                prev = conn.execute(
                    "SELECT checksum FROM document_versions WHERE doc_key = ? AND version = ?",
                    (doc_key, row["max_ver"]),
                ).fetchone()
                if prev and prev["checksum"] == checksum:
                    conn.execute("COMMIT")
                    return {
                        "doc_key": doc_key,
                        "version": row["max_ver"],
                        "skipped": True,
                        "reason": "内容无变化",
                    }

            conn.execute(
                """INSERT OR IGNORE INTO document_versions
                   (doc_key, version, title, content, checksum, author, change_summary, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_key,
                    next_version,
                    title,
                    content,
                    checksum,
                    author,
                    change_summary,
                    now,
                ),
            )

            # 如果 INSERT OR IGNORE 因为 UNIQUE 冲突被跳过（极端并发竞态），
            # 重读当前最新版本号
            if conn.total_changes == 0:
                row = conn.execute(
                    "SELECT MAX(version) as max_ver FROM document_versions WHERE doc_key = ?",
                    (doc_key,),
                ).fetchone()
                next_version = row["max_ver"] if row else 0
                conn.execute("COMMIT")
                return {
                    "doc_key": doc_key,
                    "version": next_version,
                    "skipped": True,
                    "reason": "并发写入冲突，已自动解决",
                }

            conn.commit()
            logger.info("version_saved", doc_key=doc_key, version=next_version)
            return {
                "doc_key": doc_key,
                "version": next_version,
                "title": title,
                "checksum": checksum,
                "created_at": now,
            }
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_version(self, doc_key: str, version: int) -> dict | None:
        """获取指定版本"""
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM document_versions WHERE doc_key = ? AND version = ?",
            (doc_key, version),
        ).fetchone()
        return dict(row) if row else None

    def get_latest(self, doc_key: str) -> dict | None:
        """获取最新版本"""
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM document_versions WHERE doc_key = ? ORDER BY version DESC LIMIT 1",
            (doc_key,),
        ).fetchone()
        return dict(row) if row else None

    def list_versions(self, doc_key: str) -> list[dict]:
        """列出所有版本（不含内容，减少传输）"""
        conn = _get_db()
        rows = conn.execute(
            """SELECT id, doc_key, version, title, checksum, author,
                      change_summary, created_at
               FROM document_versions WHERE doc_key = ? ORDER BY version DESC""",
            (doc_key,),
        ).fetchall()
        return [dict(r) for r in rows]

    def diff(self, doc_key: str, v1: int, v2: int) -> dict:
        """对比两个版本的差异"""
        conn = _get_db()
        r1 = conn.execute(
            "SELECT content, title FROM document_versions WHERE doc_key = ? AND version = ?",
            (doc_key, v1),
        ).fetchone()
        r2 = conn.execute(
            "SELECT content, title FROM document_versions WHERE doc_key = ? AND version = ?",
            (doc_key, v2),
        ).fetchone()
        if not r1 or not r2:
            return {"error": "版本不存在"}

        lines1 = r1["content"].splitlines(keepends=True)
        lines2 = r2["content"].splitlines(keepends=True)
        diff = list(
            difflib.unified_diff(
                lines1,
                lines2,
                fromfile=f"v{v1}",
                tofile=f"v{v2}",
                lineterm="",
            )
        )

        # 统计
        added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

        return {
            "doc_key": doc_key,
            "v1": v1,
            "v2": v2,
            "added_lines": added,
            "removed_lines": removed,
            "diff": "".join(diff),
        }

    def rollback(self, doc_key: str, target_version: int, author: str = "user") -> dict:
        """回滚到指定版本（创建新版本，内容为目标版本）"""
        target = self.get_version(doc_key, target_version)
        if not target:
            return {"error": f"版本 {target_version} 不存在"}

        return self.save_version(
            doc_key=doc_key,
            title=target["title"],
            content=target["content"],
            author=author,
            change_summary=f"回滚到版本 {target_version}",
        )

    def delete_all(self, doc_key: str) -> int:
        """删除文档的所有版本"""
        conn = _get_db()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                "DELETE FROM document_versions WHERE doc_key = ?", (doc_key,)
            )
            conn.commit()
            return cursor.rowcount
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def list_by_prefix(self, prefix: str, limit: int = 500) -> list[dict]:
        """按 doc_key 前缀列出最新版本（用于 wiki:* 列表）

        返回每个 doc_key 的最新版本（不含 content 字段，减少传输）。
        """
        conn = _get_db()
        rows = conn.execute(
            """SELECT dv.id, dv.doc_key, dv.version, dv.title, dv.checksum,
                      dv.author, dv.change_summary, dv.created_at
               FROM document_versions dv
               INNER JOIN (
                   SELECT doc_key, MAX(version) as max_v
                   FROM document_versions
                   WHERE doc_key LIKE ?
                   GROUP BY doc_key
               ) latest ON dv.doc_key = latest.doc_key AND dv.version = latest.max_v
               WHERE dv.doc_key LIKE ?
               ORDER BY dv.created_at DESC
               LIMIT ?""",
            (f"{prefix}%", f"{prefix}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]


# 全局单例
_vc: VersionControl | None = None


def get_version_control() -> VersionControl:
    global _vc
    if _vc is None:
        _vc = VersionControl()
    return _vc
