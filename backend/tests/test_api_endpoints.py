"""API 端点测试（P0-3）

使用 FastAPI TestClient 覆盖所有路由，确保端到端可用。
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

# 确保测试期间关闭认证
os.environ.setdefault("OPSKG_API_TOKEN", "")

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ────────── 健康检查 & 元数据 ──────────

class TestHealth:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_list_parsers(self):
        r = client.get("/parsers")
        assert r.status_code == 200
        fmts = r.json()["formats"]
        assert "markdown" in fmts
        assert "word" in fmts
        assert len(fmts) >= 20


# ────────── 文档管理 API ──────────

class TestDocumentAPI:
    def test_list_documents_empty(self):
        r = client.get("/documents")
        assert r.status_code == 200
        assert "documents" in r.json()
        assert "stats" in r.json()

    def test_document_stats(self):
        r = client.get("/documents/stats")
        assert r.status_code == 200
        assert "total" in r.json()

    def test_search_documents(self):
        r = client.get("/documents/search", params={"q": "test"})
        assert r.status_code == 200
        assert "results" in r.json()

    def test_get_nonexistent_document(self):
        r = client.get("/documents/nonexistent")
        assert r.status_code == 404

    def test_delete_nonexistent_document(self):
        r = client.delete("/documents/nonexistent")
        assert r.status_code == 404


# ────────── 解析 API ──────────

class TestParseAPI:
    def test_parse_markdown(self):
        content = b"# Test\n\nHello world\n"
        r = client.post(
            "/parsers/parse/markdown",
            files={"file": ("test.md", io.BytesIO(content), "text/markdown")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "Test"
        assert data["stored"] is True
        assert "doc_id" in data

    def test_parse_text(self):
        content = b"First paragraph.\n\nSecond paragraph.\n"
        r = client.post(
            "/parsers/parse/txt",
            files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        )
        assert r.status_code == 200
        assert len(r.json()["elements"]) >= 2

    def test_parse_unsupported_format(self):
        r = client.post(
            "/parsers/parse/unknown",
            files={"file": ("test.xyz", io.BytesIO(b"data"), "application/octet-stream")},
        )
        assert r.status_code == 400

    def test_parse_batch(self):
        md_content = b"# Doc 1\n\nContent"
        txt_content = b"Text content\n\nPara 2"
        r = client.post(
            "/parsers/parse/batch",
            files=[
                ("files", ("d1.md", io.BytesIO(md_content), "text/markdown")),
                ("files", ("d2.txt", io.BytesIO(txt_content), "text/plain")),
            ],
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 2


# ────────── 审查队列 API ──────────

class TestReviewAPI:
    def test_review_stats(self):
        r = client.get("/review/stats")
        assert r.status_code == 200

    def test_review_queue_list(self):
        r = client.get("/review/queue")
        assert r.status_code == 200
        assert "items" in r.json()

    def test_approve_nonexistent(self):
        r = client.post("/review/999999/approve")
        assert r.status_code == 404

    def test_reject_nonexistent(self):
        r = client.post("/review/999999/reject")
        assert r.status_code == 404


# ────────── 图谱 API（Neo4j 未连接时优雅降级） ──────────

class TestGraphAPI:
    def test_graph_stats(self):
        r = client.get("/graph/stats")
        assert r.status_code == 200
        # Neo4j 未连接时返回 error 字段
        data = r.json()
        assert "error" in data or "entities" in data

    def test_graph_visualize(self):
        r = client.get("/graph/visualize")
        assert r.status_code == 200
        data = r.json()
        assert "nodes" in data
        assert "links" in data

    def test_graph_search(self):
        r = client.get("/graph/search", params={"q": "test"})
        assert r.status_code == 200


# ────────── 认证 API ──────────

class TestAuth:
    def test_health_no_auth_required(self):
        """健康检查不需要认证"""
        r = client.get("/health")
        assert r.status_code == 200

    def test_get_documents_no_auth_required(self):
        """读操作不需要认证（Token 为空时）"""
        r = client.get("/documents")
        assert r.status_code == 200
