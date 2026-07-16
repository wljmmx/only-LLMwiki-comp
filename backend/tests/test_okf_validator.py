"""OKF Validator 纯函数单元测试

覆盖：
- OKFFinding.to_dict()
- OKFValidationResult.to_dict() / add_error() / add_warning()
- validate_concept()：各类 frontmatter 场景
- validate_reserved_file()：正确 type / 错误 type / 未知文件
- to_lint_issues()：转换逻辑
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.knowledge.okf_validator import (
    CODE_EMPTY_TYPE,
    CODE_INDEX_WRONG_TYPE,
    CODE_LOG_WRONG_TYPE,
    CODE_MISSING_FRONTMATTER,
    CODE_MISSING_RECOMMENDED,
    CODE_MISSING_TYPE,
    OKFFinding,
    OKFValidationResult,
    to_lint_issues,
    validate_concept,
    validate_reserved_file,
)

# ═══════════════ OKFFinding ═══════════════


class TestOKFFinding:
    def test_to_dict_full(self):
        f = OKFFinding(
            level="error", code="missing_type", file="test-slug",
            message="type is missing", field="type",
        )
        d = f.to_dict()
        assert d["level"] == "error"
        assert d["code"] == "missing_type"
        assert d["file"] == "test-slug"
        assert d["message"] == "type is missing"
        assert d["field"] == "type"

    def test_to_dict_minimal(self):
        f = OKFFinding(level="warn", code="some_code", message="hello")
        d = f.to_dict()
        assert "file" not in d
        assert "field" not in d
        assert d["level"] == "warn"
        assert d["code"] == "some_code"
        assert d["message"] == "hello"


# ═══════════════ OKFValidationResult ═══════════════


class TestOKFValidationResult:
    def test_defaults(self):
        r = OKFValidationResult()
        assert r.valid is True
        assert r.errors == 0
        assert r.warnings == 0
        assert r.concept_count == 0
        assert r.findings == []

    def test_add_error(self):
        r = OKFValidationResult()
        r.add_error("MISSING", "no frontmatter", file="page1")
        assert r.valid is False
        assert r.errors == 1
        assert r.warnings == 0
        assert r.findings[0].level == "error"
        assert r.findings[0].code == "MISSING"

    def test_add_warning(self):
        r = OKFValidationResult()
        r.add_warning("NO_DESC", "missing description", file="page1", field="description")
        assert r.valid is True  # warnings don't invalidate
        assert r.errors == 0
        assert r.warnings == 1
        assert r.findings[0].level == "warn"
        assert r.findings[0].field == "description"

    def test_mixed_errors_and_warnings(self):
        r = OKFValidationResult()
        r.add_error("E1", "err1", file="a")
        r.add_warning("W1", "warn1", file="a")
        r.add_error("E2", "err2", file="b")
        assert r.valid is False
        assert r.errors == 2
        assert r.warnings == 1
        assert len(r.findings) == 3

    def test_to_dict(self):
        r = OKFValidationResult()
        r.concept_count = 5
        r.add_error("E1", "err", file="x")
        r.add_warning("W1", "warn", file="y")
        d = r.to_dict()
        assert d["okf_version"] == r.okf_version
        assert d["valid"] is False
        assert d["errors"] == 1
        assert d["warnings"] == 1
        assert d["concept_count"] == 5
        assert len(d["findings"]) == 2


# ═══════════════ validate_concept ═══════════════


class TestValidateConcept:
    def test_valid_concept_with_all_fields(self):
        fm = {
            "type": "incident",
            "title": "Nginx 502",
            "description": "排查 502",
            "resource": "opskg://doc/abc",
            "tags": ["nginx", "502"],
            "timestamp": "2024-01-01T00:00:00Z",
        }
        findings = validate_concept("nginx-502", fm, "# Body")
        # All recommended fields present → no warnings
        assert len(findings) == 0

    def test_none_frontmatter(self):
        findings = validate_concept("test", None, "body")
        assert len(findings) == 1
        assert findings[0].code == CODE_MISSING_FRONTMATTER
        assert findings[0].level == "error"

    def test_empty_frontmatter_no_body_frontmatter(self):
        # Empty dict with body that doesn't start with ---
        findings = validate_concept("test", {}, "plain body")
        assert len(findings) == 1
        assert findings[0].code == CODE_MISSING_FRONTMATTER

    def test_missing_type(self):
        fm = {"title": "Something"}
        findings = validate_concept("test", fm, "# Body")
        type_codes = {f.code for f in findings}
        assert CODE_MISSING_TYPE in type_codes

    def test_empty_type(self):
        fm = {"type": "", "title": "Something"}
        findings = validate_concept("test", fm, "# Body")
        type_codes = {f.code for f in findings}
        assert CODE_EMPTY_TYPE in type_codes

    def test_whitespace_type(self):
        fm = {"type": "   ", "title": "Something"}
        findings = validate_concept("test", fm, "# Body")
        type_codes = {f.code for f in findings}
        assert CODE_EMPTY_TYPE in type_codes

    def test_missing_recommended_fields(self):
        fm = {"type": "concept"}
        findings = validate_concept("test", fm, "# Body")
        codes = {f.code for f in findings}
        assert CODE_MISSING_RECOMMENDED in codes
        fields = {f.field for f in findings if f.code == CODE_MISSING_RECOMMENDED}
        assert "title" in fields
        assert "description" in fields
        assert "resource" in fields
        assert "tags" in fields
        assert "timestamp" in fields

    def test_partial_recommended_fields(self):
        fm = {"type": "concept", "title": "Test", "tags": []}
        findings = validate_concept("test", fm, "# Body")
        codes = {f.code for f in findings}
        assert CODE_MISSING_RECOMMENDED in codes
        fields = {f.field for f in findings if f.code == CODE_MISSING_RECOMMENDED}
        assert "title" not in fields
        # empty list [] is also treated as missing (val == [] check)
        assert "tags" in fields
        assert "description" in fields
        assert "resource" in fields
        assert "timestamp" in fields


# ═══════════════ validate_reserved_file ═══════════════


class TestValidateReservedFile:
    def test_index_correct_type(self):
        findings = validate_reserved_file("index.md", {"type": "index"})
        assert len(findings) == 0

    def test_log_correct_type(self):
        findings = validate_reserved_file("log.md", {"type": "log"})
        assert len(findings) == 0

    def test_index_wrong_type(self):
        findings = validate_reserved_file("index.md", {"type": "concept"})
        assert len(findings) == 1
        assert findings[0].code == CODE_INDEX_WRONG_TYPE
        assert findings[0].level == "warn"

    def test_log_wrong_type(self):
        findings = validate_reserved_file("log.md", {"type": "incident"})
        assert len(findings) == 1
        assert findings[0].code == CODE_LOG_WRONG_TYPE
        assert findings[0].level == "warn"

    def test_unknown_file_returns_empty(self):
        findings = validate_reserved_file("other.md", {"type": "concept"})
        assert len(findings) == 0


# ═══════════════ to_lint_issues ═══════════════


class TestToLintIssues:
    def test_converts_error_to_lint_issue(self):
        result = OKFValidationResult()
        result.add_error(CODE_MISSING_TYPE, "type missing", file="incidents/test.md")
        issues = to_lint_issues(result)
        assert len(issues) == 1
        assert issues[0]["type"] == "okf_violation"
        assert issues[0]["severity"] == "error"
        assert issues[0]["slug"] == "test"
        assert "type missing" in issues[0]["message"]
        assert issues[0]["detail"]["okf_code"] == CODE_MISSING_TYPE

    def test_converts_warning_to_lint_issue(self):
        result = OKFValidationResult()
        result.add_warning(CODE_MISSING_RECOMMENDED, "missing title", file="concepts/foo.md", field="title")
        issues = to_lint_issues(result)
        assert len(issues) == 1
        assert issues[0]["severity"] == "warn"
        assert issues[0]["slug"] == "foo"
        assert issues[0]["detail"]["okf_field"] == "title"

    def test_empty_result(self):
        result = OKFValidationResult()
        issues = to_lint_issues(result)
        assert len(issues) == 0

    def test_multiple_findings(self):
        result = OKFValidationResult()
        result.add_error("E1", "err", file="a/b.md")
        result.add_warning("W1", "warn", file="c/d.md")
        issues = to_lint_issues(result)
        assert len(issues) == 2
        assert issues[0]["slug"] == "b"
        assert issues[1]["slug"] == "d"

    def test_file_without_slash(self):
        result = OKFValidationResult()
        result.add_error("E1", "err", file="slug.md")
        issues = to_lint_issues(result)
        assert issues[0]["slug"] == "slug"

    def test_file_without_extension(self):
        result = OKFValidationResult()
        result.add_error("E1", "err", file="plain")
        issues = to_lint_issues(result)
        assert issues[0]["slug"] == "plain"