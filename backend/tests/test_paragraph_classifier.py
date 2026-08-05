"""段落分类器单元测试

覆盖：
1. 关键词分类（降级方案）
2. LLM 响应解析（各种格式容错）
3. 分类结果转换
4. 批量 LLM 分类（mock LLM 客户端）
"""

from __future__ import annotations

import asyncio
import json

from app.parsers.paragraph_classifier import (
    KEYWORD_CLASSIFIERS,
    ClassifiedParagraph,
    _parse_llm_response,
    classify_paragraphs_with_llm,
    keyword_classify,
    to_cleaned_dict,
)

# ═══════════════ 关键词分类 ═══════════════


class TestKeywordClassify:
    def test_overview_detection(self):
        paragraphs = ["本文概述了系统架构和设计原则。"]
        results = keyword_classify(paragraphs)
        assert len(results) == 1
        assert results[0].class_name == "overview"
        assert results[0].confidence > 0

    def test_cause_detection(self):
        paragraphs = ["根本原因是数据库连接池配置不当。"]
        results = keyword_classify(paragraphs)
        assert len(results) == 1
        assert results[0].class_name == "cause"

    def test_solution_detection(self):
        paragraphs = ["解决方案是增加连接池大小并优化查询。"]
        results = keyword_classify(paragraphs)
        assert results[0].class_name == "solution"

    def test_steps_detection(self):
        paragraphs = ["操作步骤如下：1. 停止服务 2. 修改配置 3. 重启"]
        results = keyword_classify(paragraphs)
        assert results[0].class_name == "steps"

    def test_generic_fallback(self):
        paragraphs = ["这是一段没有关键词的普通文本内容。"]
        results = keyword_classify(paragraphs)
        assert results[0].class_name == "general"
        assert results[0].confidence == 0.3

    def test_empty_paragraphs(self):
        results = keyword_classify([])
        assert results == []

    def test_multiple_paragraphs(self):
        paragraphs = [
            "概述部分介绍了系统背景。",
            "原因分析：核心问题在于配置。",
            "解决方案：重新部署服务。",
        ]
        results = keyword_classify(paragraphs)
        assert len(results) == 3
        classes = {r.class_name for r in results}
        assert "overview" in classes
        assert "cause" in classes
        assert "solution" in classes

    def test_paragraph_indices(self):
        paragraphs = ["第一段。", "第二段。", "第三段。"]
        results = keyword_classify(paragraphs)
        assert results[0].para_idx == 0
        assert results[1].para_idx == 1
        assert results[2].para_idx == 2


# ═══════════════ LLM 响应解析 ═══════════════


class TestParseLLMResponse:
    def test_direct_json_array(self):
        text = '[{"index": 0, "class": "overview", "confidence": 0.95}]'
        result = _parse_llm_response(text)
        assert len(result) == 1
        assert result[0]["index"] == 0
        assert result[0]["class"] == "overview"

    def test_wrapped_json_code_block(self):
        text = '```json\n[{"index": 0, "class": "cause", "confidence": 0.8}]\n```'
        result = _parse_llm_response(text)
        assert len(result) == 1
        assert result[0]["class"] == "cause"

    def test_wrapped_code_block_no_lang(self):
        text = '```\n[{"index": 1, "class": "solution", "confidence": 0.9}]\n```'
        result = _parse_llm_response(text)
        assert len(result) == 1

    def test_json_object_with_results_key(self):
        text = '{"results": [{"index": 0, "class": "steps", "confidence": 0.85}]}'
        result = _parse_llm_response(text)
        assert len(result) == 1
        assert result[0]["class"] == "steps"

    def test_bracket_extraction_fallback(self):
        text = '分析结果：\n[{"index": 0, "class": "warning", "confidence": 0.7}]\n完成。'
        result = _parse_llm_response(text)
        assert len(result) == 1
        assert result[0]["class"] == "warning"

    def test_empty_text(self):
        result = _parse_llm_response("")
        assert result == []

    def test_invalid_json(self):
        result = _parse_llm_response("这不是有效的 JSON 数据")
        assert result == []


# ═══════════════ 结果转换 ═══════════════


class TestToCleanedDict:
    def test_format_matches_original(self):
        results = [
            ClassifiedParagraph(0, "概述文本", "overview", 0.9),
            ClassifiedParagraph(1, "原因文本", "cause", 0.8),
        ]
        output = to_cleaned_dict(results)
        assert len(output) == 2
        assert output[0]["para_idx"] == 0
        assert output[0]["text_preview"] == "概述文本"
        assert output[0]["class"] == "overview"
        assert output[0]["confidence"] == 0.9

    def test_sorted_by_para_idx(self):
        results = [
            ClassifiedParagraph(2, "第三段", "solution", 0.7),
            ClassifiedParagraph(0, "第一段", "overview", 0.9),
        ]
        output = to_cleaned_dict(results)
        assert output[0]["para_idx"] == 0
        assert output[1]["para_idx"] == 2


# ═══════════════ LLM 批量分类（mock） ═══════════════


class MockLLMClient:
    """Mock LLM 客户端，返回预设的分类结果"""

    def __init__(self, responses: dict[int, str] | None = None):
        self.responses = responses or {}
        self.call_count = 0

    async def chat(self, messages, **kwargs):
        self.call_count += 1
        # 从最后一条 user message 提取段落数量
        user_msg = messages[-1].content if messages else ""
        import re
        match = re.search(r'count=(\d+)', user_msg)
        count = int(match.group(1)) if match else 1

        # 生成分类结果
        batch_results = []
        for i in range(count):
            batch_results.append({
                "index": i,
                "class": ["overview", "cause", "analysis", "solution"][i % 4],
                "confidence": 0.9 - (i * 0.05),
            })

        from app.core.llm.base import LLMResponse
        return LLMResponse(
            text=json.dumps(batch_results),
            model="mock",
            prompt_tokens=100,
            completion_tokens=50,
        )


class MockFailingLLMClient(MockLLMClient):
    """始终失败的 Mock 客户端"""

    async def chat(self, messages, **kwargs):
        raise ConnectionError("LLM 服务不可用")


class TestClassifyParagraphsWithLLM:
    def test_single_batch(self):
        paragraphs = [
            "本文概述了系统的整体架构设计。",
            "主要原因是缓存命中率过低。",
            "通过增加缓存容量解决此问题。",
        ]
        client = MockLLMClient()
        results = asyncio.run(classify_paragraphs_with_llm(paragraphs, client))
        assert len(results) == 3
        assert results[0].class_name in KEYWORD_CLASSIFIERS or results[0].class_name == "general"
        assert all(isinstance(r, ClassifiedParagraph) for r in results)

    def test_batch_splitting(self):
        """测试分批处理：超过 BATCH_SIZE 的段落"""
        paragraphs = [f"第 {i} 段内容。" for i in range(25)]
        client = MockLLMClient()
        results = asyncio.run(classify_paragraphs_with_llm(paragraphs, client, batch_size=10))
        assert len(results) == 25
        assert client.call_count == 3  # 25 / 10 = 3 批

    def test_llm_failure_fallback(self):
        """LLM 失败时降级到关键词分类"""
        paragraphs = ["本文概述了系统背景。", "根本原因是配置错误。"]
        client = MockFailingLLMClient()
        results = asyncio.run(classify_paragraphs_with_llm(paragraphs, client))
        assert len(results) == 2
        # 降级到关键词分类
        assert results[0].class_name == "overview"
        assert results[1].class_name == "cause"

    def test_empty_paragraphs(self):
        client = MockLLMClient()
        results = asyncio.run(classify_paragraphs_with_llm([], client))
        assert results == []

    def test_all_empty_paragraphs_filtered(self):
        paragraphs = ["", "   ", "\n\n"]
        client = MockLLMClient()
        results = asyncio.run(classify_paragraphs_with_llm(paragraphs, client))
        assert results == []

    def test_unknown_class_becomes_general(self):
        """LLM 返回未知类别时，应降级为 general"""
        from app.core.llm.base import LLMResponse

        class WeirdClient(MockLLMClient):
            async def chat(self, messages, **kwargs):
                return LLMResponse(
                    text='[{"index": 0, "class": "exotic_category", "confidence": 0.5}]',
                    model="mock",
                )

        client = WeirdClient()
        results = asyncio.run(classify_paragraphs_with_llm(["测试段落"], client))
        assert results[0].class_name == "general"

    def test_missing_indices_filled_by_keyword(self):
        """LLM 返回部分索引时，缺失的由关键词补充"""
        from app.core.llm.base import LLMResponse

        class PartialClient(MockLLMClient):
            async def chat(self, messages, **kwargs):
                return LLMResponse(
                    text='[{"index": 0, "class": "overview", "confidence": 0.9}]',
                    model="mock",
                )

        paragraphs = ["第一段概述。", "第二段原因。"]
        client = PartialClient()
        results = asyncio.run(classify_paragraphs_with_llm(paragraphs, client))
        assert len(results) == 2
        assert results[0].class_name == "overview"
        # 第二段由关键词补充
        assert results[1].class_name in KEYWORD_CLASSIFIERS or results[1].class_name == "general"


# ═══════════════ 关键词表完整性 ═══════════════


class TestKeywordCompleteness:
    def test_all_categories_have_keywords(self):
        expected = {"overview", "cause", "analysis", "solution", "config",
                     "steps", "example", "warning", "reference"}
        actual = set(KEYWORD_CLASSIFIERS.keys())
        assert actual == expected

    def test_no_empty_keyword_lists(self):
        for cls_name, keywords in KEYWORD_CLASSIFIERS.items():
            assert len(keywords) > 0, f"{cls_name} has no keywords"
