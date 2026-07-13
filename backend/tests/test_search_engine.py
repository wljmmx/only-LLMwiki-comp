"""搜索集成测试（P2-1.5）

验证 SearchEngine 在 jieba 双端预分词下的端到端行为：
- 中文 query 召回中文文档（jieba 切分后能匹配分词后的索引）
- snippet/title 从 doc_snippets 取原始内容（不展示破碎的分词文本）
- remove_index 同步清理 doc_snippets
- 多文档相关性排序
- 向量检索与关键词检索融合
- 配置驱动的分词模式切换

DB 隔离：monkeypatch DB_PATH 到 tmp_path，重置 SearchEngine 单例。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════ DB 隔离 fixture ═══════════════


@pytest.fixture
def isolated_search_db(tmp_path, monkeypatch):
    """重定向 search_engine DB_PATH 到 tmp_path，重置单例"""
    import app.search.search_engine as se_mod

    se_db = tmp_path / "search_index.db"
    monkeypatch.setattr(se_mod, "DB_PATH", se_db)
    monkeypatch.setattr(se_mod, "_engine", None)
    yield se_mod.get_search_engine


# ═══════════════ 索引写入 ═══════════════


class TestIndexDocument:
    def test_index_writes_fts_and_snippets(self, isolated_search_db):
        """index_document 应同时写入 docs_fts 和 doc_snippets"""
        engine = isolated_search_db()
        engine.index_document(
            "doc-1",
            "Nginx 502 故障排查",
            "上游不可达导致 502 Bad Gateway",
            "md",
        )

        # 验证通过引擎内部接口检索能命中
        results = engine.search("Nginx", limit=5)
        assert len(results) == 1
        assert results[0]["doc_id"] == "doc-1"

    def test_index_stores_original_title_in_snippets(self, isolated_search_db):
        """doc_snippets 应存原始 title（未分词），用于展示"""
        engine = isolated_search_db()
        original_title = "Nginx 502 故障排查指南"
        engine.index_document(
            "doc-1",
            original_title,
            "上游不可达导致 502 Bad Gateway",
            "md",
        )

        results = engine.search("Nginx", limit=5)
        assert results
        # title 应为原始（未分词）文本
        assert results[0]["title"] == original_title

    def test_index_stores_original_content_in_snippets(self, isolated_search_db):
        """doc_snippets 应存原始 content，snippet 截取前 200 字符"""
        engine = isolated_search_db()
        original_content = "上游不可达导致 502 Bad Gateway 错误。常见原因是后端进程崩溃或网络中断。"
        engine.index_document(
            "doc-1",
            "Nginx 502",
            original_content,
            "md",
        )

        results = engine.search("Nginx", limit=5)
        assert results
        # snippet 应来自原始 content（包含未分词的文本）
        assert "Bad Gateway" in results[0]["snippet"]
        # 不应包含分词后的破碎形式（如 "Bad Gateway" 变成 "bad gateway" 单独 token）
        # 注意：snippet 是从原始 content 截取，应保持原文

    def test_index_overwrites_existing(self, isolated_search_db):
        """对同一 doc_id 重复 index_document 应覆盖旧内容"""
        engine = isolated_search_db()
        engine.index_document("doc-1", "旧标题", "旧内容", "md")
        engine.index_document("doc-1", "新标题", "新内容包含 nginx", "md")

        results = engine.search("nginx", limit=5)
        assert len(results) == 1
        assert results[0]["title"] == "新标题"
        assert "新内容" in results[0]["snippet"]

    def test_index_with_empty_content(self, isolated_search_db):
        """空 content 应能正常索引（不报错）"""
        engine = isolated_search_db()
        engine.index_document("doc-1", "Nginx 标题", "", "md")

        results = engine.search("nginx", limit=5)
        assert results
        assert results[0]["snippet"] == ""

    def test_index_with_none_content(self, isolated_search_db):
        """None content 应被当作空处理"""
        engine = isolated_search_db()
        engine.index_document("doc-1", "Nginx 标题", None, "md")  # type: ignore[arg-type]

        results = engine.search("nginx", limit=5)
        assert results

    def test_index_long_content_truncated(self, isolated_search_db):
        """超长 content 应被截断到 50000 字符（避免 FTS5 索引过大）"""
        engine = isolated_search_db()
        long_content = "Nginx 故障 " * 10000  # 远超 50000
        engine.index_document("doc-1", "Nginx", long_content, "md")

        results = engine.search("nginx", limit=5)
        assert results


# ═══════════════ 中文分词召回（核心）═══════════════


class TestChineseTokenization:
    """P2-1.5 核心：中文 query 应能召回中文文档

    动机：whitespace 模式下，FTS5 unicode61 会把 "Nginx故障排查" 当作
    一个完整 token 索引；用户搜索 "故障" 时无法匹配。
    jieba 双端预分词后，index 与 query 都切分为 ["nginx", "故障", "排查"]，
    FTS5 即可正确匹配。
    """

    def test_chinese_query_recalls_chinese_doc(self, isolated_search_db):
        """中文 query 应召回含中文 token 的文档"""
        engine = isolated_search_db()
        engine.index_document(
            "doc-1",
            "Nginx 502 故障排查指南",
            "当上游服务不可达时返回 502 Bad Gateway",
            "md",
        )

        # "故障" 是 jieba 切分后的独立 token，应能匹配
        results = engine.search("故障", limit=5)
        assert results
        assert results[0]["doc_id"] == "doc-1"

    def test_chinese_query_no_space(self, isolated_search_db):
        """无空格的中文 query 应能召回（jieba 切分）"""
        engine = isolated_search_db()
        engine.index_document(
            "doc-1",
            "反向代理服务器配置",
            "Nginx 作为反向代理，转发请求到上游服务",
            "md",
        )

        # query 不带空格，jieba 仍能切分
        results = engine.search("反向代理", limit=5)
        assert results
        assert results[0]["doc_id"] == "doc-1"

    def test_mixed_cn_en_query(self, isolated_search_db):
        """中英混合 query 应能召回"""
        engine = isolated_search_db()
        engine.index_document(
            "doc-1",
            "Nginx 反向代理配置",
            "负载均衡与高可用",
            "md",
        )

        results = engine.search("Nginx 代理", limit=5)
        assert results
        assert results[0]["doc_id"] == "doc-1"

    def test_multiple_chinese_docs_ranked_by_relevance(self, isolated_search_db):
        """多中文文档查询：相关文档应被召回（BM25 排序由 SQLite 保证）"""
        engine = isolated_search_db()
        # doc-1 多次出现 "故障"
        engine.index_document(
            "doc-1",
            "Nginx 故障排查",
            "故障 故障 故障 故障排查步骤",
            "md",
        )
        engine.index_document(
            "doc-2",
            "常规巡检",
            "日常巡检不涉及故障处理",
            "md",
        )
        # 不相关文档
        engine.index_document(
            "doc-3",
            "Redis 缓存",
            "缓存命中与穿透",
            "md",
        )

        results = engine.search("故障", limit=5)
        # 相关文档（doc-1, doc-2）应被召回，doc-3 不应出现
        recalled_ids = {r["doc_id"] for r in results}
        assert "doc-1" in recalled_ids
        assert "doc-2" in recalled_ids
        assert "doc-3" not in recalled_ids

    def test_numeric_code_preserved(self, isolated_search_db):
        """数字错误码（如 502）应能独立匹配"""
        engine = isolated_search_db()
        engine.index_document(
            "doc-1",
            "Nginx 502",
            "502 Bad Gateway 错误",
            "md",
        )
        engine.index_document(
            "doc-2",
            "Nginx 503",
            "503 Service Unavailable",
            "md",
        )

        results_502 = engine.search("502", limit=5)
        assert results_502
        assert results_502[0]["doc_id"] == "doc-1"

        results_503 = engine.search("503", limit=5)
        assert results_503
        assert results_503[0]["doc_id"] == "doc-2"


# ═══════════════ snippet/title 展示 ═══════════════


class TestSnippetDisplay:
    def test_snippet_is_original_not_tokenized(self, isolated_search_db):
        """snippet 应为原始 content（未分词），不出现破碎形式"""
        engine = isolated_search_db()
        original = "Nginx 返回 502 Bad Gateway 错误。"
        engine.index_document("doc-1", "Nginx 502", original, "md")

        results = engine.search("nginx", limit=5)
        assert results
        # snippet 是原始 content 截取，应保持原文（含大小写、标点）
        assert results[0]["snippet"] == original[:200]

    def test_snippet_truncated_to_200_chars(self, isolated_search_db):
        """snippet 应截取前 200 字符"""
        engine = isolated_search_db()
        long_content = "Nginx 故障。 " * 50  # 远超 200 字符
        engine.index_document("doc-1", "Nginx", long_content, "md")

        results = engine.search("nginx", limit=5)
        assert results
        assert len(results[0]["snippet"]) <= 200

    def test_title_is_original_not_tokenized(self, isolated_search_db):
        """title 应为原始标题（未分词）"""
        engine = isolated_search_db()
        title = "Nginx 反向代理故障排查"
        engine.index_document("doc-1", title, "内容", "md")

        results = engine.search("nginx", limit=5)
        assert results
        assert results[0]["title"] == title
        # 不应是分词后的形式
        assert results[0]["title"] != "nginx 反向 代理 故障 排查"


# ═══════════════ remove_index ═══════════════


class TestRemoveIndex:
    def test_remove_index_clears_fts(self, isolated_search_db):
        """remove_index 应清除 FTS 索引"""
        engine = isolated_search_db()
        engine.index_document("doc-1", "Nginx", "内容", "md")
        assert engine.search("nginx", limit=5)

        engine.remove_index("doc-1")
        assert not engine.search("nginx", limit=5)

    def test_remove_index_clears_snippets(self, isolated_search_db):
        """remove_index 应同步清除 doc_snippets"""
        engine = isolated_search_db()
        engine.index_document("doc-1", "Nginx 故障", "内容", "md")
        engine.remove_index("doc-1")

        # 重新索引一个不相关的 doc，确认 doc-1 不再召回
        engine.index_document("doc-2", "Redis 缓存", "内容", "md")
        results = engine.search("nginx", limit=5)
        assert not results

    def test_remove_nonexistent_index_noop(self, isolated_search_db):
        """删除不存在的 doc_id 应不报错"""
        engine = isolated_search_db()
        engine.remove_index("nonexistent")  # 不应抛异常


# ═══════════════ 边界场景 ═══════════════


class TestEdgeCases:
    def test_empty_query_returns_empty(self, isolated_search_db):
        """空 query 应返回空结果"""
        engine = isolated_search_db()
        engine.index_document("doc-1", "Nginx", "内容", "md")
        assert engine.search("", limit=5) == []

    def test_whitespace_query_returns_empty(self, isolated_search_db):
        """纯空白 query 应返回空结果"""
        engine = isolated_search_db()
        engine.index_document("doc-1", "Nginx", "内容", "md")
        assert engine.search("   ", limit=5) == []

    def test_stopword_only_query_returns_empty(self, isolated_search_db):
        """纯停用词 query 应返回空结果（tokenize 过滤后无 token）"""
        engine = isolated_search_db()
        engine.index_document("doc-1", "Nginx 故障", "内容", "md")
        # 中文停用词
        assert engine.search("的 了", limit=5) == []
        # 英文停用词
        assert engine.search("the a is", limit=5) == []

    def test_no_match_returns_empty(self, isolated_search_db):
        """无匹配应返回空列表"""
        engine = isolated_search_db()
        engine.index_document("doc-1", "Nginx", "内容", "md")
        assert engine.search("nonexistentkeyword", limit=5) == []

    def test_limit_respected(self, isolated_search_db):
        """limit 参数应被尊重"""
        engine = isolated_search_db()
        for i in range(10):
            engine.index_document(f"doc-{i}", f"Nginx {i}", "故障", "md")

        results = engine.search("nginx", limit=3)
        assert len(results) <= 3


# ═══════════════ get_stats ═══════════════


class TestGetStats:
    def test_stats_empty(self, isolated_search_db):
        """空库统计"""
        engine = isolated_search_db()
        stats = engine.get_stats()
        assert stats["indexed_docs"] == 0
        assert stats["vectorized_docs"] == 0
        assert "numpy_enabled" in stats

    def test_stats_after_indexing(self, isolated_search_db):
        """索引后统计正确"""
        engine = isolated_search_db()
        engine.index_document("doc-1", "Nginx", "内容", "md")
        engine.index_document("doc-2", "Redis", "内容", "md")

        stats = engine.get_stats()
        assert stats["indexed_docs"] == 2
        assert stats["vectorized_docs"] == 0  # 未提供 embedding

    def test_stats_with_embedding(self, isolated_search_db):
        """带 embedding 索引后 vectorized_docs 应计数"""
        engine = isolated_search_db()
        engine.index_document(
            "doc-1", "Nginx", "内容", "md", embedding=[0.1, 0.2, 0.3]
        )

        stats = engine.get_stats()
        assert stats["indexed_docs"] == 1
        assert stats["vectorized_docs"] == 1


# ═══════════════ 向量检索融合 ═══════════════


class TestVectorSearch:
    def test_vector_only_search(self, isolated_search_db):
        """仅向量检索（无关键词匹配）"""
        engine = isolated_search_db()
        engine.index_document(
            "doc-1",
            "Nginx",
            "内容",
            "md",
            embedding=[1.0, 0.0, 0.0],
        )

        # query_embedding 与 doc 完全相同 → cosine=1.0
        results = engine.search(
            "nonexistent",  # 关键词不匹配
            limit=5,
            query_embedding=[1.0, 0.0, 0.0],
        )
        assert results
        assert results[0]["doc_id"] == "doc-1"
        assert results[0]["vector_score"] > 0

    def test_rrf_fusion(self, isolated_search_db):
        """RRF 融合：keyword + vector 双路命中"""
        engine = isolated_search_db()
        engine.index_document(
            "doc-1",
            "Nginx 故障",
            "故障内容",
            "md",
            embedding=[1.0, 0.0],
        )

        results = engine.search(
            "故障",
            limit=5,
            query_embedding=[1.0, 0.0],
            fusion="rrf",
        )
        assert results
        assert results[0]["doc_id"] == "doc-1"
        assert results[0]["fusion"] == "rrf"
        # 双路命中，rrf_score 应较高
        assert results[0]["combined_score"] > 0


# ═══════════════ 配置驱动 ═══════════════


class TestConfigDrivenMode:
    def test_whitespace_mode_does_not_split_chinese(self, isolated_search_db, monkeypatch):
        """whitespace 模式下，纯中文 query 应无法召回中文索引

        这正是 P2-1.5 引入 jieba 的动机：whitespace 无法切中文。
        """
        from app.config import get_settings

        # 强制 whitespace 模式
        settings = get_settings()
        monkeypatch.setattr(settings, "search_tokenizer", "whitespace")

        engine = isolated_search_db()
        # 索引一份纯中文文档（无空格分隔）
        engine.index_document(
            "doc-1",
            "Nginx故障排查",  # 无空格的中文
            "反向代理服务器配置",
            "md",
        )

        # whitespace 模式下，"故障" 与索引的 "Nginx故障排查" 不匹配
        # （前者 tokenize 后是 ["故障"]，后者是 ["nginx故障排查"]）
        results = engine.search("故障", limit=5)
        assert results == [], (
            "whitespace 模式下，纯中文 query 不应召回无空格的中文索引。"
            f"实际: {results}"
        )

    def test_jieba_mode_splits_chinese_for_recall(self, isolated_search_db, monkeypatch):
        """jieba 模式下，纯中文 query 应能召回中文索引"""
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "search_tokenizer", "jieba")

        engine = isolated_search_db()
        engine.index_document(
            "doc-1",
            "Nginx故障排查",
            "反向代理服务器配置",
            "md",
        )

        results = engine.search("故障", limit=5)
        assert results, "jieba 模式下，中文 query 应能召回"
        assert results[0]["doc_id"] == "doc-1"
