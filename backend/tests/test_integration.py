"""集成测试：解析 → 抽取 全链路验证（基于 MarkItDown + 自研组合方案）"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

# 软依赖：openai（LLM 后端）/ markitdown[docx,xlsx]（Office 解析）
# CI 最小依赖安装时这些可能缺失，对应测试应 skip 而非 error
# 注：markitdown[docx] 实际安装的是 mammoth，[xlsx] 安装的是 openpyxl
try:
    import openai  # noqa: F401
    _openai_available = True
except ImportError:
    _openai_available = False

try:
    import mammoth  # noqa: F401
    _docx_available = True
except ImportError:
    _docx_available = False

try:
    import openpyxl  # noqa: F401
    _openpyxl_available = True
except ImportError:
    _openpyxl_available = False

requires_openai = pytest.mark.skipif(not _openai_available, reason="openai SDK 未安装")
requires_docx = pytest.mark.skipif(not _docx_available, reason="mammoth 未安装（markitdown[docx]）")
requires_openpyxl = pytest.mark.skipif(not _openpyxl_available, reason="openpyxl 未安装（markitdown[xlsx]）")


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
    @requires_docx
    def test_word_parse(self):
        from app.parsers.registry import get_parser
        doc = get_parser("word").parse(f"{FIXTURES}/sample.docx", "d-1")
        assert doc.format == "word"
        assert doc.title is not None
        assert len(doc.elements) >= 5

    @requires_openpyxl
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
    @requires_openai
    def test_auto_accept(self):
        from app.extraction.extractor import KnowledgeExtractor
        from app.extraction.types import ExtractedEntity, ExtractionResult

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


# ═══════════════ W5 图谱存储（离线测试） ═══════════════

class TestKnowledgeCompiler:
    def test_deduplicate_exact(self):
        """测试精确去重：同名同类型实体合并"""
        from app.knowledge import GraphEntity, KnowledgeCompiler

        compiler = KnowledgeCompiler()
        entities = [
            GraphEntity("Service", "order-service", {"tier": "t1"}, confidence=0.95),
            GraphEntity("Service", "order-service", {"tier": "t1"}, confidence=0.88),
            GraphEntity("Host", "web-01", {"ip": "10.0.0.1"}, confidence=0.90),
        ]
        result = compiler.deduplicate(entities)
        assert result.duplicates_found >= 1, "应检测到重复"

    def test_deduplicate_type_diff(self):
        """不同实体类型不合并"""
        from app.knowledge import GraphEntity, KnowledgeCompiler

        compiler = KnowledgeCompiler()
        entities = [
            GraphEntity("Service", "nginx", {}, confidence=0.9),
            GraphEntity("Host", "nginx", {}, confidence=0.9),
        ]
        result = compiler.deduplicate(entities)
        assert result.merged_count == 0, "不同类型不应合并"

    def test_merge_properties(self):
        """合并时保留最高置信度，聚合属性"""
        from app.knowledge import GraphEntity, KnowledgeCompiler

        compiler = KnowledgeCompiler()
        entities = [
            GraphEntity("Host", "db-01", {"ip": "10.0.1.1"}, confidence=0.7),
            GraphEntity("Host", "db-01", {"role": "master"}, confidence=0.9),
        ]
        result = compiler.deduplicate(entities)
        # 合并后应同时有 ip 和 role
        merged = compiler._merge_group(entities)
        assert "ip" in merged.properties
        assert "role" in merged.properties
        assert merged.confidence == 0.9  # 保留高置信度
        # 验证去重结果
        assert result.duplicates_found >= 1

    def test_authority_scoring(self):
        """权威评分计算"""
        from app.knowledge import GraphEntity, KnowledgeCompiler

        compiler = KnowledgeCompiler()
        e = GraphEntity(
            "Procedure", "mysql-backup",
            {"source_type": "sop", "sources": ["doc1", "doc2", "doc3"]},
            confidence=0.9,
        )
        scored = compiler.score_authority([e])
        assert "authority_score" in scored[0].properties
        assert scored[0].properties["authority_score"] > 0.5

    def test_compile_pipeline(self):
        """完整编译流水线：3→2 去重（哈希去重处理精确重复）"""
        from app.knowledge import GraphEntity, KnowledgeCompiler

        compiler = KnowledgeCompiler()
        entities = [
            GraphEntity("Service", "svc-a", {}, confidence=0.95),
            GraphEntity("Service", "svc-a", {}, confidence=0.88),  # 精确重复
            GraphEntity("Host", "h-01", {}, confidence=0.90),
        ]
        result = compiler.compile(entities, [])
        assert result.input_entities == 3
        assert result.after_dedup == 2  # 3 - 1 重复 = 2
        assert result.duplicates_found >= 1


# ═══════════════ W7 文档生成 ═══════════════

class TestDocGenerationPipeline:
    @requires_openai
    def test_graph_built(self):
        """验证 LangGraph 状态图构建成功"""
        from app.knowledge import get_pipeline

        pipeline = get_pipeline()
        nodes = list(pipeline.graph.nodes.keys())
        assert "intent" in nodes
        assert "outline" in nodes
        assert "generate" in nodes
        assert "review" in nodes
        assert "modify" in nodes
        assert "proofread" in nodes

    @requires_openai
    def test_review_router(self):
        """路由决策：accept → proofread, reject → modify"""
        from app.knowledge import get_pipeline

        pipeline = get_pipeline()
        assert pipeline._review_router({"review_decision": "accept", "iteration": 0, "max_iterations": 3}) == "accept"
        assert pipeline._review_router({"review_decision": "reject", "iteration": 0, "max_iterations": 3}) == "reject"
        assert pipeline._review_router({"review_decision": "reject", "iteration": 3, "max_iterations": 3}) == "done"

    @requires_openai
    def test_format_document(self):
        """文档格式化"""
        from app.knowledge import get_pipeline

        pipeline = get_pipeline()
        sections = [
            {"title": "Section 1", "level": 1, "content": "Content 1"},
            {"title": "Sub 1.1", "level": 2, "content": "Content 1.1"},
        ]
        doc = pipeline._format_document(sections)
        assert "# Section 1" in doc
        assert "## Sub 1.1" in doc
        assert "Content 1" in doc
