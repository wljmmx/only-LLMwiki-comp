"""P2-5.9 list 工具 cursor/nextCursor 分页测试

覆盖：
- _encode_cursor / _decode_cursor 编解码往返
- _decode_cursor 无效输入回退 offset=0
- _tool_list_incidents 首次请求 / 带 cursor / 最后一页 / 无效 cursor 回退
- _tool_list_documents 首次请求 / 带 cursor 分页
- limit + cursor 多页组合（limit=5, 共 12 条 → 3 页）

DB 隔离：通过 monkeypatch 重定向 events.db / documents.db 到 tmp_path，
并重置全局单例。
"""
from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════ fixture ═══════════════


@pytest.fixture
def isolated_events_db(tmp_path, monkeypatch):
    """将 events.db 重定向到 tmp_path，并重置 EventCorrelator 单例"""
    import app.aiops.event_correlator as ev

    db_file = tmp_path / "events.db"
    monkeypatch.setattr(ev, "DB_PATH", db_file)
    monkeypatch.setattr(ev, "_correlator", None)
    yield db_file


@pytest.fixture
def isolated_documents_db(tmp_path, monkeypatch):
    """将 documents.db 重定向到 tmp_path，并重置 DocumentStore 单例

    同时重定向 STORAGE_ROOT/UPLOADS_DIR，避免污染真实 data/uploads/。
    """
    import app.storage.document_store as ds

    db_file = tmp_path / "documents.db"
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ds, "DB_PATH", db_file)
    monkeypatch.setattr(ds, "STORAGE_ROOT", tmp_path)
    monkeypatch.setattr(ds, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(ds, "_store", None)
    yield db_file


def _seed_incidents(count: int, status: str = "open") -> list[str]:
    """直接用 SQL 插入 N 条 incident，返回 incident_id 列表

    started_at 递增（保证 ORDER BY started_at DESC 顺序稳定）
    """
    import app.aiops.event_correlator as ev

    conn = ev._get_db()
    now = datetime.now(timezone.utc)
    ids: list[str] = []
    for i in range(count):
        inc_id = f"inc-{i:03d}"
        started = (now - timedelta(seconds=count - i)).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO incidents
               (incident_id, started_at, severity, scope, status,
                alert_count, suspected_root_cause, created_at,
                transition_history, assignee)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                inc_id,
                started,
                "high",
                "{}",
                status,
                i + 1,
                "",
                started,
                "[]",
                "",
            ),
        )
        ids.append(inc_id)
    conn.commit()
    return ids


def _seed_documents(count: int) -> list[str]:
    """直接用 SQL 插入 N 条 document 记录，返回 doc_id 列表

    created_at 递增（保证 ORDER BY created_at DESC 顺序稳定）
    """
    import app.storage.document_store as ds

    conn = ds._get_db()
    now = datetime.now(timezone.utc)
    ids: list[str] = []
    for i in range(count):
        doc_id = f"doc-{i:03d}"
        created = (now - timedelta(seconds=count - i)).isoformat()
        # stored_path 用一个占位路径（list 不读 content，不需要真实文件）
        conn.execute(
            """INSERT OR REPLACE INTO documents
               (doc_id, filename, format, ext, checksum, stored_path,
                size_bytes, title, status, created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_id,
                f"file_{i}.md",
                "markdown",
                ".md",
                f"sha256:{i:064x}",
                f"/tmp/fake_{i}.md",
                100 + i,
                f"Document {i}",
                "parsed",
                created,
                created,
                "{}",
            ),
        )
        ids.append(doc_id)
    conn.commit()
    return ids


# ═══════════════ cursor 编解码 ═══════════════


class TestCursorCodec:
    def test_encode_decode_roundtrip(self):
        """_encode_cursor + _decode_cursor 往返应保持原值"""
        from app.mcp.protocol import _decode_cursor, _encode_cursor

        for offset in (0, 1, 5, 10, 100, 9999):
            cursor = _encode_cursor(offset)
            assert isinstance(cursor, str)
            assert _decode_cursor(cursor) == offset, (
                f"offset={offset} 往返失败：cursor={cursor}"
            )

    def test_encode_cursor_uses_base64(self):
        """_encode_cursor 应使用 base64 编码（标准库）"""
        from app.mcp.protocol import _encode_cursor

        cursor = _encode_cursor(42)
        # 标准 base64 解码应得到字符串 "42"
        decoded = base64.b64decode(cursor).decode("utf-8")
        assert decoded == "42"

    def test_decode_cursor_none_returns_zero(self):
        """空 cursor（None）应回退到 0"""
        from app.mcp.protocol import _decode_cursor

        assert _decode_cursor(None) == 0

    def test_decode_cursor_empty_string_returns_zero(self):
        """空字符串 cursor 应回退到 0"""
        from app.mcp.protocol import _decode_cursor

        assert _decode_cursor("") == 0

    def test_decode_cursor_invalid_base64_returns_zero(self):
        """无效 base64 字符串应回退到 0（不抛异常）"""
        from app.mcp.protocol import _decode_cursor

        # 不是合法 base64
        assert _decode_cursor("!!!not-base64!!!") == 0

    def test_decode_cursor_non_numeric_returns_zero(self):
        """base64 解码后非数字应回退到 0"""
        from app.mcp.protocol import _decode_cursor

        # base64("hello") = "aGVsbG8="
        bad_cursor = base64.b64encode(b"hello").decode("ascii")
        assert _decode_cursor(bad_cursor) == 0

    def test_decode_cursor_negative_returns_zero(self):
        """负数 offset 应回退到 0"""
        from app.mcp.protocol import _decode_cursor

        neg_cursor = base64.b64encode(b"-5").decode("ascii")
        assert _decode_cursor(neg_cursor) == 0

    def test_encode_cursor_negative_clamps_to_zero(self):
        """_encode_cursor 负数应钳制到 0"""
        from app.mcp.protocol import _decode_cursor, _encode_cursor

        cursor = _encode_cursor(-100)
        assert _decode_cursor(cursor) == 0


# ═══════════════ list_incidents 分页 ═══════════════


class TestListIncidentsPagination:
    def test_first_page_returns_next_cursor(self, isolated_events_db):
        """首次请求无 cursor，数据量 > limit 时应返回 nextCursor"""
        from app.mcp.protocol import _tool_list_incidents

        _seed_incidents(15)  # 共 15 条
        result = json.loads(_tool_list_incidents({"limit": 5, "status": "all"}))

        assert result["count"] == 5
        assert len(result["incidents"]) == 5
        assert result["nextCursor"] is not None
        # nextCursor 应解码到 offset=5
        from app.mcp.protocol import _decode_cursor

        assert _decode_cursor(result["nextCursor"]) == 5

    def test_second_page_with_cursor(self, isolated_events_db):
        """带 cursor 请求下一页，应返回 offset+limit 起始的数据"""
        from app.mcp.protocol import _decode_cursor, _tool_list_incidents

        _seed_incidents(15)
        # 第一页
        page1 = json.loads(_tool_list_incidents({"limit": 5, "status": "all"}))
        # 第二页
        page2 = json.loads(
            _tool_list_incidents(
                {"limit": 5, "status": "all", "cursor": page1["nextCursor"]}
            )
        )

        assert page2["count"] == 5
        # 第二页 offset 应为 5
        assert _decode_cursor(page1["nextCursor"]) == 5
        # 两页数据不重叠
        page1_ids = {i["incident_id"] for i in page1["incidents"]}
        page2_ids = {i["incident_id"] for i in page2["incidents"]}
        assert page1_ids.isdisjoint(page2_ids)
        # 第二页还有下一页（共 15 条，5+5=10，剩 5 条）
        assert page2["nextCursor"] is not None

    def test_last_page_next_cursor_is_null(self, isolated_events_db):
        """最后一页 nextCursor 应为 null"""
        from app.mcp.protocol import _tool_list_incidents

        _seed_incidents(12)
        # 取 limit=5，第三页（offset=10）只剩 2 条 → nextCursor 应为 None
        from app.mcp.protocol import _encode_cursor

        result = json.loads(
            _tool_list_incidents(
                {"limit": 5, "status": "all", "cursor": _encode_cursor(10)}
            )
        )
        assert result["count"] == 2
        assert result["nextCursor"] is None

    def test_invalid_cursor_falls_back_to_offset_zero(self, isolated_events_db):
        """无效 cursor 应回退到 offset=0，等同于首次请求"""
        from app.mcp.protocol import _tool_list_incidents

        _seed_incidents(8)
        # 用一个无效 cursor
        result = json.loads(
            _tool_list_incidents(
                {"limit": 5, "status": "all", "cursor": "!!!invalid!!!"}
            )
        )
        # 应等同于从 0 开始
        first_page = json.loads(_tool_list_incidents({"limit": 5, "status": "all"}))
        assert result["count"] == first_page["count"] == 5
        # 第一条 incident_id 应相同
        assert result["incidents"][0]["incident_id"] == first_page["incidents"][0]["incident_id"]

    def test_no_cursor_no_more_data_returns_null(self, isolated_events_db):
        """数据量 <= limit 时，无 cursor 请求返回 nextCursor 为 null"""
        from app.mcp.protocol import _tool_list_incidents

        _seed_incidents(3)
        result = json.loads(_tool_list_incidents({"limit": 10, "status": "all"}))
        assert result["count"] == 3
        assert result["nextCursor"] is None

    def test_multi_page_traversal_three_pages(self, isolated_events_db):
        """limit=5, 共 12 条 → 3 页（5 + 5 + 2）"""
        from app.mcp.protocol import _tool_list_incidents

        _seed_incidents(12)
        all_ids: list[str] = []
        cursor = None
        page_counts: list[int] = []
        next_cursors: list[object] = []

        # 最多迭代 5 页防止死循环
        for _ in range(5):
            args = {"limit": 5, "status": "all"}
            if cursor:
                args["cursor"] = cursor
            page = json.loads(_tool_list_incidents(args))
            page_counts.append(page["count"])
            next_cursors.append(page["nextCursor"])
            all_ids.extend(i["incident_id"] for i in page["incidents"])
            cursor = page["nextCursor"]
            if cursor is None:
                break

        assert page_counts == [5, 5, 2], f"每页数量应为 5/5/2，实际: {page_counts}"
        # 前两页有 nextCursor，最后一页为 None
        assert next_cursors[0] is not None
        assert next_cursors[1] is not None
        assert next_cursors[2] is None
        # 12 条全部遍历到，无重复
        assert len(all_ids) == 12
        assert len(set(all_ids)) == 12


# ═══════════════ list_documents 分页 ═══════════════


class TestListDocumentsPagination:
    def test_first_page_returns_next_cursor(self, isolated_documents_db):
        """首次请求无 cursor，数据量 > limit 时应返回 nextCursor"""
        from app.mcp.protocol import _tool_list_documents

        _seed_documents(15)
        result = json.loads(_tool_list_documents({"limit": 5}))

        assert result["count"] == 5
        assert len(result["documents"]) == 5
        assert result["nextCursor"] is not None
        from app.mcp.protocol import _decode_cursor

        assert _decode_cursor(result["nextCursor"]) == 5

    def test_pagination_with_cursor(self, isolated_documents_db):
        """带 cursor 分页：3 页遍历 12 条"""
        from app.mcp.protocol import _tool_list_documents

        _seed_documents(12)
        all_ids: list[str] = []
        cursor = None
        page_counts: list[int] = []

        for _ in range(5):
            args = {"limit": 5}
            if cursor:
                args["cursor"] = cursor
            page = json.loads(_tool_list_documents(args))
            page_counts.append(page["count"])
            all_ids.extend(d["doc_id"] for d in page["documents"])
            cursor = page["nextCursor"]
            if cursor is None:
                break

        assert page_counts == [5, 5, 2]
        assert len(all_ids) == 12
        assert len(set(all_ids)) == 12

    def test_no_more_data_next_cursor_null(self, isolated_documents_db):
        """数据量 <= limit 时 nextCursor 为 null"""
        from app.mcp.protocol import _tool_list_documents

        _seed_documents(3)
        result = json.loads(_tool_list_documents({"limit": 10}))
        assert result["count"] == 3
        assert result["nextCursor"] is None

    def test_invalid_cursor_falls_back(self, isolated_documents_db):
        """无效 cursor 回退到 offset=0"""
        from app.mcp.protocol import _tool_list_documents

        _seed_documents(8)
        result = json.loads(
            _tool_list_documents({"limit": 5, "cursor": "!!!invalid!!!"})
        )
        first_page = json.loads(_tool_list_documents({"limit": 5}))
        assert result["count"] == first_page["count"] == 5
        assert result["documents"][0]["doc_id"] == first_page["documents"][0]["doc_id"]


# ═══════════════ schema 校验 ═══════════════


class TestListToolSchema:
    def test_list_incidents_schema_has_cursor(self):
        """list_incidents inputSchema 应含 cursor 字段"""
        from app.mcp.protocol import TOOLS

        tool = next(t for t in TOOLS if t["name"] == "list_incidents")
        props = tool["inputSchema"]["properties"]
        assert "cursor" in props
        assert props["cursor"]["type"] == "string"
        assert "cursor" in props["cursor"]["description"].lower()

    def test_list_documents_schema_has_cursor(self):
        """list_documents inputSchema 应含 cursor 字段"""
        from app.mcp.protocol import TOOLS

        tool = next(t for t in TOOLS if t["name"] == "list_documents")
        props = tool["inputSchema"]["properties"]
        assert "cursor" in props
        assert props["cursor"]["type"] == "string"
        assert "cursor" in props["cursor"]["description"].lower()

    def test_list_incidents_keeps_limit_field(self):
        """list_incidents 仍保留 limit 字段（向后兼容）"""
        from app.mcp.protocol import TOOLS

        tool = next(t for t in TOOLS if t["name"] == "list_incidents")
        assert "limit" in tool["inputSchema"]["properties"]

    def test_list_documents_keeps_limit_field(self):
        """list_documents 仍保留 limit 字段（向后兼容）"""
        from app.mcp.protocol import TOOLS

        tool = next(t for t in TOOLS if t["name"] == "list_documents")
        assert "limit" in tool["inputSchema"]["properties"]
