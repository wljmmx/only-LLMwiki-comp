"""P1-12b: Wiki Lint issue 忽略持久化测试

覆盖：
- compute_issue_key 稳定性与区分度
- ignore_issue / list_ignored_issues / unignore_issue / _load_ignored_keys CRUD
- ignore 幂等（重复忽略仅更新 reason）
- lint_all 过滤已忽略 issue + ignored_count 计数
- POST/DELETE/GET /llm-wiki/lint/ignore* 端点

DB 隔离：monkeypatch 重定向 wiki_lint._LINT_DB_PATH / version_control /
wikilink 的 DB_PATH 到 tmp_path，避免污染真实库。
"""
from __future__ import annotations

import os
import sys

# 确保测试期间关闭认证（开发模式放行）
os.environ.setdefault("OPSKG_API_TOKEN", "")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


# ═══════════════ DB 隔离 fixture ═══════════════


@pytest.fixture
def isolated_lint_db(tmp_path, monkeypatch):
    """重定向 lint 忽略库 + version_control + wikilink 到 tmp_path，重置 vc 单例"""
    import app.knowledge.wiki_lint as lint_mod
    import app.knowledge.wikilink as wl_mod
    import app.storage.version_control as vc_mod

    monkeypatch.setattr(lint_mod, "_LINT_DB_PATH", tmp_path / "wiki_lint.db")
    monkeypatch.setattr(vc_mod, "DB_PATH", tmp_path / "versions.db")
    monkeypatch.setattr(wl_mod, "DB_PATH", tmp_path / "events.db")
    monkeypatch.setattr(vc_mod, "_vc", None)
    yield tmp_path


def _seed_page(slug: str, title: str = "测试页", page_type: str = "concept") -> dict:
    """seed 一个 wiki page（含 概述+来源 章节，避免 missing_type_section 噪音）"""
    from app.storage import get_version_control

    vc = get_version_control()
    content = f"""---
slug: {slug}
title: {title}
type: {page_type}
tags: []
created_at: 2026-07-01T00:00:00Z
updated_at: 2026-07-01T00:00:00Z
review_status: auto
---

# {title}

## 概述
测试内容。

## 来源
- 测试 seed
"""
    return vc.save_version(
        doc_key=f"wiki:{slug}",
        title=title,
        content=content,
        author="test-seed",
        change_summary="seed",
    )


# ═══════════════ compute_issue_key ═══════════════


class TestComputeIssueKey:
    def test_deterministic_same_input(self):
        from app.knowledge.wiki_lint import compute_issue_key

        k1 = compute_issue_key("orphan", "nginx", "无入链")
        k2 = compute_issue_key("orphan", "nginx", "无入链")
        assert k1 == k2

    def test_is_16_hex_chars(self):
        from app.knowledge.wiki_lint import compute_issue_key

        k = compute_issue_key("a", "b", "c")
        assert len(k) == 16
        int(k, 16)  # 合法 hex

    def test_different_inputs_yield_different_keys(self):
        from app.knowledge.wiki_lint import compute_issue_key

        assert compute_issue_key("orphan", "a", "m") != compute_issue_key("stale", "a", "m")
        assert compute_issue_key("orphan", "a", "m1") != compute_issue_key("orphan", "a", "m2")
        assert compute_issue_key("orphan", "a1", "m") != compute_issue_key("orphan", "a2", "m")


# ═══════════════ 忽略存储 CRUD ═══════════════


class TestIgnoreStore:
    def test_ignore_list_unignore_roundtrip(self, isolated_lint_db):
        from app.knowledge.wiki_lint import (
            _load_ignored_keys,
            ignore_issue,
            list_ignored_issues,
            unignore_issue,
        )

        key = "abc123def456ab78"
        ignore_issue(key, type="orphan", slug="nginx", message="无入链", reason="噪音")

        assert key in _load_ignored_keys()
        items = list_ignored_issues()
        assert len(items) == 1
        assert items[0]["issue_key"] == key
        assert items[0]["type"] == "orphan"
        assert items[0]["slug"] == "nginx"
        assert items[0]["reason"] == "噪音"

        assert unignore_issue(key) is True
        assert unignore_issue(key) is False  # 再删无记录
        assert _load_ignored_keys() == set()

    def test_ignore_is_idempotent_updates_reason(self, isolated_lint_db):
        from app.knowledge.wiki_lint import ignore_issue, list_ignored_issues

        key = "dupkey0000000001"
        ignore_issue(key, type="orphan", slug="a", message="m", reason="r1")
        ignore_issue(key, type="orphan", slug="a", message="m", reason="r2")

        items = list_ignored_issues()
        assert len(items) == 1  # 不重复
        assert items[0]["reason"] == "r2"  # 更新为最新

    def test_load_ignored_keys_empty_when_no_rows(self, isolated_lint_db):
        from app.knowledge.wiki_lint import _load_ignored_keys

        assert _load_ignored_keys() == set()


# ═══════════════ lint_all 过滤已忽略 ═══════════════


class TestLintAllFiltersIgnored:
    def test_lint_all_excludes_ignored_issue(self, isolated_lint_db):
        from app.knowledge.wiki_lint import (
            compute_issue_key,
            ignore_issue,
            lint_all,
        )

        # seed 一个无入链的孤儿页面 → lint_all 应报 orphan issue
        _seed_page("orphan-test-page", "孤儿测试页", "concept")

        report_before = lint_all(include_stale=False)
        orphan_issues = [
            i
            for i in report_before.issues
            if i.type == "orphan" and i.slug == "orphan-test-page"
        ]
        assert len(orphan_issues) == 1

        key = compute_issue_key(
            "orphan", "orphan-test-page", orphan_issues[0].message
        )
        ignore_issue(
            key,
            type="orphan",
            slug="orphan-test-page",
            message=orphan_issues[0].message,
            reason="已知噪音",
        )

        report_after = lint_all(include_stale=False)
        # 该 issue 已被过滤
        assert not any(
            i.type == "orphan" and i.slug == "orphan-test-page"
            for i in report_after.issues
        )
        # ignored_count 至少 1
        assert report_after.ignored_count >= 1
        # to_dict 透出 ignored_count
        d = report_after.to_dict()
        assert d["ignored_count"] == report_after.ignored_count
        # 每个 issue dict 带 issue_key
        assert all("issue_key" in i for i in d["issues"])

    def test_to_dict_includes_issue_key(self, isolated_lint_db):
        from app.knowledge.wiki_lint import lint_all

        _seed_page("key-check-page", "键检查页", "concept")
        report = lint_all(include_stale=False)
        d = report.to_dict()
        assert "ignored_count" in d
        for issue_dict in d["issues"]:
            assert "issue_key" in issue_dict
            assert len(issue_dict["issue_key"]) == 16


# ═══════════════ HTTP 端点 ═══════════════


class TestLintIgnoreEndpoints:
    def test_ignore_get_unignore_endpoints(self, isolated_lint_db):
        key = "endpointkey00001"
        r = client.post(
            "/llm-wiki/lint/ignore",
            json={
                "issue_key": key,
                "type": "orphan",
                "slug": "x",
                "message": "m",
                "reason": "r",
            },
        )
        assert r.status_code == 200
        assert r.json() == {"issue_key": key, "ignored": True}

        r = client.get("/llm-wiki/lint/ignored")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["items"][0]["issue_key"] == key

        r = client.delete(f"/llm-wiki/lint/ignore/{key}")
        assert r.status_code == 200
        assert r.json()["unignored"] is True

        r = client.get("/llm-wiki/lint/ignored")
        assert r.json()["count"] == 0

    def test_ignore_missing_fields_returns_422(self, isolated_lint_db):
        # 缺必填字段 type → 422
        r = client.post(
            "/llm-wiki/lint/ignore",
            json={"issue_key": "k", "slug": "s", "message": "m"},
        )
        assert r.status_code == 422

    def test_unignore_nonexistent_returns_unignored_false(self, isolated_lint_db):
        r = client.delete("/llm-wiki/lint/ignore/no-such-key")
        assert r.status_code == 200
        assert r.json()["unignored"] is False
