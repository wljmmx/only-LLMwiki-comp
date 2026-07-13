"""搜索分词器单元测试（P2-1.5）

覆盖：
- jieba/whitespace 两种模式的分词行为
- 停用词/标点/空白过滤
- 空输入处理
- tokenize_to_string 拼接
- 双端一致性（index 侧与 query 侧使用同一切分逻辑）
- 降级：jieba 不可用时回退到 whitespace
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.search.tokenizer import (
    _is_meaningful,
    _tokenize_jieba,
    _tokenize_whitespace,
    tokenize,
    tokenize_to_string,
)

# ═══════════════ _is_meaningful ═══════════════


class TestIsMeaningful:
    def test_empty_string(self):
        assert _is_meaningful("") is False

    def test_whitespace_only(self):
        assert _is_meaningful(" ") is False
        assert _is_meaningful("   ") is False
        assert _is_meaningful("\t\n") is False

    def test_punctuation_only(self):
        assert _is_meaningful("。") is False
        assert _is_meaningful("，") is False
        assert _is_meaningful("!") is False
        assert _is_meaningful("?") is False
        assert _is_meaningful("...") is False
        assert _is_meaningful("---") is False
        assert _is_meaningful("___") is False

    def test_chinese_stopword(self):
        assert _is_meaningful("的") is False
        assert _is_meaningful("了") is False
        assert _is_meaningful("在") is False
        assert _is_meaningful("是") is False

    def test_english_stopword(self):
        assert _is_meaningful("the") is False
        assert _is_meaningful("a") is False
        assert _is_meaningful("is") is False
        assert _is_meaningful("of") is False

    def test_meaningful_token(self):
        assert _is_meaningful("nginx") is True
        assert _is_meaningful("502") is True
        assert _is_meaningful("故障") is True
        assert _is_meaningful("Nginx") is True
        # 含字母+连字符的复合词仍视为有意义（regex 仅过滤纯标点）
        assert _is_meaningful("reverse-proxy") is True
        assert _is_meaningful("-") is False  # 纯连字符无意义


# ═══════════════ tokenize 基础行为 ═══════════════


class TestTokenizeBasic:
    def test_empty_input(self):
        assert tokenize("") == []
        assert tokenize("", mode="jieba") == []
        assert tokenize("", mode="whitespace") == []

    def test_whitespace_only(self):
        assert tokenize("   ") == []
        assert tokenize("\t\n") == []

    def test_punctuation_only(self):
        # 纯标点应被全部过滤
        assert tokenize("。。！？") == []
        assert tokenize("!!! ???") == []

    def test_stopword_only(self):
        # 纯停用词应被全部过滤
        assert tokenize("的 了 在 是") == []
        assert tokenize("the a is of") == []


# ═══════════════ jieba 模式 ═══════════════


class TestTokenizeJieba:
    def test_mixed_cn_en(self):
        """中英混合：jieba 应切出中文词 + 英文词"""
        tokens = tokenize("Nginx 502 故障排查", mode="jieba")
        # jieba 输出小写化
        assert "nginx" in tokens
        assert "502" in tokens
        assert "故障" in tokens
        assert "排查" in tokens

    def test_chinese_segmentation(self):
        """纯中文：jieba 应切出语义词（而非整段）"""
        tokens = tokenize("反向代理服务器配置", mode="jieba")
        # 应至少切出"反向"/"代理"/"服务器" 等子词
        assert "反向" in tokens or "反向代理" in tokens
        assert "代理" in tokens
        assert "服务器" in tokens

    def test_jieba_lowercase(self):
        """jieba 输出应小写化（保证大小写不敏感匹配）"""
        tokens = tokenize("NGINX Redis MySQL", mode="jieba")
        assert "nginx" in tokens
        assert "redis" in tokens
        assert "mysql" in tokens
        # 不应出现大写形式
        assert not any(t.isupper() for t in tokens if t.isalpha())

    def test_jieba_filters_stopwords(self):
        """jieba 切分后应过滤中文停用词"""
        tokens = tokenize("我在的服务器", mode="jieba")
        assert "我" not in tokens
        assert "在" not in tokens
        assert "的" not in tokens
        assert "服务器" in tokens

    def test_jieba_preserves_numeric_codes(self):
        """数字错误码（高区分度）应保留"""
        tokens = tokenize("HTTP 502 503 504 错误", mode="jieba")
        assert "502" in tokens
        assert "503" in tokens
        assert "504" in tokens

    def test_jieba_search_mode_finer_granularity(self):
        """cut_for_search 比 cut 更细，适合搜索召回"""
        # "反向代理服务器" 在 search 模式下应同时切出 "反向" 和 "反向代理"
        tokens = tokenize("反向代理服务器", mode="jieba")
        # search 模式会输出更细的子词组合
        assert "服务器" in tokens
        # 至少包含 "反向" 或 "代理" 之一
        assert "反向" in tokens or "代理" in tokens

    def test_jieba_punctuation_filtered(self):
        """jieba 输出的标点应被过滤"""
        tokens = tokenize("Nginx, 故障; 排查。", mode="jieba")
        assert "," not in tokens
        assert ";" not in tokens
        assert "。" not in tokens
        assert "nginx" in tokens
        assert "故障" in tokens
        assert "排查" in tokens


# ═══════════════ whitespace 模式 ═══════════════


class TestTokenizeWhitespace:
    def test_whitespace_splits_by_space(self):
        """whitespace 模式按空格切分"""
        tokens = tokenize("nginx redis mysql", mode="whitespace")
        assert tokens == ["nginx", "redis", "mysql"]

    def test_whitespace_does_not_split_chinese(self):
        """whitespace 模式不会切分中文（整段保留）"""
        # 这正是引入 jieba 的动机：whitespace 无法切中文
        tokens = tokenize("Nginx故障排查", mode="whitespace")
        assert tokens == ["nginx故障排查"]  # 整段未被切

    def test_whitespace_lowercase(self):
        """whitespace 输出应小写化"""
        tokens = tokenize("NGINX REDIS", mode="whitespace")
        assert tokens == ["nginx", "redis"]

    def test_whitespace_filters_stopwords(self):
        """whitespace 模式也过滤停用词"""
        tokens = tokenize("the nginx is working", mode="whitespace")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "nginx" in tokens
        assert "working" in tokens

    def test_whitespace_collapses_multiple_spaces(self):
        """多个空格应被正确处理（不产生空 token）"""
        tokens = tokenize("nginx   redis", mode="whitespace")
        assert tokens == ["nginx", "redis"]

    def test_whitespace_preserves_numeric_codes(self):
        """数字错误码应保留"""
        tokens = tokenize("HTTP 502 503", mode="whitespace")
        assert "502" in tokens
        assert "503" in tokens


# ═══════════════ 双模式对比 ═══════════════


class TestModeComparison:
    def test_jieba_better_recall_on_chinese(self):
        """对纯中文文本，jieba 召回粒度优于 whitespace"""
        text = "反向代理负载均衡"
        jieba_tokens = set(tokenize(text, mode="jieba"))
        ws_tokens = set(tokenize(text, mode="whitespace"))

        # jieba 应切出多个 token，whitespace 只有一个
        assert len(jieba_tokens) > 1, f"jieba 应切分中文，实际: {jieba_tokens}"
        assert len(ws_tokens) == 1, f"whitespace 不切中文，实际: {ws_tokens}"

    def test_same_result_on_pure_english(self):
        """对纯英文（空格分隔），两种模式结果一致"""
        text = "nginx redis mysql"
        assert tokenize(text, mode="jieba") == tokenize(text, mode="whitespace")


# ═══════════════ tokenize_to_string ═══════════════


class TestTokenizeToString:
    def test_basic_join(self):
        s = tokenize_to_string("Nginx 502 故障", mode="jieba")
        # 应为空格分隔的小写 token
        assert "nginx" in s
        assert "502" in s
        assert "故障" in s
        assert " " in s  # 含分隔空格

    def test_empty_input(self):
        assert tokenize_to_string("") == ""
        assert tokenize_to_string("   ") == ""

    def test_stopword_only_input(self):
        """纯停用词输入应返回空字符串"""
        assert tokenize_to_string("的 了") == ""
        assert tokenize_to_string("the a is") == ""

    def test_whitespace_mode(self):
        s = tokenize_to_string("Nginx Redis", mode="whitespace")
        assert s == "nginx redis"

    def test_consistent_with_tokenize(self):
        """tokenize_to_string 与 tokenize 应保持一致"""
        text = "Nginx 502 故障排查"
        tokens = tokenize(text, mode="jieba")
        s = tokenize_to_string(text, mode="jieba")
        assert s == " ".join(tokens)


# ═══════════════ 双端一致性（index vs query）═══════════════


class TestDualEndConsistency:
    """P2-1.5 核心：index 写入与 query 查询必须使用同一分词逻辑

    否则 FTS5 MATCH 召回失效。此测试用例模拟该一致性。
    """

    def test_index_and_query_use_same_tokens(self):
        """同一文本，index 侧 tokenize_to_string 与 query 侧 tokenize 应对齐"""
        doc_text = "Nginx 502 故障排查"
        query_text = "Nginx 故障"

        # index 侧：整段分词后写入
        indexed = tokenize_to_string(doc_text, mode="jieba")
        # query 侧：query 分词后用于 MATCH
        query_tokens = tokenize(query_text, mode="jieba")

        # query tokens 必须全部出现在 indexed 中（否则 MATCH 失败）
        indexed_token_set = set(indexed.split())
        for qt in query_tokens:
            assert qt in indexed_token_set, (
                f"query token {qt!r} 未出现在 index 中，FTS5 MATCH 将失效。"
                f"indexed={indexed!r}, query_tokens={query_tokens}"
            )

    def test_chinese_query_matches_chinese_index(self):
        """中文 query 应能匹配中文 index（jieba 切分后 token 集合相交）"""
        doc_text = "反向代理服务器负载均衡配置"
        query_text = "负载均衡"

        indexed_tokens = set(tokenize_to_string(doc_text, mode="jieba").split())
        query_tokens = set(tokenize(query_text, mode="jieba"))

        # 至少有一个 query token 出现在 index 中
        common = indexed_tokens & query_tokens
        assert common, (
            f"query 与 index 无共同 token，召回失败。"
            f"indexed={indexed_tokens}, query={query_tokens}"
        )


# ═══════════════ 降级场景 ═══════════════


class TestFallback:
    def test_jieba_falls_back_to_whitespace_when_unavailable(self, monkeypatch):
        """jieba 不可用时，_tokenize_jieba 应回退到 _tokenize_whitespace"""
        from app.search import tokenizer as tk

        # 模拟 jieba 不可用：清缓存 + 让 _get_jieba 返回 None
        monkeypatch.setattr(tk, "_get_jieba", lambda: None)

        result = _tokenize_jieba("nginx redis")
        expected = _tokenize_whitespace("nginx redis")
        assert result == expected

    def test_get_jieba_caches(self, monkeypatch):
        """_get_jieba 应使用 lru_cache（重复调用不重复加载）"""
        from app.search import tokenizer as tk

        # 清缓存
        tk._get_jieba.cache_clear()
        j1 = tk._get_jieba()
        j2 = tk._get_jieba()
        assert j1 is j2  # 同一对象引用（缓存命中）

        # 清理：避免影响后续测试
        tk._get_jieba.cache_clear()


# ═══════════════ 边界场景 ═══════════════


class TestEdgeCases:
    def test_very_long_text(self):
        """超长文本应能正常处理（不报错）"""
        long_text = "Nginx 故障 " * 1000
        tokens = tokenize(long_text, mode="jieba")
        assert len(tokens) > 0
        assert "nginx" in tokens
        assert "故障" in tokens

    def test_mixed_with_newlines(self):
        """含换行的文本应能正确处理"""
        text = "Nginx\n故障\n排查"
        tokens = tokenize(text, mode="jieba")
        assert "nginx" in tokens
        assert "故障" in tokens
        assert "排查" in tokens

    def test_only_numbers(self):
        """纯数字应保留"""
        tokens = tokenize("502 503 504", mode="jieba")
        assert "502" in tokens
        assert "503" in tokens
        assert "504" in tokens

    def test_special_chars_mixed_with_text(self):
        """特殊字符与文本混合，应过滤特殊字符保留文本"""
        text = "Nginx@502#故障"
        tokens = tokenize(text, mode="jieba")
        # 至少 nginx 或 502 或 故障 之一应被保留
        meaningful = [t for t in tokens if t.isalnum()]
        assert len(meaningful) > 0

    def test_default_mode_is_jieba(self):
        """tokenize 默认 mode 应为 jieba"""
        # 通过对比：jieba 切中文，whitespace 不切
        text = "反向代理"
        default_tokens = tokenize(text)  # 不指定 mode
        jieba_tokens = tokenize(text, mode="jieba")
        assert default_tokens == jieba_tokens

    def test_none_safe(self):
        """None 输入应被当作空处理（不报错）"""
        # tokenize 第一行检查 `if not text`，None 会被视为 falsy
        assert tokenize(None) == []  # type: ignore[arg-type]
