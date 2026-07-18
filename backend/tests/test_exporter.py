"""Exporter 单元测试

覆盖 Exporter.to_markdown / to_html / to_text / export / _markdown_to_html / _render_table
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.export.exporter import Exporter, get_exporter


@pytest.fixture
def exporter():
    return Exporter()


class TestToMarkdown:
    def test_basic(self, exporter):
        result = exporter.to_markdown("Test", "Hello World")
        assert isinstance(result, bytes)
        text = result.decode("utf-8")
        assert text.startswith("# Test\n\n")
        assert "Hello World" in text

    def test_empty_content(self, exporter):
        result = exporter.to_markdown("Title", "")
        assert result.decode("utf-8") == "# Title\n\n"


class TestToHtml:
    def test_basic(self, exporter):
        result = exporter.to_html("Test", "Hello World")
        assert isinstance(result, bytes)
        text = result.decode("utf-8")
        assert "<!DOCTYPE html>" in text
        assert "<title>Test</title>" in text
        assert "<h1>Test</h1>" in text
        assert "Hello World" in text

    def test_without_css(self, exporter):
        result = exporter.to_html("Test", "Hello", include_css=False)
        text = result.decode("utf-8")
        assert "<style>" not in text
        assert "<!DOCTYPE html>" in text

    def test_escapes_title(self, exporter):
        result = exporter.to_html("Test <script>", "Body")
        text = result.decode("utf-8")
        assert "&lt;script&gt;" in text
        assert "<script>" not in text


class TestToText:
    def test_basic(self, exporter):
        result = exporter.to_text("Test", "Hello World")
        assert isinstance(result, bytes)
        text = result.decode("utf-8")
        assert "Test\n====\n" in text
        assert "Hello World" in text

    def test_strips_markdown(self, exporter):
        result = exporter.to_text("Title", "# Heading\n\n**bold** text `code` [link](url)")
        text = result.decode("utf-8")
        assert "Heading" in text
        assert "bold" in text
        assert "code" in text
        assert "link" in text
        assert "#" not in text.split("Title")[1]  # heading marker removed

    def test_blockquote_stripped(self, exporter):
        result = exporter.to_text("Title", "> quoted text")
        text = result.decode("utf-8")
        assert "quoted text" in text


class TestExport:
    def test_export_markdown(self, exporter):
        content, media_type, ext = exporter.export("T", "C", "markdown")
        assert isinstance(content, bytes)
        assert media_type == "text/markdown"
        assert ext == ".md"

    def test_export_md_alias(self, exporter):
        content, media_type, ext = exporter.export("T", "C", "md")
        assert media_type == "text/markdown"
        assert ext == ".md"

    def test_export_html(self, exporter):
        content, media_type, ext = exporter.export("T", "C", "html")
        assert media_type == "text/html"
        assert ext == ".html"

    def test_export_text(self, exporter):
        content, media_type, ext = exporter.export("T", "C", "text")
        assert media_type == "text/plain"
        assert ext == ".txt"

    def test_export_txt_alias(self, exporter):
        content, media_type, ext = exporter.export("T", "C", "txt")
        assert media_type == "text/plain"
        assert ext == ".txt"

    def test_export_invalid_format(self, exporter):
        with pytest.raises(ValueError, match="不支持的导出格式"):
            exporter.export("T", "C", "invalid")


class TestMarkdownToHtml:
    def test_headings(self, exporter):
        html = exporter._markdown_to_html("# H1\n## H2\n### H3")
        assert "<h2>H1</h2>" in html
        assert "<h2>H2</h2>" in html
        assert "<h3>H3</h3>" in html

    def test_bold_and_code(self, exporter):
        html = exporter._markdown_to_html("**bold** and `code`")
        assert "<strong>bold</strong>" in html
        assert "<code>code</code>" in html

    def test_links(self, exporter):
        html = exporter._markdown_to_html("[text](http://example.com)")
        assert '<a href="http://example.com">text</a>' in html

    def test_unordered_list(self, exporter):
        html = exporter._markdown_to_html("- item 1\n- item 2")
        assert "<ul>" in html
        assert "<li>item 1</li>" in html
        assert "<li>item 2</li>" in html
        assert "</ul>" in html

    def test_blockquote(self, exporter):
        html = exporter._markdown_to_html("> quoted")
        assert "<blockquote>" in html
        assert "quoted" in html

    def test_code_block(self, exporter):
        html = exporter._markdown_to_html("```python\nprint('hi')\n```")
        assert "<pre>" in html
        assert "<code" in html
        assert "print" in html

    def test_table(self, exporter):
        html = exporter._markdown_to_html(
            "| Col1 | Col2 |\n|------|------|\n| A    | B    |"
        )
        assert "<table>" in html
        assert "<th>Col1</th>" in html
        assert "<td>A</td>" in html
        assert "<td>B</td>" in html

    def test_empty(self, exporter):
        html = exporter._markdown_to_html("")
        assert html == ""


class TestRenderTable:
    def test_simple_table(self, exporter):
        rows = ["| Col1 | Col2 |", "|------|------|", "| A | B |"]
        html = exporter._render_table(rows)
        assert "<table>" in html
        assert "<th>Col1</th>" in html
        assert "<td>A</td>" in html
        assert "<td>B</td>" in html
        # Separator row should be skipped
        assert "------" not in html


class TestGetExporter:
    def test_singleton(self):
        e1 = get_exporter()
        e2 = get_exporter()
        assert e1 is e2
        assert isinstance(e1, Exporter)
