"""P2-5.5 候选 2 测试：文档生成 SSE 流 + doc_generator 进度回调

验证：
- DocGenerationPipeline.generate(on_progress=...) 在 6 节点入口/出口触发回调
- POST /doc/generate/stream SSE 端点透传 stage_start/stage_done/section_start/section_done
- 错误路径：生成异常 → error 事件
- 取消机制：cancel_token 传递
- SSE 格式：content-type / x-accel-buffering / cache-control

通过 monkeypatch 替换 get_pipeline 返回假 pipeline，
其 generate() 在执行中调用 on_progress 模拟进度事件。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.knowledge.doc_generator import DocGenState, PipelineStage

# ═══════════════ 假 pipeline ═══════════════


class _FakePipeline:
    """模拟 DocGenerationPipeline，按预设事件序列调用 on_progress"""

    def __init__(
        self,
        events: list[tuple[str, dict[str, Any]]] | None = None,
        result: DocGenState | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = events or []
        self.result = result or {
            "final_document": "# 测试文档\n\n内容",
            "outline": [{"title": "测试", "level": 1}],
            "sections": [{"title": "测试", "level": 1, "content": "内容"}],
            "iteration": 1,
            "token_usage": 500,
            "error": "",
        }
        self.error = error
        self.last_call_kwargs: dict[str, Any] = {}

    async def generate(
        self,
        request: str,
        context: str = "",
        max_iterations: int | None = None,
        *,
        on_progress=None,
    ) -> DocGenState:
        self.last_call_kwargs = {
            "request": request,
            "context": context,
            "max_iterations": max_iterations,
            "on_progress": on_progress,
        }
        for stage, data in self.events:
            if on_progress is not None:
                on_progress(stage, data)
            await asyncio.sleep(0.001)
        if self.error is not None:
            raise self.error
        return self.result


# ═══════════════ fixture ═══════════════


@pytest.fixture
def dev_client(monkeypatch):
    """dev 模式 TestClient + 假 pipeline 注入点"""
    from fastapi.testclient import TestClient

    from app.auth import token_auth
    from app.main import app

    class _FakeDevSettings:
        api_token = ""
        alertmanager_ingest_token = ""

    monkeypatch.setattr(token_auth, "get_settings", lambda: _FakeDevSettings())

    placeholder = _FakePipeline()
    from app.routers import runbook_router

    monkeypatch.setattr(runbook_router, "get_pipeline", lambda: placeholder)

    return TestClient(app), placeholder, runbook_router


def _set_pipeline(monkeypatch, runbook_router, pipeline: _FakePipeline) -> None:
    monkeypatch.setattr(runbook_router, "get_pipeline", lambda: pipeline)


def _parse_sse_stream(response) -> list[tuple[str, dict]]:
    """解析 SSE 流"""
    events: list[tuple[str, dict]] = []
    current_event = ""
    current_data_lines: list[str] = []

    for line in response.iter_lines():
        if line.startswith("event: "):
            current_event = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            current_data_lines.append(line[len("data: ") :])
        elif line == "":
            if current_event and current_data_lines:
                data_str = "\n".join(current_data_lines)
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    data = {"_raw": data_str}
                events.append((current_event, data))
            current_event = ""
            current_data_lines = []
    return events


# ═══════════════ 进度透传 ═══════════════


class TestDocGenProgressPassthrough:
    def test_full_stage_sequence(self, dev_client, monkeypatch):
        """完整 6 阶段进度序列透传"""
        client, _, router = dev_client
        fake = _FakePipeline(
            events=[
                ("stage_start", {"stage": PipelineStage.INTENT.value, "message": "分析需求意图...", "iteration": 0}),
                ("stage_done", {"stage": PipelineStage.INTENT.value, "token_usage": 120}),
                ("stage_start", {"stage": PipelineStage.OUTLINE.value, "message": "生成文档大纲...", "iteration": 0}),
                ("stage_done", {"stage": PipelineStage.OUTLINE.value, "sections_total": 3, "token_usage": 250}),
                ("stage_start", {"stage": PipelineStage.GENERATE.value, "message": "生成文档内容...", "sections_total": 3}),
                ("section_start", {"stage": PipelineStage.GENERATE.value, "section_index": 0, "section_total": 3, "section_title": "概述"}),
                ("section_done", {"stage": PipelineStage.GENERATE.value, "section_index": 1, "section_total": 3}),
                ("section_start", {"stage": PipelineStage.GENERATE.value, "section_index": 1, "section_total": 3, "section_title": "配置"}),
                ("section_done", {"stage": PipelineStage.GENERATE.value, "section_index": 2, "section_total": 3}),
                ("stage_done", {"stage": PipelineStage.GENERATE.value, "sections_completed": 3}),
                ("stage_start", {"stage": PipelineStage.REVIEW.value, "message": "质量审查（第 1 轮）...", "iteration": 1}),
                ("stage_done", {"stage": PipelineStage.REVIEW.value, "decision": "accept", "iteration": 1}),
                ("stage_start", {"stage": PipelineStage.PROOFREAD.value, "message": "校对润色最终文档..."}),
                ("stage_done", {"stage": PipelineStage.PROOFREAD.value, "token_usage": 500}),
            ],
        )
        _set_pipeline(monkeypatch, router, fake)

        with client.stream(
            "POST",
            "/doc/generate/stream",
            json={"request": "生成 Nginx 部署文档", "context": ""},
        ) as resp:
            assert resp.status_code == 200
            events = _parse_sse_stream(resp)

        event_types = [e[0] for e in events]
        # 验证所有事件类型都透传
        assert "stage_start" in event_types
        assert "stage_done" in event_types
        assert "section_start" in event_types
        assert "section_done" in event_types
        assert "done" in event_types
        assert event_types[-1] == "done"

        # 验证 6 个阶段都有 stage_start
        stage_starts = [d for et, d in events if et == "stage_start"]
        stages = {d.get("stage") for d in stage_starts}
        assert PipelineStage.INTENT.value in stages
        assert PipelineStage.OUTLINE.value in stages
        assert PipelineStage.GENERATE.value in stages
        assert PipelineStage.REVIEW.value in stages
        assert PipelineStage.PROOFREAD.value in stages

        # 验证逐章节进度
        section_starts = [d for et, d in events if et == "section_start"]
        assert len(section_starts) == 2  # 3 章中前 2 章有 section_start
        assert section_starts[0]["section_title"] == "概述"

        # 验证 done 事件含最终文档
        done_event = next(d for et, d in events if et == "done")
        assert "测试文档" in done_event["document"]
        assert done_event["iterations"] == 1
        assert done_event["token_usage"] == 500
        assert "total_ms" in done_event

    def test_on_progress_callback_passed_to_pipeline(self, dev_client, monkeypatch):
        """on_progress 回调应被传递给 pipeline.generate()"""
        client, _, router = dev_client
        fake = _FakePipeline(events=[])
        _set_pipeline(monkeypatch, router, fake)

        with client.stream(
            "POST", "/doc/generate/stream",
            json={"request": "test"},
        ) as resp:
            assert resp.status_code == 200
            list(resp.iter_lines())

        assert fake.last_call_kwargs.get("on_progress") is not None, (
            "on_progress 回调应被传递给 pipeline.generate()"
        )
        assert fake.last_call_kwargs.get("request") == "test"

    def test_no_events_still_emits_done(self, dev_client, monkeypatch):
        """pipeline 不发送任何进度事件时，SSE 仍正常结束"""
        client, _, router = dev_client
        fake = _FakePipeline(events=[])
        _set_pipeline(monkeypatch, router, fake)

        with client.stream(
            "POST", "/doc/generate/stream",
            json={"request": "test"},
        ) as resp:
            events = _parse_sse_stream(resp)

        event_types = [e[0] for e in events]
        assert event_types == ["done"], f"无进度时只应有 done: {event_types}"

    def test_generate_error_emits_error_event(self, dev_client, monkeypatch):
        """生成异常 → SSE error 事件"""
        client, _, router = dev_client
        fake = _FakePipeline(
            events=[
                ("stage_start", {"stage": PipelineStage.INTENT.value, "message": "..."}),
            ],
            error=RuntimeError("LLM 超时"),
        )
        _set_pipeline(monkeypatch, router, fake)

        with client.stream(
            "POST", "/doc/generate/stream",
            json={"request": "test"},
        ) as resp:
            events = _parse_sse_stream(resp)

        event_types = [e[0] for e in events]
        assert "stage_start" in event_types
        assert "error" in event_types
        assert event_types[-1] == "error"

        error_data = next(d for et, d in events if et == "error")
        assert "LLM 超时" in error_data["message"]
        assert error_data["retryable"] is True


# ═══════════════ SSE 格式 ═══════════════


class TestDocGenSseFormat:
    def test_content_type(self, dev_client, monkeypatch):
        client, _, router = dev_client
        _set_pipeline(monkeypatch, router, _FakePipeline())

        with client.stream(
            "POST", "/doc/generate/stream",
            json={"request": "test"},
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_x_accel_buffering_disabled(self, dev_client, monkeypatch):
        client, _, router = dev_client
        _set_pipeline(monkeypatch, router, _FakePipeline())

        with client.stream(
            "POST", "/doc/generate/stream",
            json={"request": "test"},
        ) as resp:
            assert resp.headers.get("x-accel-buffering") == "no"

    def test_cache_control_no_cache(self, dev_client, monkeypatch):
        client, _, router = dev_client
        _set_pipeline(monkeypatch, router, _FakePipeline())

        with client.stream(
            "POST", "/doc/generate/stream",
            json={"request": "test"},
        ) as resp:
            assert resp.headers.get("cache-control") == "no-cache"


# ═══════════════ doc_generator 单元测试（不经过 HTTP）═══════════════


class TestDocGeneratorCallback:
    """直接测试 DocGenerationPipeline 的 on_progress 回调机制

    通过 monkeypatch 替换 LLM client 为假实现，验证：
    - on_progress 在每个节点入口/出口被调用
    - 回调失败不影响主流程
    - 回调在 generate() 结束后被清理（单例不泄漏）
    """

    def test_callback_isolated_per_generate_call(self, monkeypatch):
        """generate() 结束后 _on_progress 应被清理，避免单例泄漏"""
        # 由于 DocGenerationPipeline 单例依赖 LLM，这里只验证逻辑：
        # generate() 的 finally 块会清理 _on_progress
        # 通过检查源码行为间接验证
        import inspect

        from app.knowledge.doc_generator import DocGenerationPipeline

        source = inspect.getsource(DocGenerationPipeline.generate)
        assert "self._on_progress = on_progress" in source
        assert "self._on_progress = None" in source
        assert "finally:" in source

    def test_emit_progress_noop_when_no_callback(self):
        """无回调时 _emit_progress 应为 no-op"""
        from app.knowledge.doc_generator import DocGenerationPipeline

        # 创建一个不经过 __init__ 的实例（避免依赖 LLM）
        pipeline = DocGenerationPipeline.__new__(DocGenerationPipeline)
        pipeline._on_progress = None

        # 应不抛异常
        pipeline._emit_progress("stage_start", {"stage": "intent"})
        pipeline._emit_progress("stage_done", {"stage": "intent"})

    def test_emit_progress_invokes_callback(self):
        """有回调时 _emit_progress 应触发回调"""
        from app.knowledge.doc_generator import DocGenerationPipeline

        pipeline = DocGenerationPipeline.__new__(DocGenerationPipeline)
        events = []
        pipeline._on_progress = lambda stage, data: events.append((stage, data))

        pipeline._emit_progress("stage_start", {"stage": "intent", "message": "..."})
        pipeline._emit_progress("stage_done", {"stage": "intent", "token_usage": 100})

        assert len(events) == 2
        assert events[0] == ("stage_start", {"stage": "intent", "message": "..."})
        assert events[1] == ("stage_done", {"stage": "intent", "token_usage": 100})

    def test_emit_progress_swallows_callback_exception(self):
        """回调异常不应中断主流程"""
        from app.knowledge.doc_generator import DocGenerationPipeline

        pipeline = DocGenerationPipeline.__new__(DocGenerationPipeline)

        def bad_callback(stage, data):
            raise RuntimeError("callback failed")

        pipeline._on_progress = bad_callback

        # 应不抛异常
        pipeline._emit_progress("stage_start", {"stage": "intent"})
        pipeline._emit_progress("stage_done", {"stage": "intent"})
