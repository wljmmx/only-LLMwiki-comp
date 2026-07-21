"""附件存储

管理附件文件的存储和索引。

attachment_index 表结构:
    ref_id             TEXT PRIMARY KEY  -- 附件引用 ID
    parent_doc_id      TEXT NOT NULL     -- 来源文档 ID
    parent_section_id  TEXT              -- 来源章节 ID（章节拆分后填充）
    attachment_type    TEXT NOT NULL     -- 'image' | 'file' | 'figure_ref'
    subtype            TEXT              -- 'base64' | 'markdown_ref' | 'html_tag' | 'file_ref' | 'figure_ref'
    original_ref       TEXT              -- 原文引用文本
    file_path          TEXT              -- 提取后的文件路径
    mime_type          TEXT              -- MIME 类型
    context            TEXT              -- 上下文描述
    linked_doc_id      TEXT              -- 提取为独立 L0 文档的 ID
    extracted_at       TEXT NOT NULL     -- 提取时间 ISO 8601
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.attachments.extractor import AttachmentRef, AttachmentExtractionResult


@dataclass
class AttachmentRecord:
    """附件索引记录"""
    ref_id: str
    parent_doc_id: str
    parent_section_id: str | None
    attachment_type: str
    subtype: str
    original_ref: str
    file_path: str | None
    mime_type: str | None
    context: str
    linked_doc_id: str | None
    extracted_at: str


class AttachmentStore:
    """附件存储管理器

    管理 attachment_index 表（SQLite），提供附件记录的 CRUD 操作。
    表结构支持与 section_contributions 表关联查询。
    """

    def __init__(self, db_path: str = "data/events.db"):
        self.db_path = Path(db_path)
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_table(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS attachment_index (
                    ref_id             TEXT PRIMARY KEY,
                    parent_doc_id      TEXT NOT NULL,
                    parent_section_id  TEXT,
                    attachment_type    TEXT NOT NULL,
                    subtype            TEXT NOT NULL,
                    original_ref       TEXT,
                    file_path          TEXT,
                    mime_type          TEXT,
                    context            TEXT DEFAULT '',
                    linked_doc_id      TEXT,
                    extracted_at       TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_att_parent_doc
                ON attachment_index(parent_doc_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_att_parent_section
                ON attachment_index(parent_section_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_att_linked_doc
                ON attachment_index(linked_doc_id)
            """)
            conn.commit()

    def save_result(
        self, doc_id: str, result: AttachmentExtractionResult,
    ) -> list[AttachmentRecord]:
        """保存附件提取结果到索引

        Args:
            doc_id: 来源文档 ID
            result: 附件提取结果

        Returns:
            保存的 AttachmentRecord 列表
        """
        now = datetime.now(timezone.utc).isoformat()
        records: list[AttachmentRecord] = []

        with self._get_conn() as conn:
            for ref in result.refs:
                record = AttachmentRecord(
                    ref_id=ref.ref_id,
                    parent_doc_id=doc_id,
                    parent_section_id=None,  # 章节拆分后由 SectionStore 填充
                    attachment_type=ref.attachment_type,
                    subtype=ref.subtype,
                    original_ref=ref.original_text,
                    file_path=ref.linked_file_path,
                    mime_type=f'image/{ref.format}' if ref.format else None,
                    context=ref.context,
                    linked_doc_id=ref.linked_doc_id,
                    extracted_at=now,
                )
                records.append(record)

                conn.execute(
                    """INSERT OR REPLACE INTO attachment_index
                       (ref_id, parent_doc_id, parent_section_id, attachment_type,
                        subtype, original_ref, file_path, mime_type, context,
                        linked_doc_id, extracted_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.ref_id, record.parent_doc_id, record.parent_section_id,
                        record.attachment_type, record.subtype, record.original_ref,
                        record.file_path, record.mime_type, record.context,
                        record.linked_doc_id, record.extracted_at,
                    ),
                )
            conn.commit()

        return records

    def get_by_doc(self, doc_id: str) -> list[AttachmentRecord]:
        """按文档 ID 查询所有附件"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT ref_id, parent_doc_id, parent_section_id, attachment_type,
                          subtype, original_ref, file_path, mime_type, context,
                          linked_doc_id, extracted_at
                   FROM attachment_index
                   WHERE parent_doc_id = ?
                   ORDER BY ref_id""",
                (doc_id,),
            ).fetchall()

        return [self._row_to_record(r) for r in rows]

    def get_by_section(self, section_id: str) -> list[AttachmentRecord]:
        """按章节 ID 查询附件"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT ref_id, parent_doc_id, parent_section_id, attachment_type,
                          subtype, original_ref, file_path, mime_type, context,
                          linked_doc_id, extracted_at
                   FROM attachment_index
                   WHERE parent_section_id = ?
                   ORDER BY ref_id""",
                (section_id,),
            ).fetchall()

        return [self._row_to_record(r) for r in rows]

    def assign_to_section(self, ref_id: str, section_id: str) -> None:
        """将附件关联到章节"""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE attachment_index SET parent_section_id = ? WHERE ref_id = ?",
                (section_id, ref_id),
            )
            conn.commit()

    def get_images_by_doc(self, doc_id: str) -> list[AttachmentRecord]:
        """按文档 ID 查询所有图片附件"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT ref_id, parent_doc_id, parent_section_id, attachment_type,
                          subtype, original_ref, file_path, mime_type, context,
                          linked_doc_id, extracted_at
                   FROM attachment_index
                   WHERE parent_doc_id = ? AND attachment_type = 'image'
                   ORDER BY ref_id""",
                (doc_id,),
            ).fetchall()

        return [self._row_to_record(r) for r in rows]

    def _row_to_record(self, row: tuple) -> AttachmentRecord:
        return AttachmentRecord(
            ref_id=row[0],
            parent_doc_id=row[1],
            parent_section_id=row[2],
            attachment_type=row[3],
            subtype=row[4],
            original_ref=row[5] or '',
            file_path=row[6],
            mime_type=row[7],
            context=row[8] or '',
            linked_doc_id=row[9],
            extracted_at=row[10],
        )