"""Wiki 编译器类型与工具函数单元测试

覆盖 WikiPage 创建、WikiCompileResult 统计、ProgressEventType 枚举、
以及 slug 生成函数（make_slug, make_hierarchical_slug）。
"""
from __future__ import annotations

import pytest

# 直接导入子模块，避免触发 app.knowledge.__init__ 的 neo4j 依赖链
from app.knowledge.wiki_compiler_types import (
    ENTITY_TYPE_TO_PAGE_TYPE,
    ProgressEventType,
    PipelineTrace,
    SectionTrace,
    WikiCompileResult,
    WikiPage,
)
from app.knowledge.wiki_compiler_utils import (
    make_hierarchical_slug,
    make_slug,
    slugify,
)


# ────────── WikiPage 创建 ──────────


class TestWikiPage:
    def test_create_basic_page(self):
        """创建基本 WikiPage"""
        page = WikiPage(
            slug="nginx-502-troubleshooting",
            title="Nginx 502 故障排查",
            type="incident",
            tags=["nginx", "502", "gateway"],
            sources=[{"doc_id": "abc123", "title": "Nginx Guide", "checksum": "sha256:abc"}],
            body_md="## 概述\n\n502 错误排查指南。",
        )
        assert page.slug == "nginx-502-troubleshooting"
        assert page.title == "Nginx 502 故障排查"
        assert page.type == "incident"
        assert page.tags == ["nginx", "502", "gateway"]
        assert len(page.sources) == 1
        assert page.sources[0]["doc_id"] == "abc123"
        assert page.body_md == "## 概述\n\n502 错误排查指南。"
        # 默认值
        assert page.review_status == "auto"
        assert page.source_doc_id == ""
        assert page.stale_items == []
        assert page.paragraph_labels == []

    def test_create_page_with_review_status(self):
        """创建带审查状态的页面"""
        page = WikiPage(
            slug="test-page",
            title="Test",
            type="concept",
            tags=[],
            sources=[],
            body_md="content",
            review_status="review_needed",
        )
        assert page.review_status == "review_needed"

    def test_create_page_with_stale_items(self):
        """创建带 stale 标记的页面"""
        page = WikiPage(
            slug="test-page",
            title="Test",
            type="concept",
            tags=[],
            sources=[],
            body_md="content",
            stale_items=["端口号已从 80 变更为 8080"],
        )
        assert page.stale_items == ["端口号已从 80 变更为 8080"]

    def test_create_page_with_paragraph_labels(self):
        """创建带段落分类标签的页面"""
        page = WikiPage(
            slug="test-page",
            title="Test",
            type="concept",
            tags=[],
            sources=[],
            body_md="content",
            paragraph_labels=["配置", "故障排查", "性能"],
        )
        assert page.paragraph_labels == ["配置", "故障排查", "性能"]

    def test_entity_type_to_page_type_mapping(self):
        """验证 ENTITY_TYPE_TO_PAGE_TYPE 映射的完整性"""
        assert ENTITY_TYPE_TO_PAGE_TYPE["Host"] == "host"
        assert ENTITY_TYPE_TO_PAGE_TYPE["Service"] == "service"
        assert ENTITY_TYPE_TO_PAGE_TYPE["Component"] == "entity"
        assert ENTITY_TYPE_TO_PAGE_TYPE["Incident"] == "incident"
        assert ENTITY_TYPE_TO_PAGE_TYPE["Procedure"] == "runbook"
        assert ENTITY_TYPE_TO_PAGE_TYPE["Concept"] == "concept"


# ────────── WikiCompileResult 统计 ──────────


class TestWikiCompileResult:
    def test_create_empty_result(self):
        """创建空的编译结果"""
        result = WikiCompileResult(doc_id="doc-001")
        assert result.doc_id == "doc-001"
        assert result.pages_created == 0
        assert result.pages_updated == 0
        assert result.pages_unchanged == 0
        assert result.slugs == []
        assert result.review_needed == []
        assert result.stale_marked == []
        assert result.errors == []
        assert result.index_rebuilt is False
        assert result.graph_compiled is False
        assert result.paragraph_count == 0
        assert result.pipeline_trace is None

    def test_create_result_with_pages(self):
        """创建带页面统计的编译结果"""
        result = WikiCompileResult(
            doc_id="doc-001",
            pages_created=3,
            pages_updated=1,
            pages_unchanged=2,
            slugs=["page-a", "page-b", "page-c", "page-d", "page-e", "page-f"],
            review_needed=["page-a"],
            stale_marked=["page-b"],
            errors=["Failed to compile page-d"],
            index_rebuilt=True,
            graph_compiled=True,
            paragraph_count=15,
        )
        assert result.pages_created == 3
        assert result.pages_updated == 1
        assert result.pages_unchanged == 2
        assert len(result.slugs) == 6
        assert result.review_needed == ["page-a"]
        assert result.stale_marked == ["page-b"]
        assert result.errors == ["Failed to compile page-d"]
        assert result.index_rebuilt is True
        assert result.graph_compiled is True
        assert result.paragraph_count == 15

    def test_total_pages(self):
        """验证总页面数 = created + updated + unchanged"""
        result = WikiCompileResult(
            doc_id="doc-001",
            pages_created=5,
            pages_updated=3,
            pages_unchanged=7,
        )
        total = result.pages_created + result.pages_updated + result.pages_unchanged
        assert total == 15


# ────────── ProgressEventType 枚举 ──────────


class TestProgressEventType:
    def test_enum_values(self):
        """验证所有枚举值"""
        assert ProgressEventType.STEP_START == "step_start"
        assert ProgressEventType.STEP_DONE == "step_done"
        assert ProgressEventType.PAGE_START == "page_start"
        assert ProgressEventType.PAGE_DONE == "page_done"
        assert ProgressEventType.QUALITY_CHECK == "quality_check"
        assert ProgressEventType.CONFLICT_DETECTED == "conflict_detected"
        assert ProgressEventType.PROGRESS == "progress"
        assert ProgressEventType.SECTION_PROGRESS == "section_progress"
        assert ProgressEventType.SECTION_START == "section_start"
        assert ProgressEventType.SECTION_DONE == "section_done"

    def test_enum_is_string(self):
        """ProgressEventType 是字符串枚举"""
        assert isinstance(ProgressEventType.STEP_START, str)
        assert ProgressEventType.STEP_START == "step_start"

    def test_enum_from_string(self):
        """从字符串构造枚举"""
        assert ProgressEventType("step_start") == ProgressEventType.STEP_START
        assert ProgressEventType("page_done") == ProgressEventType.PAGE_DONE
        assert ProgressEventType("progress") == ProgressEventType.PROGRESS

    def test_enum_membership(self):
        """验证成员数量"""
        members = list(ProgressEventType)
        assert len(members) == 10


# ────────── PipelineTrace / SectionTrace ──────────


class TestPipelineTrace:
    def test_create_section_trace(self):
        """创建 SectionTrace"""
        trace = SectionTrace(
            title="概述",
            level=2,
            slug="nginx-guide-overview",
            raw_content="原始内容",
            raw_chars=4,
            compiled_content="编译后内容",
            compiled_chars=5,
            llm_success=True,
            processing_time_ms=123.45,
            children_count=2,
        )
        assert trace.title == "概述"
        assert trace.level == 2
        assert trace.slug == "nginx-guide-overview"
        assert trace.raw_chars == 4
        assert trace.compiled_chars == 5
        assert trace.llm_success is True
        assert trace.processing_time_ms == 123.45
        assert trace.children_count == 2

    def test_create_pipeline_trace(self):
        """创建 PipelineTrace"""
        sections = [
            SectionTrace(
                title="概述", level=2, slug="overview",
                raw_content="raw", raw_chars=3,
                compiled_content="compiled", compiled_chars=8,
                llm_success=True, processing_time_ms=100, children_count=1,
            ),
            SectionTrace(
                title="配置", level=2, slug="config",
                raw_content="raw2", raw_chars=4,
                compiled_content="compiled2", compiled_chars=9,
                llm_success=False, processing_time_ms=200, children_count=0,
            ),
        ]
        trace = PipelineTrace(
            doc_id="doc-001",
            doc_title="Nginx Guide",
            duration_ms=300.0,
            sections=sections,
            total_raw_chars=7,
            total_compiled_chars=17,
            total_sections=2,
            sections_with_children=1,
            llm_success_count=1,
            llm_fail_count=1,
        )
        assert trace.doc_id == "doc-001"
        assert trace.doc_title == "Nginx Guide"
        assert trace.duration_ms == 300.0
        assert len(trace.sections) == 2
        assert trace.total_raw_chars == 7
        assert trace.total_compiled_chars == 17
        assert trace.total_sections == 2
        assert trace.sections_with_children == 1
        assert trace.llm_success_count == 1
        assert trace.llm_fail_count == 1


# ────────── slug 生成函数 ──────────


class TestSlugify:
    def test_slugify_basic(self):
        """基本 slugify"""
        assert slugify("Nginx 502 Troubleshooting") == "nginx-502-troubleshooting"

    def test_slugify_with_underscores(self):
        """下划线转连字符"""
        assert slugify("nginx_config_file") == "nginx-config-file"

    def test_slugify_special_chars(self):
        """特殊字符被移除"""
        assert slugify("Hello, World!") == "hello-world"

    def test_slugify_chinese(self):
        """纯中文被 strip 后返回 unnamed（_SLUG_SAFE_RE 仅保留 ASCII）"""
        result = slugify("故障排查")
        assert result == "unnamed"

    def test_slugify_empty(self):
        """空字符串返回 unnamed"""
        assert slugify("") == "unnamed"
        assert slugify("   ") == "unnamed"


class TestMakeSlug:
    def test_make_slug_host(self):
        """Host 类型 → host-{name}"""
        slug = make_slug("Host", "web-prod-01")
        assert slug == "host-web-prod-01"

    def test_make_slug_service(self):
        """Service 类型 → service-{name}"""
        slug = make_slug("Service", "nginx")
        assert slug == "service-nginx"

    def test_make_slug_incident(self):
        """Incident 类型 → {name}-troubleshooting"""
        slug = make_slug("Incident", "502 Bad Gateway")
        assert slug.endswith("-troubleshooting")
        assert "502" in slug

    def test_make_slug_incident_already_has_troubleshoot(self):
        """Incident 类型但名称已含 troubleshooting → 不重复追加"""
        slug = make_slug("Incident", "nginx-502-troubleshooting")
        assert slug == "nginx-502-troubleshooting"

    def test_make_slug_incident_chinese(self):
        """Incident 类型中文名称含 '故障' → 不追加 troubleshooting"""
        slug = make_slug("Incident", "Nginx 故障排查")
        # 名称含 "故障" 不追加
        assert "troubleshoot" not in slug

    def test_make_slug_runbook(self):
        """Procedure 类型 → runbook-{name}"""
        slug = make_slug("Procedure", "nginx-restart")
        assert slug == "runbook-nginx-restart"

    def test_make_slug_concept(self):
        """Concept 类型 → 直接用概念名"""
        slug = make_slug("Concept", "Reverse Proxy")
        assert slug == "reverse-proxy"

    def test_make_slug_entity(self):
        """Component 类型 → 直接用名称"""
        slug = make_slug("Component", "Database Connection Pool")
        assert slug == "database-connection-pool"

    def test_make_slug_unknown_type(self):
        """未知类型回退到 concept"""
        slug = make_slug("UnknownType", "Some Name")
        assert slug == "some-name"


class TestMakeHierarchicalSlug:
    def test_h1_slug(self):
        """H1 标题：直接 slugify"""
        slug = make_hierarchical_slug("Nginx Deployment Guide", level=1)
        assert slug == "nginx-deployment-guide"

    def test_h2_slug_with_parent(self):
        """H2 标题：parent 取 level-1=1 个部分 + section-slug"""
        slug = make_hierarchical_slug(
            "Configuration", level=2, parent_slug="nginx-deployment-guide"
        )
        # parent 被截断为 level-1=1 部分 → "nginx"
        assert slug == "nginx-configuration"

    def test_h3_slug_with_parent(self):
        """H3 标题：parent 取 level-1=2 个部分 + section-slug"""
        slug = make_hierarchical_slug(
            "SSL Settings", level=3, parent_slug="nginx-deployment-guide-configuration"
        )
        # parent 被截断为 level-1=2 部分 → "nginx-deployment"
        assert slug == "nginx-deployment-ssl-settings"

    def test_hierarchical_slug_with_entity_type_host(self):
        """带 entity_type Host → host- 前缀"""
        slug = make_hierarchical_slug(
            "web-prod-01", level=1, entity_type="Host"
        )
        assert slug == "host-web-prod-01"

    def test_hierarchical_slug_with_entity_type_service(self):
        """带 entity_type Service → service- 前缀"""
        slug = make_hierarchical_slug(
            "nginx", level=1, entity_type="Service"
        )
        assert slug == "service-nginx"

    def test_hierarchical_slug_with_entity_type_runbook(self):
        """带 entity_type Procedure → runbook- 前缀"""
        slug = make_hierarchical_slug(
            "nginx-restart", level=1, entity_type="Procedure"
        )
        assert slug == "runbook-nginx-restart"

    def test_hierarchical_slug_with_entity_type_incident(self):
        """带 entity_type Incident → 追加 troubleshooting"""
        slug = make_hierarchical_slug(
            "502 Bad Gateway", level=1, entity_type="Incident"
        )
        assert slug.endswith("-troubleshooting")

    def test_hierarchical_slug_max_length(self):
        """超长 slug 被截断"""
        long_title = "A" * 200
        slug = make_hierarchical_slug(long_title, level=1, max_length=100)
        assert len(slug) <= 100

    def test_hierarchical_slug_empty_title(self):
        """空标题使用 unnamed 兜底（slugify 空串返回 unnamed）"""
        slug = make_hierarchical_slug("", level=2, parent_slug="parent")
        # slugify("") → "unnamed", 加上 parent → "parent-unnamed"
        assert "unnamed" in slug

    def test_hierarchical_slug_parent_depth_truncation(self):
        """父 slug 层级过深时截断"""
        parent = "a-b-c-d-e-f-g-h"
        slug = make_hierarchical_slug("New Section", level=2, parent_slug=parent)
        # 父 slug 只取 level-1 个部分
        parts = slug.split("-")
        # parent 部分被截断为 level-1 = 1 个部分
        assert parts[0] == "a"
        assert "new-section" in slug

    def test_hierarchical_slug_entity_and_parent(self):
        """同时有 entity_type 和 parent_slug"""
        slug = make_hierarchical_slug(
            "Config", level=2, parent_slug="host-web-prod-01", entity_type="Host"
        )
        assert slug.startswith("host-")
        # parent 被截断为 level-1=1 部分 → "host"
        assert "config" in slug