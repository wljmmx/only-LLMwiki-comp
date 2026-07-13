"""P2-5.5 SSE 流式重编译进度透传测试

验证 POST /llm-wiki/recompile/{doc_id}/stream 端点：
- 透传 wiki_compiler 内部 on_progress 事件到 SSE 流
- 事件序列：step_start → step_done → page_start → progress → page_done → done
- 错误路径：编译异常 → error 事件
- 客户端断连取消：cancel_token 传播到 compiler
- 认证：dev 模式放行 / token 校验

通过 monkeypatch 替换 get_wiki_compiler 返回假 compiler，
其 compile_raw_to_wiki 在执行中调用 on_progress 模拟进度事件。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.knowledge.wiki_compiler import ProgressEventType, WikiCompileResult

# ═══════════════ 假 compiler ═══════════════


class _FakeCompiler:
    """模拟 WikiCompiler，按预设事件序列调用 on_progress

    用法：
        fake = _FakeCompiler(events=[
            (ProgressEventType.STEP_START, {"step": "parse", "message": "..."}),
            (ProgressEventType.STEP_DONE, {"step": "parse", "elements": 5}),
        ], result=WikiCompileResult(doc_id="doc-1", pages_created=2))
    """

    def __init__(
        self,
        events: list[tuple[ProgressEventType, dict[str, Any]]] | None = None,
        result: WikiCompileResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = events or []
        self.result = result or WikiCompileResult(doc_id="doc-1", pages_created=2)
        self.error = error
        # 记录调用参数，供测试断言
        self.last_call_kwargs: dict[str, Any] = {}

    async def compile_raw_to_wiki(
        self,
        doc_id: str,
        *,
        force: bool = False,
        rebuild_index_after: bool = True,
        also_compile_graph: bool = False,
        on_progress=None,
        is_cancelled=None,
        task_state=None,
    ) -> WikiCompileResult:
        self.last_call_kwargs = {
            "doc_id": doc_id,
            "force": force,
            "on_progress": on_progress,
            "is_cancelled": is_cancelled,
        }
        # 模拟编译过程中的进度推送
        for etype, data in self.events:
            if on_progress is not None:
                on_progress(etype, data)
            # 让出 event loop，使 SSE generator 有机会 yield
            await asyncio.sleep(0.001)
        if self.error is not None:
            raise self.error
        return self.result


# ═══════════════ fixture ═══════════════


@pytest.fixture
def dev_client(monkeypatch):
    """dev 模式 TestClient（无认证）+ 假 compiler 注入点"""
    from fastapi.testclient import TestClient

    # dev 模式：关闭认证
    from app.auth import token_auth
    from app.main import app

    class _FakeDevSettings:
        api_token = ""
        alertmanager_ingest_token = ""

    monkeypatch.setattr(token_auth, "get_settings", lambda: _FakeDevSettings())

    # 提供一个可替换的 compiler 占位（各测试用例自行 monkeypatch）
    placeholder = _FakeCompiler()
    from app.routers import llm_wiki_router

    monkeypatch.setattr(llm_wiki_router, "get_wiki_compiler", lambda: placeholder)

    return TestClient(app), placeholder, llm_wiki_router


def _set_compiler(monkeypatch, llm_wiki_router, compiler: _FakeCompiler) -> None:
    """替换 dev_client 中的 placeholder compiler"""
    monkeypatch.setattr(llm_wiki_router, "get_wiki_compiler", lambda: compiler)


def _parse_sse_stream(response) -> list[tuple[str, dict]]:
    """解析 SSE 流，返回 [(event_type, data_dict), ...]"""
    events: list[tuple[str, dict]] = []
    current_event = ""
    current_data_lines: list[str] = []

    for line in response.iter_lines():
        if line.startswith("event: "):
            current_event = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            current_data_lines.append(line[len("data: ") :])
        elif line == "":
            # 事件块结束
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


class TestProgressPassthrough:
    def test_full_progress_sequence(self, dev_client, monkeypatch):
        """完整进度序列：step_start → step_done → page_start → progress → page_done → done"""
        client, _, router = dev_client
        fake = _FakeCompiler(
            events=[
                (ProgressEventType.STEP_START, {"step": "parse", "message": "开始解析..."}),
                (ProgressEventType.STEP_DONE, {"step": "parse", "elements": 5}),
                (ProgressEventType.STEP_START, {"step": "extract", "message": "开始抽取..."}),
                (ProgressEventType.STEP_DONE, {"step": "extract", "entities": 3}),
                (ProgressEventType.STEP_START, {"step": "compile", "total": 2}),
                (ProgressEventType.PAGE_START, {"entity": "Nginx", "index": 0, "total": 2}),
                (ProgressEventType.PROGRESS, {"percent": 50, "current": 1, "total": 2}),
                (ProgressEventType.PAGE_DONE, {
                    "entity": "Nginx",
                    "slug": "nginx",
                    "outcome": "created",
                }),
                (ProgressEventType.PAGE_START, {"entity": "Redis", "index": 1, "total": 2}),
                (ProgressEventType.PROGRESS, {"percent": 100, "current": 2, "total": 2}),
                (ProgressEventType.PAGE_DONE, {
                    "entity": "Redis",
                    "slug": "redis",
                    "outcome": "created",
                }),
                (ProgressEventType.STEP_DONE, {"step": "compile", "pages": 2}),
            ],
            result=WikiCompileResult(
                doc_id="doc-1",
                pages_created=2,
                slugs=["nginx", "redis"],
            ),
        )
        _set_compiler(monkeypatch, router, fake)

        with client.stream(
            "POST", "/llm-wiki/recompile/doc-1/stream", params={"force": True}
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            events = _parse_sse_stream(resp)

        # 验证事件序列
        event_types = [e[0] for e in events]
        assert "step_start" in event_types, f"应有 step_start 事件: {event_types}"
        assert "step_done" in event_types, f"应有 step_done 事件: {event_types}"
        assert "page_start" in event_types, f"应有 page_start 事件: {event_types}"
        assert "page_done" in event_types, f"应有 page_done 事件: {event_types}"
        assert "progress" in event_types, f"应有 progress 事件: {event_types}"
        assert "done" in event_types, f"应有 done 事件: {event_types}"
        assert event_types[-1] == "done", f"最后应为 done: {event_types[-5:]}"

        # 验证 step_start 事件的 data 透传
        step_starts = [d for et, d in events if et == "step_start"]
        steps = [d.get("step") for d in step_starts]
        assert "parse" in steps, f"应含 parse 步骤: {steps}"
        assert "extract" in steps, f"应含 extract 步骤: {steps}"
        assert "compile" in steps, f"应含 compile 步骤: {steps}"

        # 验证 page_done 事件
        page_dones = [d for et, d in events if et == "page_done"]
        assert len(page_dones) == 2
        slugs = [d.get("slug") for d in page_dones]
        assert "nginx" in slugs
        assert "redis" in slugs

        # 验证 done 事件包含汇总信息
        done_event = next(d for et, d in events if et == "done")
        assert done_event["doc_id"] == "doc-1"
        assert done_event["pages_created"] == 2
        assert "nginx" in done_event["slugs"]
        assert "redis" in done_event["slugs"]
        assert "total_ms" in done_event

    def test_on_progress_callback_passed_to_compiler(self, dev_client, monkeypatch):
        """验证 on_progress 回调被传递给 compile_raw_to_wiki"""
        client, _, router = dev_client
        fake = _FakeCompiler(events=[])  # 无事件，仅验证调用
        _set_compiler(monkeypatch, router, fake)

        with client.stream("POST", "/llm-wiki/recompile/doc-1/stream") as resp:
            assert resp.status_code == 200
            # 消费完流
            list(resp.iter_lines())

        assert fake.last_call_kwargs.get("on_progress") is not None, (
            "on_progress 回调应被传递给 compile_raw_to_wiki"
        )
        assert fake.last_call_kwargs.get("force") is True

    def test_no_progress_events_still_emits_done(self, dev_client, monkeypatch):
        """compiler 不发送任何进度事件时，SSE 仍应正常结束（done）"""
        client, _, router = dev_client
        fake = _FakeCompiler(events=[], result=WikiCompileResult(doc_id="doc-x"))
        _set_compiler(monkeypatch, router, fake)

        with client.stream("POST", "/llm-wiki/recompile/doc-x/stream") as resp:
            assert resp.status_code == 200
            events = _parse_sse_stream(resp)

        event_types = [e[0] for e in events]
        assert event_types == ["done"], f"无进度时只应有 done: {event_types}"

    def test_compile_error_emits_error_event(self, dev_client, monkeypatch):
        """编译异常 → SSE error 事件"""
        client, _, router = dev_client
        fake = _FakeCompiler(
            events=[
                (ProgressEventType.STEP_START, {"step": "parse", "message": "开始..."}),
            ],
            error=RuntimeError("LLM 超时"),
        )
        _set_compiler(monkeypatch, router, fake)

        with client.stream("POST", "/llm-wiki/recompile/doc-err/stream") as resp:
            assert resp.status_code == 200
            events = _parse_sse_stream(resp)

        event_types = [e[0] for e in events]
        # 应有 step_start（透传）+ error
        assert "step_start" in event_types, f"异常前的事件应透传: {event_types}"
        assert "error" in event_types, f"应有 error 事件: {event_types}"
        assert event_types[-1] == "error", f"最后应为 error: {event_types[-1:]}"

        error_data = next(d for et, d in events if et == "error")
        assert "LLM 超时" in error_data["message"]
        assert error_data["retryable"] is True
        assert error_data["step"] == "compile"


# ═══════════════ SSE 格式 ═══════════════


class TestSseFormat:
    def test_content_type(self, dev_client, monkeypatch):
        """响应 Content-Type 应为 text/event-stream"""
        client, _, router = dev_client
        _set_compiler(monkeypatch, router, _FakeCompiler())

        with client.stream("POST", "/llm-wiki/recompile/doc-1/stream") as resp:
            assert resp.status_code == 200
            ct = resp.headers.get("content-type", "")
            assert "text/event-stream" in ct, f"content-type: {ct}"

    def test_x_accel_buffering_disabled(self, dev_client, monkeypatch):
        """应设置 X-Accel-Buffering: no（nginx 不缓冲）"""
        client, _, router = dev_client
        _set_compiler(monkeypatch, router, _FakeCompiler())

        with client.stream("POST", "/llm-wiki/recompile/doc-1/stream") as resp:
            assert resp.headers.get("x-accel-buffering") == "no"

    def test_cache_control_no_cache(self, dev_client, monkeypatch):
        """应设置 Cache-Control: no-cache"""
        client, _, router = dev_client
        _set_compiler(monkeypatch, router, _FakeCompiler())

        with client.stream("POST", "/llm-wiki/recompile/doc-1/stream") as resp:
            assert resp.headers.get("cache-control") == "no-cache"


# ═══════════════ 进度事件类型覆盖 ═══════════════


class TestProgressEventTypes:
    def test_all_event_types_passed_through(self, dev_client, monkeypatch):
        """所有 ProgressEventType 类型都应透传到 SSE"""
        client, _, router = dev_client
        fake = _FakeCompiler(
            events=[
                (ProgressEventType.STEP_START, {"step": "s1"}),
                (ProgressEventType.STEP_DONE, {"step": "s1"}),
                (ProgressEventType.PAGE_START, {"entity": "e1", "index": 0, "total": 1}),
                (ProgressEventType.PAGE_DONE, {"entity": "e1", "slug": "e1"}),
                (ProgressEventType.PROGRESS, {"percent": 100, "current": 1, "total": 1}),
                (ProgressEventType.QUALITY_CHECK, {"passed": True}),
                (ProgressEventType.CONFLICT_DETECTED, {"conflict": "version"}),
            ],
        )
        _set_compiler(monkeypatch, router, fake)

        with client.stream("POST", "/llm-wiki/recompile/doc-1/stream") as resp:
            events = _parse_sse_stream(resp)

        event_types = {e[0] for e in events}
        # 所有类型都应出现（除 done 是最终汇总）
        assert "step_start" in event_types
        assert "step_done" in event_types
        assert "page_start" in event_types
        assert "page_done" in event_types
        assert "progress" in event_types
        assert "quality_check" in event_types
        assert "conflict_detected" in event_types

    def test_progress_percent_data_preserved(self, dev_client, monkeypatch):
        """progress 事件的 percent 数据应完整透传"""
        client, _, router = dev_client
        fake = _FakeCompiler(
            events=[
                (ProgressEventType.PROGRESS, {"percent": 75, "current": 3, "total": 4}),
            ],
        )
        _set_compiler(monkeypatch, router, fake)

        with client.stream("POST", "/llm-wiki/recompile/doc-1/stream") as resp:
            events = _parse_sse_stream(resp)

        progress_events = [d for et, d in events if et == "progress"]
        assert len(progress_events) == 1
        assert progress_events[0]["percent"] == 75
        assert progress_events[0]["current"] == 3
        assert progress_events[0]["total"] == 4


# ═══════════════ 取消机制 ═══════════════


class TestCancellation:
    def test_cancel_token_passed_to_compiler(self, dev_client, monkeypatch):
        """is_cancelled（request.is_disconnected）应传递给 compiler"""
        client, _, router = dev_client
        fake = _FakeCompiler()
        _set_compiler(monkeypatch, router, fake)

        with client.stream("POST", "/llm-wiki/recompile/doc-1/stream") as resp:
            list(resp.iter_lines())

        assert fake.last_call_kwargs.get("is_cancelled") is not None, (
            "is_cancelled 回调应被传递（用于客户端断连取消）"
        )
