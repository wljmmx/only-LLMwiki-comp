"""OKF Adapter 纯函数单元测试

覆盖：
- type_dir_for() / slug_from_concept_id()
- build_okf_link() / wikilink_to_okf() / okf_link_to_wikilink()
- _split_frontmatter() / _assemble_frontmatter()
- extract_description() / derive_resource() / normalize_frontmatter_for_okf()
- 数据模型：OKFConcept / ExportResult / ImportResult
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.knowledge.okf_adapter import (
    FALLBACK_DIR,
    OKF_VERSION,
    OKFConcept,
    ExportResult,
    ImportResult,
    _assemble_frontmatter,
    _split_frontmatter,
    build_okf_link,
    derive_resource,
    extract_description,
    normalize_frontmatter_for_okf,
    okf_link_to_wikilink,
    slug_from_concept_id,
    type_dir_for,
    wikilink_to_okf,
)

# ═══════════════ type_dir_for / slug_from_concept_id ═══════════════


class TestTypeDirFor:
    def test_known_types(self):
        assert type_dir_for("incident") == "incidents"
        assert type_dir_for("runbook") == "runbooks"
        assert type_dir_for("service") == "services"
        assert type_dir_for("host") == "hosts"
        assert type_dir_for("concept") == "concepts"
        assert type_dir_for("entity") == "entities"

    def test_unknown_type_falls_back(self):
        assert type_dir_for("bogus") == FALLBACK_DIR
        assert type_dir_for("") == FALLBACK_DIR


class TestSlugFromConceptId:
    def test_simple_path(self):
        assert slug_from_concept_id("incidents/nginx-502") == "nginx-502"

    def test_nested_path(self):
        assert slug_from_concept_id("a/b/c/slug") == "slug"

    def test_no_slash(self):
        assert slug_from_concept_id("plain-slug") == "plain-slug"


# ═══════════════ build_okf_link ═══════════════


class TestBuildOkfLink:
    def test_known_slug(self):
        slug_to_type = {"nginx-502": "incident"}
        link = build_okf_link("nginx-502", "Nginx 502", slug_to_type)
        assert link == "[Nginx 502](/incidents/nginx-502.md)"

    def test_unknown_slug_returns_display_only(self):
        slug_to_type = {}
        result = build_okf_link("unknown", "Unknown Page", slug_to_type)
        assert result == "Unknown Page"

    def test_display_equals_slug(self):
        slug_to_type = {"my-service": "service"}
        link = build_okf_link("my-service", "my-service", slug_to_type)
        assert link == "[my-service](/services/my-service.md)"


# ═══════════════ wikilink_to_okf ═══════════════


class TestWikilinkToOkf:
    def test_single_link(self):
        slug_to_type = {"nginx-502": "incident"}
        result = wikilink_to_okf("See [[nginx-502]] for details", slug_to_type)
        assert result == "See [nginx-502](/incidents/nginx-502.md) for details"

    def test_link_with_display_text(self):
        slug_to_type = {"nginx-502": "incident"}
        result = wikilink_to_okf("See [[nginx-502|Nginx 502 Error]]", slug_to_type)
        assert result == "See [Nginx 502 Error](/incidents/nginx-502.md)"

    def test_multiple_links(self):
        slug_to_type = {"a": "incident", "b": "concept"}
        result = wikilink_to_okf("[[a]] and [[b]]", slug_to_type)
        assert "[a](/incidents/a.md)" in result
        assert "[b](/concepts/b.md)" in result

    def test_broken_link_keeps_display(self):
        slug_to_type = {}
        result = wikilink_to_okf("See [[missing]]", slug_to_type)
        assert result == "See missing"

    def test_broken_link_with_display_text(self):
        slug_to_type = {}
        result = wikilink_to_okf("See [[missing|Missing Page]]", slug_to_type)
        assert result == "See Missing Page"

    def test_no_wikilinks(self):
        slug_to_type = {"a": "concept"}
        result = wikilink_to_okf("Plain text without links", slug_to_type)
        assert result == "Plain text without links"

    def test_mixed_known_and_unknown(self):
        slug_to_type = {"known": "concept"}
        result = wikilink_to_okf("[[known]] and [[unknown]]", slug_to_type)
        assert "[known](/concepts/known.md)" in result
        assert "unknown" in result


# ═══════════════ okf_link_to_wikilink ═══════════════


class TestOkfLinkToWikilink:
    def test_simple_link(self):
        result = okf_link_to_wikilink("[nginx-502](/incidents/nginx-502.md)")
        assert result == "[[nginx-502]]"

    def test_link_with_display(self):
        result = okf_link_to_wikilink("[Nginx 502](/incidents/nginx-502.md)")
        assert result == "[[nginx-502|Nginx 502]]"

    def test_multiple_links(self):
        result = okf_link_to_wikilink(
            "See [a](/concepts/a.md) and [b](/entities/b.md)"
        )
        assert "[[a]]" in result
        assert "[[b]]" in result

    def test_non_okf_links_unchanged(self):
        result = okf_link_to_wikilink(
            "See [Google](https://google.com) and [file](doc.pdf)"
        )
        assert "[Google](https://google.com)" in result
        assert "[file](doc.pdf)" in result

    def test_no_links(self):
        result = okf_link_to_wikilink("Plain text")
        assert result == "Plain text"


# ═══════════════ _split_frontmatter / _assemble_frontmatter ═══════════════


class TestSplitFrontmatter:
    def test_valid_frontmatter(self):
        content = "---\ntype: incident\ntitle: Test\n---\n\n# Body\nContent here"
        meta, body = _split_frontmatter(content)
        assert meta == {"type": "incident", "title": "Test"}
        assert body == "# Body\nContent here"

    def test_no_frontmatter(self):
        content = "# Just a heading\nPlain text"
        meta, body = _split_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_empty_frontmatter(self):
        content = "---\n---\n\nBody"
        meta, body = _split_frontmatter(content)
        assert meta == {}
        assert body == "Body"

    def test_incomplete_delimiter(self):
        content = "---\ntype: test\n"
        meta, body = _split_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_invalid_yaml(self):
        content = "---\n:bad: yaml: here\n---\n\nBody"
        meta, body = _split_frontmatter(content)
        assert meta == {}
        assert body == "Body"


class TestAssembleFrontmatter:
    def test_basic_assembly(self):
        meta = {"type": "incident", "title": "Test"}
        body = "# Body\nContent"
        result = _assemble_frontmatter(meta, body)
        assert result.startswith("---\n")
        assert "type: incident" in result
        assert "title: Test" in result
        assert result.endswith("\n")

    def test_filters_none_values(self):
        meta = {"type": "concept", "title": None, "description": "desc"}
        body = "Body"
        result = _assemble_frontmatter(meta, body)
        assert "title:" not in result
        assert "description: desc" in result


# ═══════════════ extract_description ═══════════════


class TestExtractDescription:
    def test_from_overview_section(self):
        body = "# Title\n\n## 概述\n\nThis is a description of the incident.\n\nMore text."
        desc = extract_description(body)
        assert "This is a description of the incident" in desc

    def test_from_first_paragraph(self):
        body = "# Title\n\nFirst paragraph text here.\n\n## Other Section\nMore text."
        desc = extract_description(body)
        assert "First paragraph text here" in desc

    def test_truncation(self):
        body = "# Title\n\n" + "x" * 200
        desc = extract_description(body, max_len=50)
        assert len(desc) <= 53  # 50 + "..."
        assert desc.endswith("...")

    def test_empty_body(self):
        desc = extract_description("")
        assert desc == ""

    def test_only_heading(self):
        body = "# Title\n"
        desc = extract_description(body)
        assert desc == ""


# ═══════════════ derive_resource ═══════════════


class TestDeriveResource:
    def test_from_sources(self):
        meta = {"slug": "test", "sources": [{"doc_id": "abc123"}]}
        assert derive_resource(meta) == "opskg://doc/abc123"

    def test_from_properties_ip(self):
        meta = {"slug": "test", "properties": {"ip": "10.0.0.1"}}
        assert derive_resource(meta) == "host://10.0.0.1"

    def test_from_properties_hostname(self):
        meta = {"slug": "test", "properties": {"hostname": "web-01"}}
        assert derive_resource(meta) == "host://web-01"

    def test_from_properties_service_id(self):
        meta = {"slug": "test", "properties": {"service_id": "svc-123"}}
        assert derive_resource(meta) == "service://svc-123"

    def test_fallback_to_slug(self):
        meta = {"slug": "my-page"}
        assert derive_resource(meta) == "opskg://wiki/my-page"

    def test_empty_slug_fallback(self):
        meta = {"slug": ""}
        assert derive_resource(meta) == ""

    def test_sources_empty_list(self):
        meta = {"slug": "test", "sources": []}
        assert derive_resource(meta) == "opskg://wiki/test"


# ═══════════════ normalize_frontmatter_for_okf ═══════════════


class TestNormalizeFrontmatterForOkf:
    def test_complete_meta(self):
        meta = {
            "type": "incident",
            "title": "Nginx 502",
            "description": "Troubleshooting 502",
            "resource": "opskg://doc/abc",
            "timestamp": "2024-01-01T00:00:00Z",
        }
        result = normalize_frontmatter_for_okf(meta, "# Body", "nginx-502")
        assert result["type"] == "incident"
        assert result["title"] == "Nginx 502"
        assert result["description"] == "Troubleshooting 502"
        assert result["resource"] == "opskg://doc/abc"

    def test_missing_title_uses_slug(self):
        meta = {"type": "concept"}
        result = normalize_frontmatter_for_okf(meta, "", "my-slug")
        assert result["title"] == "my-slug"

    def test_missing_description_derives_from_body(self):
        meta = {"type": "concept", "title": "Test"}
        body = "# Title\n\nA description of the concept.\n\n## Section"
        result = normalize_frontmatter_for_okf(meta, body, "test")
        assert "A description of the concept" in result["description"]

    def test_missing_resource_derives(self):
        meta = {"type": "concept", "title": "Test", "slug": "test"}
        result = normalize_frontmatter_for_okf(meta, "", "test")
        assert result["resource"] == "opskg://wiki/test"

    def test_missing_type_defaults_to_concept(self):
        meta = {"title": "Test"}
        result = normalize_frontmatter_for_okf(meta, "", "test")
        assert result["type"] == "concept"

    def test_timestamp_from_updated_at(self):
        meta = {"type": "concept", "title": "Test", "updated_at": "2024-06-01T00:00:00Z"}
        result = normalize_frontmatter_for_okf(meta, "", "test")
        assert result["timestamp"] == "2024-06-01T00:00:00Z"

    def test_timestamp_from_created_at(self):
        meta = {"type": "concept", "title": "Test", "created_at": "2024-01-01T00:00:00Z"}
        result = normalize_frontmatter_for_okf(meta, "", "test")
        assert result["timestamp"] == "2024-01-01T00:00:00Z"


# ═══════════════ 数据模型 ═══════════════


class TestOKFConcept:
    def test_concept_id(self):
        c = OKFConcept(rel_path="incidents/nginx-502.md", frontmatter={}, body="")
        assert c.concept_id == "incidents/nginx-502"

    def test_concept_id_no_extension(self):
        c = OKFConcept(rel_path="plain", frontmatter={}, body="")
        assert c.concept_id == "plain"

    def test_type_property(self):
        c = OKFConcept(rel_path="test.md", frontmatter={"type": "incident"}, body="")
        assert c.type == "incident"

    def test_type_missing(self):
        c = OKFConcept(rel_path="test.md", frontmatter={}, body="")
        assert c.type == ""


class TestExportResult:
    def test_defaults(self):
        r = ExportResult(bundle_dir=".")
        assert r.pages_exported == 0
        assert r.index_written is False
        assert r.log_written is False
        assert r.errors == []
        assert r.skipped == []


class TestImportResult:
    def test_defaults(self):
        r = ImportResult()
        assert r.pages_imported == 0
        assert r.pages_skipped == 0
        assert r.slugs == []
        assert r.errors == []
        assert r.warnings == []


# ═══════════════ OKF_VERSION ═══════════════


class TestOKFVersion:
    def test_version_is_defined(self):
        assert OKF_VERSION == "0.1"