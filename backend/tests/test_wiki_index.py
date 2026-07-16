"""Wiki Index 纯函数单元测试

覆盖：
- _slug_from_key / _key_from_slug / _parse_frontmatter
- _render_index_md / _render_shard_md / _render_hub_md
- 常量：SHARD_THRESHOLD / SHARD_TYPES / TYPE_LABEL / INDEX_SLUG
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.knowledge.wiki_index import (
    INDEX_SLUG,
    SHARD_THRESHOLD,
    SHARD_TYPES,
    TYPE_LABEL,
    _key_from_slug,
    _parse_frontmatter,
    _render_hub_md,
    _render_index_md,
    _render_shard_md,
    _slug_from_key,
)

# ═══════════════ _slug_from_key / _key_from_slug ═══════════════


class TestSlugFromKey:
    def test_wiki_prefix(self):
        assert _slug_from_key("wiki:nginx-502") == "nginx-502"

    def test_no_prefix(self):
        assert _slug_from_key("plain-text") == "plain-text"


class TestKeyFromSlug:
    def test_basic(self):
        assert _key_from_slug("nginx-502") == "wiki:nginx-502"

    def test_empty(self):
        assert _key_from_slug("") == "wiki:"


# ═══════════════ _parse_frontmatter ═══════════════


class TestParseFrontmatter:
    def test_valid(self):
        content = "---\ntype: incident\ntitle: Test\n---\n\n# Body\nContent"
        meta, body = _parse_frontmatter(content)
        assert meta == {"type": "incident", "title": "Test"}
        assert body == "# Body\nContent"

    def test_no_frontmatter(self):
        content = "# Heading\nText"
        meta, body = _parse_frontmatter(content)
        assert meta == {}

    def test_empty_frontmatter(self):
        content = "---\n---\n\nBody"
        meta, body = _parse_frontmatter(content)
        assert meta == {}
        assert body == "Body"


# ═══════════════ _render_index_md ═══════════════


class TestRenderIndexMd:
    def test_basic_structure(self):
        by_type = {
            "incident": [
                {
                    "slug": "nginx-502",
                    "title": "Nginx 502",
                    "tags": ["nginx", "502"],
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ]
        }
        recent = [
            {
                "slug": "nginx-502",
                "title": "Nginx 502",
                "updated_at": "2024-01-01T00:00:00Z",
            }
        ]
        orphans = []
        all_pages = [
            {
                "slug": "nginx-502",
                "title": "Nginx 502",
                "tags": ["nginx"],
                "updated_at": "2024-01-01T00:00:00Z",
            }
        ]

        md = _render_index_md(by_type, recent, orphans, all_pages)
        assert "---" in md
        assert "slug: index" in md
        assert "title: Wiki Index" in md
        assert "type: index" in md
        assert "Wiki Index" in md
        assert "[[nginx-502|Nginx 502]]" in md
        assert "nginx" in md

    def test_with_orphans(self):
        by_type = {
            "incident": [
                {
                    "slug": "orphan-page",
                    "title": "Orphan",
                    "tags": [],
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ]
        }
        recent = []
        orphans = ["orphan-page"]
        all_pages = [
            {
                "slug": "orphan-page",
                "title": "Orphan",
                "tags": [],
                "updated_at": "2024-01-01T00:00:00Z",
            }
        ]

        md = _render_index_md(by_type, recent, orphans, all_pages)
        assert "孤岛候选" in md
        assert "[[orphan-page]]" in md

    def test_recent_changes_section(self):
        by_type = {}
        recent = [
            {
                "slug": "recent-page",
                "title": "Recent",
                "updated_at": "2024-06-01T00:00:00Z",
            }
        ]
        orphans = []
        all_pages = []

        md = _render_index_md(by_type, recent, orphans, all_pages)
        assert "最近变更" in md
        assert "[[recent-page|Recent]]" in md

    def test_empty(self):
        md = _render_index_md({}, [], [], [])
        assert "0 个页面" in md
        assert "0 个孤岛" in md

    def test_extra_types(self):
        by_type = {
            "custom-type": [
                {
                    "slug": "custom",
                    "title": "Custom",
                    "tags": [],
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ]
        }
        recent = []
        orphans = []
        all_pages = [
            {
                "slug": "custom",
                "title": "Custom",
                "tags": [],
                "updated_at": "2024-01-01T00:00:00Z",
            }
        ]

        md = _render_index_md(by_type, recent, orphans, all_pages)
        assert "custom" in md.lower()


# ═══════════════ _render_shard_md ═══════════════


class TestRenderShardMd:
    def test_basic(self):
        type_pages = [
            {
                "slug": "incident-1",
                "title": "Incident 1",
                "tags": ["critical"],
                "updated_at": "2024-01-01T00:00:00Z",
                "review_status": "auto",
            }
        ]
        md = _render_shard_md("index-incident", "incident", type_pages)
        assert "slug: index-incident" in md
        assert "type: index-shard" in md
        assert "shard_type: incident" in md
        assert "[[incident-1|Incident 1]]" in md
        assert "[[index|Wiki Index 主页]]" in md

    def test_with_review_needed(self):
        type_pages = [
            {
                "slug": "page",
                "title": "Page",
                "tags": [],
                "updated_at": "2024-01-01T00:00:00Z",
                "review_status": "review_needed",
            }
        ]
        md = _render_shard_md("index-concept", "concept", type_pages)
        assert "需审查" in md


# ═══════════════ _render_hub_md ═══════════════


class TestRenderHubMd:
    def test_basic(self):
        shards = [{"slug": "index-incident", "type": "incident", "count": 5}]
        recent = [
            {
                "slug": "recent",
                "title": "Recent",
                "updated_at": "2024-01-01T00:00:00Z",
            }
        ]
        orphans = ["orphan-1"]
        all_pages = [
            {
                "slug": "recent",
                "title": "Recent",
                "updated_at": "2024-01-01T00:00:00Z",
            }
        ]

        md = _render_hub_md(shards, recent, orphans, all_pages)
        assert "type: index-hub" in md
        assert "shard_count: 1" in md
        assert "按类型浏览（分片）" in md
        assert "[[index-incident|故障（5）]]" in md
        assert "[[recent|Recent]]" in md
        assert "[[orphan-1]]" in md

    def test_empty(self):
        md = _render_hub_md([], [], [], [])
        assert "0 个页面" in md
        assert "0 个分片" in md
        assert "0 个孤岛" in md

    def test_many_orphans(self):
        shards = []
        orphans = [f"orphan-{i}" for i in range(25)]
        md = _render_hub_md(shards, [], orphans, [])
        assert "共 25 个" in md


# ═══════════════ 常量 ═══════════════


class TestConstants:
    def test_index_slug(self):
        assert INDEX_SLUG == "wiki:index"

    def test_shard_threshold(self):
        assert SHARD_THRESHOLD == 50

    def test_shard_types(self):
        assert "incident" in SHARD_TYPES
        assert "runbook" in SHARD_TYPES
        assert "service" in SHARD_TYPES
        assert "host" in SHARD_TYPES
        assert "concept" in SHARD_TYPES
        assert "entity" in SHARD_TYPES

    def test_type_labels(self):
        assert TYPE_LABEL["entity"] == "实体"
        assert TYPE_LABEL["concept"] == "概念"
        assert TYPE_LABEL["incident"] == "故障"
        assert TYPE_LABEL["runbook"] == "操作手册"
        assert TYPE_LABEL["service"] == "服务"
        assert TYPE_LABEL["host"] == "主机"