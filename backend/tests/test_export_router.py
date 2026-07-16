"""Export Router API 测试

使用 FastAPI TestClient 覆盖导出路由端点。
"""
from __future__ import annotations

import os

os.environ.setdefault("OPSKG_API_TOKEN", "")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestExport:
    def test_export_markdown(self):
        r = client.post(
            "/export",
            json={
                "title": "Test Document",
                "content": "# Hello\n\nWorld",
                "format": "markdown",
            },
        )
        assert r.status_code == 200
        assert r.headers["content-type"] in (
            "text/markdown",
            "text/markdown; charset=utf-8",
        )
        assert "attachment" in r.headers["content-disposition"]

    def test_export_html(self):
        r = client.post(
            "/export",
            json={
                "title": "Test Document",
                "content": "# Hello\n\nWorld",
                "format": "html",
            },
        )
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_export_text(self):
        r = client.post(
            "/export",
            json={
                "title": "Test Document",
                "content": "# Hello\n\nWorld",
                "format": "text",
            },
        )
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]

    def test_export_default_format(self):
        r = client.post(
            "/export",
            json={"title": "Test", "content": "Hello"},
        )
        assert r.status_code == 200

    def test_export_invalid_format(self):
        r = client.post(
            "/export",
            json={
                "title": "Test",
                "content": "Hello",
                "format": "pdf",
            },
        )
        # PDF may raise ValueError (400) or RuntimeError (500) depending on deps
        assert r.status_code in (400, 500)

    def test_export_title_with_slash(self):
        r = client.post(
            "/export",
            json={
                "title": "test/file",
                "content": "Hello",
                "format": "text",
            },
        )
        assert r.status_code == 200
        # Title with slash should be sanitized
        assert "test_file" in r.headers["content-disposition"]


class TestExportEdgeCases:
    def test_export_empty_content(self):
        r = client.post(
            "/export",
            json={"title": "Empty", "content": "", "format": "text"},
        )
        assert r.status_code == 200

    def test_export_unicode_title(self):
        r = client.post(
            "/export",
            json={
                "title": "测试文档",
                "content": "Hello",
                "format": "text",
            },
        )
        assert r.status_code == 200
        assert "UTF-8" in r.headers["content-disposition"]