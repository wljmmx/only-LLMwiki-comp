"""wiki_query.py 纯函数单元测试（不需要 LLM / DB）

覆盖：
- _tokenize：英文分词、中文分词、停用词过滤、CJK 混合
- _cosine_similarity：相同向量、正交向量、不同长度、空向量
- _strip_frontmatter：有/无 frontmatter、边界情况
- _extract_snippet：命中 token、未命中、空 tokens
- _rebuild_frontmatter：往返一致性
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.knowledge.wiki_query import (
    _cosine_similarity,
    _extract_snippet,
    _rebuild_frontmatter,
    _strip_frontmatter,
    _tokenize,
)

# ═══════════════ _tokenize ═══════════════


class TestTokenize:
    def test_tokenize_english(self):
        """English words are split and lowercased; stop words and short tokens removed"""
        tokens = _tokenize("Nginx 502 Bad Gateway Error")
        assert "nginx" in tokens
        assert "502" in tokens
        assert "bad" in tokens
        assert "gateway" in tokens
        assert "error" in tokens
        # stop words removed
        assert "the" not in tokens
        assert "is" not in tokens
        # short tokens (< 2 chars) removed from English parts
        # "a" would be removed as stop word or short

    def test_tokenize_chinese(self):
        """Chinese characters produce single chars + bigrams"""
        tokens = _tokenize("故障排查")
        # single chars
        assert "故" in tokens
        assert "障" in tokens
        assert "排" in tokens
        assert "查" in tokens
        # bigrams
        assert "故障" in tokens
        assert "障排" in tokens
        assert "排查" in tokens

    def test_tokenize_stop_words_removed(self):
        """English stop words are removed; Chinese stop words also removed from English split"""
        tokens = _tokenize("the is a and what how")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "a" not in tokens
        assert "and" not in tokens
        assert "what" not in tokens
        assert "how" not in tokens

    def test_tokenize_chinese_stop_words_handled(self):
        """Chinese stop words appear as CJK chars but are short; bigrams still produced"""
        tokens = _tokenize("的 了")
        # Chinese chars are added as single chars regardless of stop word status
        # (the CJK loop adds all CJK chars, stop word filtering only applies to English split)
        assert "的" in tokens
        assert "了" in tokens
        # Bigrams are also produced
        assert "的了" in tokens

    def test_tokenize_mixed_cjk_english(self):
        """Mixed CJK and English text produces both types of tokens"""
        tokens = _tokenize("Nginx 反向代理 server")
        # English tokens
        assert "nginx" in tokens
        assert "server" in tokens
        # Chinese single chars
        assert "反" in tokens
        assert "向" in tokens
        assert "代" in tokens
        assert "理" in tokens
        # Chinese bigrams
        assert "反向" in tokens
        assert "向代" in tokens
        assert "代理" in tokens

    def test_tokenize_empty(self):
        """Empty input returns empty list"""
        assert _tokenize("") == []

    def test_tokenize_numbers_only(self):
        """Numeric tokens are preserved"""
        tokens = _tokenize("502 503 504")
        assert "502" in tokens
        assert "503" in tokens
        assert "504" in tokens

    def test_tokenize_english_single_char_filtered(self):
        """Single English characters are filtered out (len < 2)"""
        tokens = _tokenize("a b c")
        # "a" is a stop word, "b" and "c" might or might not be in stop words
        # After splitting by non-alphanumeric, single chars with len < 2 are filtered
        assert "b" not in tokens
        assert "c" not in tokens


# ═══════════════ _cosine_similarity ═══════════════


class TestCosineSimilarity:
    def test_identical_vectors(self):
        """Identical vectors return 1.0"""
        v = [1.0, 2.0, 3.0]
        result = _cosine_similarity(v, v)
        assert math.isclose(result, 1.0, rel_tol=1e-9)

    def test_orthogonal_vectors(self):
        """Orthogonal vectors return ~0.0"""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        result = _cosine_similarity(a, b)
        assert math.isclose(result, 0.0, abs_tol=1e-9)

    def test_different_lengths(self):
        """Vectors of different lengths return 0.0"""
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_empty_vectors(self):
        """Empty vectors return 0.0"""
        assert _cosine_similarity([], []) == 0.0
        assert _cosine_similarity([1.0], []) == 0.0

    def test_zero_vector(self):
        """Zero vector returns 0.0 (norm is 0)"""
        assert _cosine_similarity([0.0, 0.0, 0.0], [1.0, 2.0, 3.0]) == 0.0
        assert _cosine_similarity([1.0, 2.0, 3.0], [0.0, 0.0, 0.0]) == 0.0

    def test_negative_values(self):
        """Vectors with negative values produce correct cosine"""
        a = [1.0, -2.0, 3.0]
        b = [-1.0, 2.0, -3.0]
        result = _cosine_similarity(a, b)
        assert math.isclose(result, -1.0, rel_tol=1e-9)

    def test_partial_overlap(self):
        """Partially overlapping vectors produce intermediate cosine"""
        a = [1.0, 1.0, 0.0]
        b = [1.0, 0.0, 0.0]
        result = _cosine_similarity(a, b)
        expected = 1.0 / math.sqrt(2)  # 1 / sqrt(2)
        assert math.isclose(result, expected, rel_tol=1e-9)


# ═══════════════ _strip_frontmatter ═══════════════


class TestStripFrontmatter:
    def test_strip_with_frontmatter(self):
        """Frontmatter is stripped, leaving only body"""
        md = """---
slug: test-page
title: Test Page
type: concept
---

# Overview

This is the body."""

        body = _strip_frontmatter(md)
        assert "slug:" not in body
        assert "title:" not in body
        assert "# Overview" in body
        assert "This is the body." in body

    def test_strip_without_frontmatter(self):
        """Returns unchanged when no frontmatter"""
        md = "# Just a heading\n\nSome content."
        result = _strip_frontmatter(md)
        assert result == md

    def test_strip_empty_string(self):
        """Empty string returns unchanged"""
        assert _strip_frontmatter("") == ""

    def test_strip_malformed_frontmatter(self):
        """Malformed frontmatter (only opening ---) returns unchanged"""
        md = "---\nJust a stray separator"
        # Only one --- found, so split returns 2 parts → returns unchanged
        result = _strip_frontmatter(md)
        assert result == md

    def test_strip_frontmatter_with_trailing_newlines(self):
        """Frontmatter with empty lines preserves body"""
        md = """---
key: value
---

body text"""
        body = _strip_frontmatter(md)
        assert body == "body text"

    def test_strip_frontmatter_body_with_dashes(self):
        """Body containing --- should not be affected"""
        md = """---
slug: test
---

# Section

Some text with --- a dash separator.

More content."""
        body = _strip_frontmatter(md)
        assert "# Section" in body
        assert "--- a dash separator" in body
        assert "More content." in body


# ═══════════════ _extract_snippet ═══════════════


class TestExtractSnippet:
    def test_extract_with_matching_token(self):
        """Finds token and returns a snippet around it"""
        md = "This is a long document that contains the word nginx somewhere in the middle and then continues with more text about server configuration and deployment strategies."
        snippet = _extract_snippet(md, ["nginx"], window=60)
        assert "nginx" in snippet
        # Should be a window around the token, not the full text
        assert len(snippet) < len(md)

    def test_extract_without_token(self):
        """Returns beginning of text when no token matches"""
        md = "This document has no matching keyword anywhere."
        snippet = _extract_snippet(md, ["nginx"], window=200)
        assert snippet == md[:200]

    def test_extract_empty_tokens(self):
        """Returns beginning of text when tokens list is empty"""
        md = "Some content here."
        snippet = _extract_snippet(md, [], window=200)
        assert snippet == md[:200]

    def test_extract_empty_text(self):
        """Empty text returns empty string"""
        assert _extract_snippet("", ["token"]) == ""

    def test_extract_first_matching_token(self):
        """Returns snippet around the first matching token (not all)"""
        md = "First keyword alpha here. Then keyword beta later."
        snippet = _extract_snippet(md, ["beta", "alpha"], window=30)
        # "beta" comes first in the tokens list, so it should match first
        assert "beta" in snippet

    def test_extract_wikilink_rendered(self):
        """Wikilinks in markdown are rendered to plain text in snippet"""
        md = "See [[nginx-config]] for details about the nginx setup."
        snippet = _extract_snippet(md, ["nginx"], window=100)
        # Wikilink should be stripped to display text
        assert "[[" not in snippet
        assert "nginx-config" in snippet


# ═══════════════ _rebuild_frontmatter ═══════════════


class TestRebuildFrontmatter:
    def test_rebuild_roundtrip(self):
        """Rebuild frontmatter then strip produces the same body (modulo trailing newline)"""
        meta = {
            "slug": "test-page",
            "title": "Test Page",
            "type": "concept",
            "tags": ["test", "example"],
        }
        body = "# Overview\n\nThis is the body content.\n\n## Details\n\nMore info."
        rebuilt = _rebuild_frontmatter(meta, body)
        stripped = _strip_frontmatter(rebuilt)
        # _rebuild_frontmatter normalizes body: body.rstrip() + "\n"
        assert stripped == body.rstrip() + "\n"

    def test_rebuild_empty_meta(self):
        """Empty meta dict returns just the body"""
        body = "# Just body\n"
        result = _rebuild_frontmatter({}, body)
        assert result == body

    def test_rebuild_preserves_frontmatter_values(self):
        """Rebuilt frontmatter contains all meta fields"""
        meta = {
            "slug": "my-page",
            "title": "My Page",
            "type": "entity",
            "review_status": "auto",
        }
        body = "Body content.\n"
        rebuilt = _rebuild_frontmatter(meta, body)
        assert 'slug: my-page' in rebuilt
        assert 'title: My Page' in rebuilt
        assert 'type: entity' in rebuilt
        assert 'review_status: auto' in rebuilt
        assert 'Body content.' in rebuilt

    def test_rebuild_roundtrip_with_unicode(self):
        """Rebuild + strip roundtrip with Chinese content"""
        meta = {
            "slug": "nginx-502",
            "title": "Nginx 502 故障排查",
            "type": "incident",
        }
        body = "# 概述\n\nNginx 502 错误排查步骤。\n"
        rebuilt = _rebuild_frontmatter(meta, body)
        stripped = _strip_frontmatter(rebuilt)
        assert stripped == body
        assert "Nginx 502 故障排查" in rebuilt
