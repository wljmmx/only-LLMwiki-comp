"""Wiki Lint 纯函数单元测试

覆盖：
- compute_issue_key() / LintIssue / LintReport
- _split_frontmatter / _check_contradictions
- _parse_json_array / _entity_to_wiki_slugs
- 常量：REQUIRED_SECTIONS / SEV_* / TYPE_*
"""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.knowledge.wiki_lint import (
    REQUIRED_SECTIONS,
    SEV_ERROR,
    SEV_INFO,
    SEV_WARN,
    TYPE_CONTRADICTION,
    TYPE_DEADLINK,
    TYPE_EMPTY_SECTION,
    TYPE_MISSING_CONCEPT,
    TYPE_MISSING_CONCEPT_FROM_GRAPH,
    TYPE_MISSING_TYPE_SECTION,
    TYPE_OKF_VIOLATION,
    TYPE_ORPHAN,
    TYPE_STALE,
    LintIssue,
    LintReport,
    _check_contradictions,
    _entity_to_wiki_slugs,
    _parse_json_array,
    _split_frontmatter,
    compute_issue_key,
)

# ═══════════════ compute_issue_key ═══════════════


class TestComputeIssueKey:
    def test_deterministic(self):
        k1 = compute_issue_key("stale", "my-page", "doc changed")
        k2 = compute_issue_key("stale", "my-page", "doc changed")
        assert k1 == k2
        assert len(k1) == 16

    def test_different_messages_produce_different_keys(self):
        k1 = compute_issue_key("stale", "page", "msg A")
        k2 = compute_issue_key("stale", "page", "msg B")
        assert k1 != k2

    def test_different_types_produce_different_keys(self):
        k1 = compute_issue_key("stale", "page", "msg")
        k2 = compute_issue_key("orphan", "page", "msg")
        assert k1 != k2


# ═══════════════ LintIssue ═══════════════


class TestLintIssue:
    def test_basic_creation(self):
        issue = LintIssue(
            type=TYPE_STALE, severity=SEV_WARN, slug="test", message="stale doc"
        )
        assert issue.type == TYPE_STALE
        assert issue.severity == SEV_WARN
        assert issue.slug == "test"
        assert issue.message == "stale doc"
        assert issue.detail == {}

    def test_with_detail(self):
        issue = LintIssue(
            type=TYPE_CONTRADICTION,
            severity=SEV_ERROR,
            slug="page-a",
            message="conflict",
            detail={"param": "port", "values": {"a": "80", "b": "8080"}},
        )
        assert issue.detail["param"] == "port"


# ═══════════════ LintReport ═══════════════


class TestLintReport:
    def test_add(self):
        report = LintReport()
        report.add(
            LintIssue(type=TYPE_STALE, severity=SEV_WARN, slug="test", message="msg")
        )
        assert len(report.issues) == 1
        assert report.by_type[TYPE_STALE] == 1
        assert report.by_severity[SEV_WARN] == 1

    def test_add_multiple(self):
        report = LintReport()
        report.add(
            LintIssue(type=TYPE_STALE, severity=SEV_WARN, slug="a", message="m1")
        )
        report.add(
            LintIssue(type=TYPE_ORPHAN, severity=SEV_INFO, slug="b", message="m2")
        )
        report.add(
            LintIssue(type=TYPE_STALE, severity=SEV_WARN, slug="c", message="m3")
        )
        assert len(report.issues) == 3
        assert report.by_type[TYPE_STALE] == 2
        assert report.by_type[TYPE_ORPHAN] == 1
        assert report.by_severity[SEV_WARN] == 2
        assert report.by_severity[SEV_INFO] == 1

    def test_recompute(self):
        report = LintReport()
        report.add(
            LintIssue(type=TYPE_STALE, severity=SEV_WARN, slug="a", message="m1")
        )
        # Manually corrupt counts
        report.by_type = {}
        report.by_severity = {}
        report.recompute()
        assert report.by_type[TYPE_STALE] == 1
        assert report.by_severity[SEV_WARN] == 1

    def test_to_dict(self):
        report = LintReport(pages_checked=5, ignored_count=1)
        report.add(
            LintIssue(
                type=TYPE_STALE,
                severity=SEV_WARN,
                slug="test",
                message="stale",
                detail={"key": "val"},
            )
        )
        d = report.to_dict()
        assert d["pages_checked"] == 5
        assert d["total_issues"] == 1
        assert d["ignored_count"] == 1
        assert d["by_type"][TYPE_STALE] == 1
        assert d["by_severity"][SEV_WARN] == 1
        assert len(d["issues"]) == 1
        assert d["issues"][0]["type"] == TYPE_STALE
        assert d["issues"][0]["slug"] == "test"
        assert "issue_key" in d["issues"][0]
        assert d["issues"][0]["detail"] == {"key": "val"}


# ═══════════════ _split_frontmatter ═══════════════


class TestSplitFrontmatter:
    def test_valid(self):
        meta, body = _split_frontmatter(
            "---\ntype: incident\n---\n\n# Body\nText"
        )
        assert meta == {"type": "incident"}
        assert body == "# Body\nText"

    def test_no_frontmatter(self):
        meta, body = _split_frontmatter("# Heading")
        assert meta == {}
        assert body == "# Heading"


# ═══════════════ _check_contradictions ═══════════════


class TestCheckContradictions:
    def test_no_conflict(self):
        contents = {"page-a": "# Body\nport: 80\n", "page-b": "# Body\nport: 80\n"}
        issues = _check_contradictions(contents)
        assert len(issues) == 0

    def test_numeric_conflict(self):
        contents = {"page-a": "# Body\nport: 80\n", "page-b": "# Body\nport: 8080\n"}
        issues = _check_contradictions(contents)
        assert len(issues) >= 1
        assert any(i.type == TYPE_CONTRADICTION for i in issues)
        assert any("port" in i.message for i in issues)

    def test_same_page_no_conflict(self):
        # Same page appearing twice with same value → no conflict
        contents = {"page-a": "port: 80\nport: 80\n"}
        issues = _check_contradictions(contents)
        assert len(issues) == 0

    def test_empty_contents(self):
        issues = _check_contradictions({})
        assert len(issues) == 0

    def test_boolean_conflict(self):
        contents = {
            "page-a": "enabled: true\n",
            "page-b": "enabled: false\n",
        }
        issues = _check_contradictions(contents)
        assert len(issues) >= 1

    def test_chinese_value(self):
        # Chinese key names not matched by regex, but Chinese values are fine
        contents = {
            "page-a": "mode: 是\n",
            "page-b": "mode: 否\n",
        }
        issues = _check_contradictions(contents)
        assert len(issues) >= 1

    def test_bold_param_format(self):
        contents = {
            "page-a": "- **timeout**: 30s\n",
            "page-b": "- **timeout**: 60s\n",
        }
        issues = _check_contradictions(contents)
        assert len(issues) >= 1
        assert any("timeout" in i.message.lower() for i in issues)

    def test_ignore_long_values(self):
        contents = {
            "page-a": "desc: " + "x" * 101 + "\n",
            "page-b": "desc: " + "y" * 101 + "\n",
        }
        issues = _check_contradictions(contents)
        # Values > 100 chars are filtered out
        assert len(issues) == 0


# ═══════════════ _parse_json_array ═══════════════


class TestParseJsonArray:
    def test_valid_array(self):
        result = _parse_json_array('[{"key": "val"}]')
        assert result == [{"key": "val"}]

    def test_empty_array(self):
        result = _parse_json_array("[]")
        assert result == []

    def test_with_code_fence(self):
        result = _parse_json_array('```json\n[{"a": 1}]\n```')
        assert result == [{"a": 1}]

    def test_invalid_json(self):
        result = _parse_json_array("not json")
        assert result == []

    def test_no_brackets(self):
        result = _parse_json_array("plain text")
        assert result == []

    def test_filters_non_dict_items(self):
        result = _parse_json_array('[{"a": 1}, "string", 42]')
        assert result == [{"a": 1}]


# ═══════════════ _entity_to_wiki_slugs ═══════════════


class TestEntityToWikiSlugs:
    def test_basic(self):
        slugs = _entity_to_wiki_slugs("Nginx")
        assert "Nginx" in slugs

    def test_generates_kebab(self):
        slugs = _entity_to_wiki_slugs("My Service")
        assert "my-service" in slugs

    def test_generates_prefix_candidates(self):
        slugs = _entity_to_wiki_slugs("MyService")
        assert "host-MyService" in slugs
        assert "service-MyService" in slugs
        assert "component-MyService" in slugs
        assert "incident-MyService" in slugs

    def test_no_duplicate_prefix(self):
        slugs = _entity_to_wiki_slugs("host-web-01")
        # Already starts with "host-", should not add another
        assert "host-host-web-01" not in slugs


# ═══════════════ 常量 ═══════════════


class TestConstants:
    def test_severity_levels(self):
        assert SEV_INFO == "info"
        assert SEV_WARN == "warn"
        assert SEV_ERROR == "error"

    def test_issue_types(self):
        assert TYPE_CONTRADICTION == "contradiction"
        assert TYPE_STALE == "stale"
        assert TYPE_ORPHAN == "orphan"
        assert TYPE_DEADLINK == "deadlink"
        assert TYPE_MISSING_CONCEPT == "missing_concept"
        assert TYPE_MISSING_CONCEPT_FROM_GRAPH == "missing_concept_from_graph"
        assert TYPE_EMPTY_SECTION == "empty_section"
        assert TYPE_MISSING_TYPE_SECTION == "missing_type_section"
        assert TYPE_OKF_VIOLATION == "okf_violation"

    def test_required_sections(self):
        assert "概述" in REQUIRED_SECTIONS["entity"]
        assert "属性" in REQUIRED_SECTIONS["entity"]
        assert "来源" in REQUIRED_SECTIONS["entity"]
        assert "概述" in REQUIRED_SECTIONS["incident"]
        assert "排查步骤" in REQUIRED_SECTIONS["incident"]
        assert "处置方案" in REQUIRED_SECTIONS["incident"]
        assert "来源" in REQUIRED_SECTIONS["incident"]
        assert "概述" in REQUIRED_SECTIONS["runbook"]
        assert "排查步骤" in REQUIRED_SECTIONS["runbook"]
        assert "处置方案" in REQUIRED_SECTIONS["runbook"]