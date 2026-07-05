"""集成测试：解析 → 抽取 全链路验证（基于 MarkItDown + 自研组合方案）"""
from __future__ import annotations

import pytest, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


# ═══════════════ 注册中心测试 ═══════════════

class TestParserRegistry:
    def test_core_formats_registered(self):
        from app.parsers.registry import supported_formats
        formats = supported_formats()
        assert "markdown" in formats
        assert "sql" in formats
        assert "txt" in formats

    def test_markitdown_formats_registered(self):
        from app.parsers.registry import supported_formats
        formats = supported_formats()
        for fmt in ["word", "docx", "excel", "xlsx", "html", "htm", "pdf", "csv"]:
            assert fmt in formats, f"缺少格式: {fmt}"

    def test_28_formats_total(self):
        from app.parsers.registry import supported_formats
        assert len(supported_formats()) >= 25


# ═══════════════ 自研解析器 ═══════════════

class TestMarkdownParser:
    def test_parse_structure(self):
        from app.parsers.markdown_parser import MarkdownParser
        doc = MarkdownParser().parse(f"{FIXTURES}/sample.md", "md-1")
        assert len(doc.elements) == 10
        types = {e.type.value for e in doc.elements}
        assert "heading" in types
        assert "code" in types
        assert "table" in types
        assert "list" in types

    def test_title_detection(self):
        from app.parsers.markdown_parser import MarkdownParser
        doc = MarkdownParser().parse(f"{FIXTURES}/sample.md", "md-1")
        assert "MySQL" in doc.title


class TestSQLParser:
    def test_ddl_dml_classification(self):
        from app.parsers.sql_parser import SQLParser
        doc = SQLParser().parse(f"{FIXTURES}/sample.sql", "sql-1")
        cats = [e.metadata["category"] for e in doc.elements]
        assert "ddl" in cats
        assert "dml" in cats

    def test_table_extraction(self):
        from app.parsers.sql_parser import SQLParser
        doc = SQLParser().parse(f"{FIXTURES}/sample.sql", "sql-1")
        server_stmts = [e for e in doc.elements if "servers" in e.metadata.get("tables", [])]
        assert len(server_stmts) >= 2


class TestTextParser:
    def test_parse(self):
        from app.parsers.text_parser import TextParser
        doc = TextParser().parse(f"{FIXTURES}/sample.txt", "txt-1")
        assert doc.title == "Nginx 配置优化指南"
        assert len(doc.elements) == 5


# ═══════════════ MarkItDown 适配器 ═══════════════

class TestMarkItDownAdapter:
    def test_word_parse(self):
        from app.parsers.registry import get_parser
        doc = get_parser("word").parse(f"{FIXTURES}/sample.docx", "d-1")
        assert doc.format == "word"
        assert doc.title is not None
        assert len(doc.elements) >= 5

    def test_excel_parse(self):
        from app.parsers.registry import get_parser
        doc = get_parser("excel").parse(f"{FIXTURES}/sample.xlsx", "e-1")
        assert doc.format == "excel"
        tables = [e for e in doc.elements if e.type.value == "table"]
        assert len(tables) >= 2

    def test_html_parse(self):
        from app.parsers.registry import get_parser
        doc = get_parser("html").parse(f"{FIXTURES}/sample.html", "h-1")
        assert "Redis" in doc.title
        assert len(doc.elements) >= 7

    def test_markdown_passthrough(self):
        from app.parsers.registry import get_parser
        doc = get_parser("markdown").parse(f"{FIXTURES}/sample.md", "md-1")
        assert doc.format == "markdown"


# ═══════════════ 抽取门控 ═══════════════

class TestExtractionGating:
    def test_auto_accept(self):
        from app.extraction.types import ExtractedEntity, ExtractionResult
        from app.extraction.extractor import KnowledgeExtractor

        settings = type("S", (), {"confidence_auto": 0.85, "confidence_review": 0.60})()
        extractor = KnowledgeExtractor()
        extractor.settings = settings

        entities = [
            ExtractedEntity(entity_type="Host", name="high", confidence=0.90),
            ExtractedEntity(entity_type="Host", name="mid", confidence=0.70),
            ExtractedEntity(entity_type="Host", name="low", confidence=0.40),
        ]
        result = ExtractionResult(doc_id="test")
        extractor._apply_gating(entities, [], result)

        assert len(result.auto_accepted_entities) == 1
        assert result.auto_accepted_entities[0].name == "high"
        assert len(result.review_entities) == 1
        assert result.review_entities[0].name == "mid"
        assert result.discarded_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])