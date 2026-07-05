"""集成测试：解析 → 抽取 全链路验证（W3 + W4）"""
from __future__ import annotations

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.markdown_parser import MarkdownParser
from app.parsers.sql_parser import SQLParser
from app.parsers.text_parser import TextParser
from app.parsers.html_parser import HTMLParser
from app.parsers.registry import supported_formats, get_parser


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


# ═══════════════ W3：解析器测试 ═══════════════

class TestMarkdownParser:
    def test_parse_returns_document(self):
        p = MarkdownParser()
        doc = p.parse(f"{FIXTURES}/sample.md", "md-1")
        assert doc.format == "markdown"
        assert doc.title == "运维手册：MySQL 主从同步故障排查"
        assert len(doc.elements) == 10
        assert doc.checksum

    def test_elements_have_expected_types(self):
        p = MarkdownParser()
        doc = p.parse(f"{FIXTURES}/sample.md", "md-1")
        types = [e.type.value for e in doc.elements]
        assert "heading" in types
        assert "code" in types
        assert "table" in types
        assert "list" in types

    def test_code_block_extracted(self):
        p = MarkdownParser()
        doc = p.parse(f"{FIXTURES}/sample.md", "md-1")
        code_blocks = [e for e in doc.elements if e.type.value == "code"]
        assert any("SHOW SLAVE STATUS" in e.content for e in code_blocks)


class TestSQLParser:
    def test_parse_returns_document(self):
        p = SQLParser()
        doc = p.parse(f"{FIXTURES}/sample.sql", "sql-1")
        assert doc.format == "sql"
        assert len(doc.elements) == 5

    def test_ddl_detected(self):
        p = SQLParser()
        doc = p.parse(f"{FIXTURES}/sample.sql", "sql-1")
        ddl = [e for e in doc.elements if e.metadata["category"] == "ddl"]
        assert len(ddl) >= 2  # CREATE DATABASE, CREATE TABLE

    def test_dml_detected(self):
        p = SQLParser()
        doc = p.parse(f"{FIXTURES}/sample.sql", "sql-1")
        dml = [e for e in doc.elements if e.metadata["category"] == "dml"]
        assert len(dml) >= 2  # INSERT, SELECT

    def test_table_extraction(self):
        p = SQLParser()
        doc = p.parse(f"{FIXTURES}/sample.sql", "sql-1")
        server_stmts = [e for e in doc.elements if "servers" in e.metadata.get("tables", [])]
        assert len(server_stmts) >= 2


class TestTextParser:
    def test_parse_returns_document(self):
        p = TextParser()
        doc = p.parse(f"{FIXTURES}/sample.txt", "txt-1")
        assert doc.format == "txt"
        assert doc.title == "Nginx 配置优化指南"
        assert len(doc.elements) == 5


class TestHTMLParser:
    def test_parse_returns_document(self):
        p = HTMLParser()
        doc = p.parse(f"{FIXTURES}/sample.html", "html-1")
        assert doc.format == "html"
        assert doc.title == "故障报告"
        assert len(doc.elements) == 9

    def test_headings_extracted(self):
        p = HTMLParser()
        doc = p.parse(f"{FIXTURES}/sample.html", "html-1")
        headings = [e for e in doc.elements if e.type.value == "heading"]
        assert len(headings) >= 5

    def test_list_extracted(self):
        p = HTMLParser()
        doc = p.parse(f"{FIXTURES}/sample.html", "html-1")
        lists = [e for e in doc.elements if e.type.value == "list"]
        assert len(lists) >= 1


class TestParserRegistry:
    def test_all_formats_registered(self):
        formats = supported_formats()
        assert "markdown" in formats
        assert "sql" in formats
        assert "txt" in formats
        assert "html" in formats

    def test_get_parser_returns_parser(self):
        from app.parsers.base import DocumentParser
        for fmt in ["markdown", "sql", "txt", "html"]:
            parser = get_parser(fmt)
            assert isinstance(parser, DocumentParser)

    def test_unknown_format_raises(self):
        with pytest.raises(KeyError):
            get_parser("unknown")


# ═══════════════ W4：抽取测试 ═══════════════

class TestExtractionTypes:
    def test_entity_creation(self):
        from app.extraction.types import ExtractedEntity, EntityType
        e = ExtractedEntity(
            entity_type=EntityType.COMPONENT.value,
            name="MySQL",
            properties={"version": "8.0"},
            confidence=0.92,
            evidence_span="MySQL 8.0 主库",
            source_doc_id="doc-1",
        )
        assert e.entity_type == "Component"
        assert e.confidence == 0.92
        assert e.properties["version"] == "8.0"

    def test_gating_thresholds(self):
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
        assert len(result.review_entities) == 1
        assert result.discarded_count == 1
        assert result.auto_accepted_entities[0].name == "high"


class TestExtractionLLM:
    @pytest.mark.asyncio
    async def test_extract_without_llm(self):
        """测试无 LLM 时的抽取（应返回空结果）"""
        from app.parsers.base import ParsedDocument, ParsedElement, ElementType
        from app.extraction.extractor import KnowledgeExtractor

        doc = ParsedDocument(
            doc_id="test", source_path="test.md", format="markdown",
            checksum="abc", title="Test",
            elements=[ParsedElement(type=ElementType.PARAGRAPH, content="test content")],
        )
        extractor = KnowledgeExtractor()
        # 无 LLM 后端 -> 应返回空结果
        result = await extractor.extract(doc)
        assert result.doc_id == "test"
        assert len(result.entities) == 0  # LLM 调用失败时返回空列表


if __name__ == "__main__":
    pytest.main([__file__, "-v"])