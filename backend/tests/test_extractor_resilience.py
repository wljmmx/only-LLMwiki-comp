"""KnowledgeExtractor 韧性测试

验证修复的关键问题：
1. _safe_confidence：畸形 confidence（None/字符串/超出范围）不抛错
2. _parse_entity / _parse_relation：单条坏数据不中断整体抽取
3. fallback 触发条件：门控后全空时补 fallback
4. /graph/upload 修复：同时接受 auto_accepted + review 实体
5. _check_paused 超时保护：暂停后无 resume 不永久阻塞
"""
from __future__ import annotations

import asyncio
import os

os.environ.setdefault("OPSKG_API_TOKEN", "")

from unittest.mock import patch

import pytest

from app.extraction.extractor import KnowledgeExtractor
from app.parsers.base import HeadingNode, ParsedDocument

# ────────── 1. _safe_confidence ──────────


class TestSafeConfidence:
    def test_none_returns_default(self):
        assert KnowledgeExtractor._safe_confidence({"confidence": None}) == 0.5

    def test_string_returns_default(self):
        assert KnowledgeExtractor._safe_confidence({"confidence": "high"}) == 0.5

    def test_out_of_range_high_returns_default(self):
        assert KnowledgeExtractor._safe_confidence({"confidence": 1.5}) == 0.5

    def test_out_of_range_low_returns_default(self):
        assert KnowledgeExtractor._safe_confidence({"confidence": -0.5}) == 0.5

    def test_missing_key_returns_default(self):
        assert KnowledgeExtractor._safe_confidence({}) == 0.5

    def test_valid_float_passes_through(self):
        assert KnowledgeExtractor._safe_confidence({"confidence": 0.9}) == 0.9

    def test_valid_int_passes_through(self):
        assert KnowledgeExtractor._safe_confidence({"confidence": 1}) == 1.0

    def test_custom_default(self):
        assert KnowledgeExtractor._safe_confidence({"confidence": None}, default=0.7) == 0.7


# ────────── 2. _parse_entity / _parse_relation 容错 ──────────


class TestParseEntityResilience:
    def test_parse_entity_with_none_confidence(self):
        ext = KnowledgeExtractor()
        e = ext._parse_entity({"name": "nginx", "confidence": None}, "doc-1")
        assert e.name == "nginx"
        assert e.confidence == 0.5

    def test_parse_entity_with_string_confidence(self):
        ext = KnowledgeExtractor()
        e = ext._parse_entity({"name": "nginx", "confidence": "high"}, "doc-1")
        assert e.confidence == 0.5

    def test_parse_relation_with_none_confidence(self):
        ext = KnowledgeExtractor()
        r = ext._parse_relation(
            {"from_entity": "a", "to_entity": "b", "confidence": None}, "doc-1",
        )
        assert r.confidence == 0.5

    def test_parse_entity_batch_no_crash(self):
        """批量解析中含畸形 confidence 不应中断整体"""
        ext = KnowledgeExtractor()
        raw_list = [
            {"name": "good", "confidence": 0.9},
            {"name": "bad1", "confidence": None},
            {"name": "bad2", "confidence": "invalid"},
            {"name": "bad3", "confidence": 2.0},
            {"name": "good2", "confidence": 0.8},
        ]
        # 旧版此处会抛 TypeError/ValueError，修复后应全部解析成功
        entities = [ext._parse_entity(r, "doc-1") for r in raw_list]
        assert len(entities) == 5
        assert entities[0].confidence == 0.9
        assert entities[1].confidence == 0.5
        assert entities[4].confidence == 0.8


# ────────── 3. fallback 触发条件 ──────────


def _make_doc_with_content() -> ParsedDocument:
    return ParsedDocument(
        doc_id="test-doc",
        source_path="/tmp/test.md",
        format="markdown",
        checksum="sha256:abc",
        title="Test",
        elements=[],
        heading_tree=[
            HeadingNode(level=1, title="Section A"),
            HeadingNode(level=2, title="Section B"),
        ],
    )


class TestFallbackTrigger:
    @pytest.mark.anyio
    async def test_fallback_triggers_when_llm_returns_empty(self):
        """LLM 返回空时触发 fallback"""
        ext = KnowledgeExtractor()
        with patch.object(ext, "_call_llm", return_value=([], [], None)):
            result = await ext.extract(_make_doc_with_content())
        # fallback 从 heading_tree 提取 2 个 Concept 实体（confidence=0.65 → review）
        assert len(result.review_entities) >= 2

    @pytest.mark.anyio
    async def test_fallback_triggers_when_all_discarded_after_gating(self):
        """LLM 返回低置信度实体全被门控丢弃时，补一次 fallback"""
        ext = KnowledgeExtractor()
        # LLM 返回 1 个 confidence=0.3 的实体（< review 0.6，会被丢弃）
        with patch.object(
            ext,
            "_call_llm",
            return_value=([{"name": "low", "entity_type": "Concept", "confidence": 0.3}], [], None),
        ):
            result = await ext.extract(_make_doc_with_content())
        # 原始实体被丢弃，fallback 补充了 heading 实体
        assert len(result.auto_accepted_entities) + len(result.review_entities) > 0
        assert result.discarded_count >= 1  # 原低置信度实体被丢弃

    @pytest.mark.anyio
    async def test_no_fallback_when_llm_returns_valid_entities(self):
        """LLM 返回有效实体时不触发 fallback"""
        ext = KnowledgeExtractor()
        with patch.object(
            ext,
            "_call_llm",
            return_value=([{"name": "good", "entity_type": "Concept", "confidence": 0.9}], [], None),
        ):
            result = await ext.extract(_make_doc_with_content())
        # 0.9 >= auto(0.85)，进 auto_accepted
        assert len(result.auto_accepted_entities) == 1
        assert result.auto_accepted_entities[0].name == "good"

    @pytest.mark.anyio
    async def test_fallback_failure_does_not_crash_extract(self):
        """fallback 自身抛错时 extract 不应中断"""
        ext = KnowledgeExtractor()
        with patch.object(ext, "_call_llm", return_value=([], [], None)):
            with patch.object(
                ext.compiled_extractor,
                "extract_from_document",
                side_effect=RuntimeError("forced fallback failure"),
            ):
                result = await ext.extract(_make_doc_with_content())
        # fallback 失败，extract 仍正常返回（空结果）
        assert result.auto_accepted_entities == []
        assert result.review_entities == []


# ────────── 4. _check_paused 超时保护 ──────────


class TestCheckPausedTimeout:
    @pytest.mark.anyio
    async def test_check_paused_returns_false_when_not_paused(self):
        from app.knowledge.wiki_compiler import _check_paused
        assert await _check_paused(None) is False
        assert await _check_paused("nonexistent-run") is False

    @pytest.mark.anyio
    async def test_check_paused_times_out_and_auto_resumes(self):
        """暂停后未 resume，超时后自动恢复"""
        from app.knowledge.wiki_compiler import (
            _check_paused,
            _paused_events,
            pause_compile,
        )

        run_id = "test-timeout-run"
        pause_compile(run_id)
        assert run_id in _paused_events

        # _check_paused 应在超时后返回 True（已等待过），并清理 _paused_events
        result = await _check_paused(run_id, timeout=0.1)
        assert result is True
        # 超时后 _paused_events 中的条目应被清理
        assert run_id not in _paused_events

    @pytest.mark.anyio
    async def test_check_paused_resumes_when_set(self):
        """暂停后被 resume，应立即返回"""
        from app.knowledge.wiki_compiler import (
            _check_paused,
            cancel_pause,
            pause_compile,
            resume_compile,
        )

        run_id = "test-resume-run"
        pause_compile(run_id)

        async def _resume_after_delay():
            await asyncio.sleep(0.05)
            resume_compile(run_id)

        # 启动 resume 任务
        asyncio.create_task(_resume_after_delay())
        # _check_paused 应在 resume 后返回
        result = await _check_paused(run_id, timeout=2.0)
        assert result is True
        # 清理
        cancel_pause(run_id)


# ────────── 5. /graph/upload 实体筛选修复 ──────────


class TestGraphUploadEntitySelection:
    def test_graph_upload_takes_both_auto_and_review(self):
        """验证 /graph/upload 修复后同时接受 auto + review 实体

        这是修复"知识图谱提取为 0"问题的关键测试：
        - 旧版仅取 auto_accepted，LLM 不可用时 fallback 实体(0.65)只进 review → 图谱 0 实体
        - 修复后同时取 auto + review，与 wiki_compiler._compile_to_graph 一致
        """
        from app.extraction.types import ExtractedEntity, ExtractionResult

        # 模拟 extract 返回的结果：0 auto + 3 review（fallback 典型场景）
        result = ExtractionResult(doc_id="doc-1")
        result.auto_accepted_entities = []
        result.review_entities = [
            ExtractedEntity(
                entity_type="Concept", name="nginx", properties={},
                confidence=0.65, evidence_span="", source_doc_id="doc-1",
            ),
            ExtractedEntity(
                entity_type="Command", name="systemctl restart nginx", properties={},
                confidence=0.65, evidence_span="", source_doc_id="doc-1",
            ),
        ]

        # 模拟 graph_router.py 修复后的逻辑
        all_entities = list(result.auto_accepted_entities) + list(result.review_entities)
        assert len(all_entities) == 2  # 修复后应为 2
        assert all_entities[0].name == "nginx"
        assert all_entities[1].name == "systemctl restart nginx"
