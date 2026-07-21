"""章节存储

管理 Section 元数据和 section_contributions 双向更新索引表。

section_contributions 是系统的核心追踪表，记录每个 Section 对下游产物的贡献:
    section_id → wiki_slug       (Wiki 页面)
    section_id → entity_slug     (Graph 实体)
    section_id → relation_id     (Graph 关系)
    section_id → output_doc_id   (输出文档)

这张表是双向更新、漂移检测、增量重编译的基础。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.sections.splitter import SectionSplitResult


@dataclass
class SectionRecord:
    """Section 元数据记录"""
    section_id: str
    source_doc_id: str
    parent_section_id: str | None
    title: str
    level: int
    semantic_role: str
    index: int
    compiled_version: int
    compiled_checksum: str | None
    compiled_at: str | None
    created_at: str
    updated_at: str
    metadata: dict = field(default_factory=dict)


@dataclass
class SectionContribution:
    """Section 贡献关系"""
    section_id: str
    source_doc_id: str
    target_type: str       # 'wiki_page' | 'graph_entity' | 'graph_relation' | 'output_doc'
    target_slug: str
    contribution_type: str  # 'primary' | 'supplementary' | 'reference'
    compiled_version: int
    compiled_at: str


class SectionStore:
    """章节存储管理器

    管理两张核心表:
    - sections: Section 元数据
    - section_contributions: 双向更新索引
    """

    def __init__(self, db_path: str = "data/events.db"):
        self.db_path = Path(db_path)
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_tables(self) -> None:
        with self._get_conn() as conn:
            # Section 元数据表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sections (
                    section_id       TEXT PRIMARY KEY,
                    source_doc_id    TEXT NOT NULL,
                    parent_section_id TEXT,
                    title            TEXT NOT NULL,
                    level            INTEGER NOT NULL DEFAULT 1,
                    semantic_role    TEXT NOT NULL DEFAULT 'general',
                    idx              INTEGER NOT NULL DEFAULT 0,
                    compiled_version INTEGER NOT NULL DEFAULT 0,
                    compiled_checksum TEXT,
                    compiled_at      TEXT,
                    created_at       TEXT NOT NULL,
                    updated_at       TEXT NOT NULL,
                    metadata         TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sections_source
                ON sections(source_doc_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sections_role
                ON sections(semantic_role)
            """)

            # 贡献关系表（核心双向索引）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS section_contributions (
                    section_id        TEXT NOT NULL,
                    source_doc_id     TEXT NOT NULL,
                    target_type       TEXT NOT NULL,
                    target_slug       TEXT NOT NULL,
                    contribution_type TEXT NOT NULL DEFAULT 'primary',
                    compiled_version  INTEGER NOT NULL,
                    compiled_at       TEXT NOT NULL,
                    PRIMARY KEY (section_id, target_type, target_slug)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sc_target
                ON section_contributions(target_type, target_slug)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sc_section
                ON section_contributions(section_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sc_source
                ON section_contributions(source_doc_id)
            """)
            conn.commit()

    # ─── Section 元数据 CRUD ──────────────────────────────────────

    def save_sections(self, result: SectionSplitResult) -> list[SectionRecord]:
        """保存拆分结果到 sections 表"""
        now = datetime.now(timezone.utc).isoformat()
        records: list[SectionRecord] = []

        with self._get_conn() as conn:
            for section in result.sections:
                record = SectionRecord(
                    section_id=section.section_id,
                    source_doc_id=section.source_doc_id,
                    parent_section_id=section.parent_section_id,
                    title=section.title,
                    level=section.level,
                    semantic_role=section.semantic_role,
                    index=section.index,
                    compiled_version=section.compiled_version,
                    compiled_checksum=None,
                    compiled_at=section.compiled_at,
                    created_at=now,
                    updated_at=now,
                    metadata=section.metadata,
                )
                records.append(record)

                conn.execute(
                    """INSERT OR REPLACE INTO sections
                       (section_id, source_doc_id, parent_section_id, title, level,
                        semantic_role, idx, compiled_version, compiled_checksum,
                        compiled_at, created_at, updated_at, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.section_id, record.source_doc_id,
                        record.parent_section_id, record.title, record.level,
                        record.semantic_role, record.index,
                        record.compiled_version, record.compiled_checksum,
                        record.compiled_at, record.created_at, record.updated_at,
                        json.dumps(record.metadata, ensure_ascii=False),
                    ),
                )
            conn.commit()

        return records

    def get_by_doc(self, doc_id: str) -> list[SectionRecord]:
        """按文档 ID 查询所有章节"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT section_id, source_doc_id, parent_section_id, title, level,
                          semantic_role, idx, compiled_version, compiled_checksum,
                          compiled_at, created_at, updated_at, metadata
                   FROM sections
                   WHERE source_doc_id = ?
                   ORDER BY idx""",
                (doc_id,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_by_id(self, section_id: str) -> SectionRecord | None:
        """按 ID 查询单个章节"""
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT section_id, source_doc_id, parent_section_id, title, level,
                          semantic_role, idx, compiled_version, compiled_checksum,
                          compiled_at, created_at, updated_at, metadata
                   FROM sections WHERE section_id = ?""",
                (section_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def update_compiled(
        self, section_id: str, version: int, checksum: str,
    ) -> None:
        """更新章节的编译状态"""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE sections
                   SET compiled_version = ?, compiled_checksum = ?, compiled_at = ?,
                       updated_at = ?
                   WHERE section_id = ?""",
                (version, checksum, now, now, section_id),
            )
            conn.commit()

    # ─── 贡献关系 CRUD ────────────────────────────────────────────

    def add_contribution(
        self,
        section_id: str,
        source_doc_id: str,
        target_type: str,
        target_slug: str,
        contribution_type: str = 'primary',
        compiled_version: int = 1,
    ) -> SectionContribution:
        """添加贡献关系"""
        now = datetime.now(timezone.utc).isoformat()
        contrib = SectionContribution(
            section_id=section_id,
            source_doc_id=source_doc_id,
            target_type=target_type,
            target_slug=target_slug,
            contribution_type=contribution_type,
            compiled_version=compiled_version,
            compiled_at=now,
        )
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO section_contributions
                   (section_id, source_doc_id, target_type, target_slug,
                    contribution_type, compiled_version, compiled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    contrib.section_id, contrib.source_doc_id,
                    contrib.target_type, contrib.target_slug,
                    contrib.contribution_type, contrib.compiled_version,
                    contrib.compiled_at,
                ),
            )
            conn.commit()
        return contrib

    def get_contributions_by_section(self, section_id: str) -> list[SectionContribution]:
        """查询某个章节的所有贡献"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT section_id, source_doc_id, target_type, target_slug,
                          contribution_type, compiled_version, compiled_at
                   FROM section_contributions
                   WHERE section_id = ?
                   ORDER BY compiled_at DESC""",
                (section_id,),
            ).fetchall()
        return [self._row_to_contrib(r) for r in rows]

    def get_contributions_by_target(
        self, target_type: str, target_slug: str,
    ) -> list[SectionContribution]:
        """查询某个目标（Wiki页面/Graph实体）的所有贡献来源

        这是双向更新的核心查询：给定一个 Wiki 页面 slug，
        返回所有贡献了内容的 Section。
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT section_id, source_doc_id, target_type, target_slug,
                          contribution_type, compiled_version, compiled_at
                   FROM section_contributions
                   WHERE target_type = ? AND target_slug = ?
                   ORDER BY compiled_at DESC""",
                (target_type, target_slug),
            ).fetchall()
        return [self._row_to_contrib(r) for r in rows]

    def get_affected_targets(
        self, section_id: str,
    ) -> list[tuple[str, str]]:
        """查询某个章节影响的所有下游目标

        Returns:
            [(target_type, target_slug), ...] 用于触发增量更新
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT DISTINCT target_type, target_slug
                   FROM section_contributions
                   WHERE section_id = ?""",
                (section_id,),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def remove_contributions(self, section_id: str) -> int:
        """移除章节的所有贡献关系（重编译前清理）"""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM section_contributions WHERE section_id = ?",
                (section_id,),
            )
            conn.commit()
            return cursor.rowcount

    def get_all_section_ids(self) -> list[str]:
        """获取所有 section_id"""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT section_id FROM sections").fetchall()
        return [r[0] for r in rows]

    # ─── 批量操作 ─────────────────────────────────────────────────

    def batch_add_contributions(
        self, contributions: list[SectionContribution],
    ) -> None:
        """批量添加贡献关系"""
        with self._get_conn() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO section_contributions
                   (section_id, source_doc_id, target_type, target_slug,
                    contribution_type, compiled_version, compiled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        c.section_id, c.source_doc_id, c.target_type,
                        c.target_slug, c.contribution_type,
                        c.compiled_version, c.compiled_at,
                    )
                    for c in contributions
                ],
            )
            conn.commit()

    def get_stats(self) -> dict:
        """获取存储统计"""
        with self._get_conn() as conn:
            section_count = conn.execute(
                "SELECT COUNT(*) FROM sections"
            ).fetchone()[0]
            contrib_count = conn.execute(
                "SELECT COUNT(*) FROM section_contributions"
            ).fetchone()[0]
            contrib_by_type = conn.execute(
                """SELECT target_type, COUNT(*)
                   FROM section_contributions
                   GROUP BY target_type"""
            ).fetchall()
        return {
            'section_count': section_count,
            'contribution_count': contrib_count,
            'contributions_by_type': dict(contrib_by_type),
        }

    # ─── 内部方法 ─────────────────────────────────────────────────

    def _row_to_record(self, row: tuple) -> SectionRecord:
        return SectionRecord(
            section_id=row[0],
            source_doc_id=row[1],
            parent_section_id=row[2],
            title=row[3],
            level=row[4],
            semantic_role=row[5],
            index=row[6],
            compiled_version=row[7],
            compiled_checksum=row[8],
            compiled_at=row[9],
            created_at=row[10],
            updated_at=row[11],
            metadata=json.loads(row[12]) if row[12] else {},
        )

    def _row_to_contrib(self, row: tuple) -> SectionContribution:
        return SectionContribution(
            section_id=row[0],
            source_doc_id=row[1],
            target_type=row[2],
            target_slug=row[3],
            contribution_type=row[4],
            compiled_version=row[5],
            compiled_at=row[6],
        )
