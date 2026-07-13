"""P2-5.5 测试：POST /wiki/{slug}/recompile/stream SSE 端点

验证：
- 完整 3 阶段进度序列：start（含 slug）→ compiling → done（含结果摘要）
- 编译成功 done 事件含结果数据（pages_created / pages_updated / slugs）
- 编译失败推送 error 事件（retryable=True）
- SSE 响应头：content-type / x-accel-buffering / cache-control
- 不存在的 slug 路径处理（error 事件，retryable=False）
- on_progress 回调被传递给 compiler（可观察性）

通过 monkeypatch 替换 wiki_router.get_wiki_compiler 返回假编译器，
其 compile_raw_to_wiki() 在执行中调用 on_progress 模拟进度事件。
DB 隔离：重定向 version_control / wikilink / search_engine 的 DB_PATH。
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

# ═══════════════ 假 wiki 编译器 ═══════════════


class _FakeWikiCompiler:
    """模拟 WikiCompiler，按预设事件序列调用 on_progress"""

    def __init__(
        self,
        events: list[tuple[ProgressEventType, dict[str, Any]]] | None = None,
        result: WikiCompileResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = events or []
        self.result = result or WikiCompileResult(
            doc_id="test-doc-123",
            pages_created=1,
            pages_updated=2,
            pages_unchanged=0,
            slugs=["nginx-502-troubleshooting"],
            review_needed=[],
            stale_marked=[],
            errors=[],
            index_rebuilt=True,
        )
        self.error = error
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
        task_state: dict | None = None,
    ) -> WikiCompileResult:
        self.last_call_kwargs = {
            "doc_id": doc_id,
            "force": force,
            "on_progress": on_progress,
            "is_cancelled": is_cancelled,
        }
        for etype, data in self.events:
            if on_progress is not None:
                on_progress(etype, data)
            await asyncio.sleep(0.001)
        if self.error is not None:
            raise self.error
        return self.result


# ═══════════════ fixture ═══════════════


@pytest.fixture
def dev_client(monkeypatch, tmp_path):
    """dev 模式 TestClient + 假 wiki_compiler + DB 隔离"""
    from fastapi.testclient import TestClient

    from app.auth import token_auth
    from app.main import app

    # 关闭认证
    class _FakeDevSettings:
        api_token = ""
        alertmanager_ingest_token = ""

    monkeypatch.setattr(token_auth, "get_settings", lambda: _FakeDevSettings())

    # DB 隔离：重定向 version_control / wikilink / search_engine 的 DB_PATH
    import app.knowledge.wikilink as wl_mod
    import app.search.search_engine as se_mod
    import app.storage.version_control as vc_mod

    vc_db = tmp_path / "versions.db"
    wl_db = tmp_path / "events.db"
    se_db = tmp_path / "search_index.db"

    monkeypatch.setattr(vc_mod, "DB_PATH", vc_db)
    monkeypatch.setattr(wl_mod, "DB_PATH", wl_db)
    monkeypatch.setattr(se_mod, "DB_PATH", se_db)

    # 重置单例，使其用新 DB_PATH 重建
    monkeypatch.setattr(vc_mod, "_vc", None)
    monkeypatch.setattr(se_mod, "_engine", None)

    # 注入假 wiki_compiler
    placeholder = _FakeWikiCompiler()
    from app.routers import wiki_router

    monkeypatch.setattr(wiki_router, "get_wiki_compiler", lambda: placeholder)

    return TestClient(app), placeholder, wiki_router


def _set_compiler(monkeypatch, wiki_router, compiler: _FakeWikiCompiler) -> None:
    monkeypatch.setattr(wiki_router, "get_wiki_compiler", lambda: compiler)


def _parse_sse_stream(response) -> list[tuple[str, dict]]:
    """解析 SSE 流，返回 [(event_type, data), ...]"""
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


def _seed_wiki_page(
    slug: str = "nginx-502-troubleshooting",
    doc_id: str = "test-doc-123",
    title: str = "Nginx 502 故障排查",
) -> dict:
    """seed 一个 wiki 页面，含 sources frontmatter 供端点解析 doc_id"""
    from app.storage import get_version_control

    vc = get_version_control()
    content = f"""---
slug: {slug}
title: {title}
type: incident
tags: [nginx, 502]
sources:
  - doc_id: {doc_id}
    title: Nginx 部署指南
    checksum: sha256:abc123
created_at: 2026-07-01T00:00:00Z
updated_at: 2026-07-01T00:00:00Z
review_status: auto
---

# {title}

## 概述
测试内容。
"""
    return vc.save_version(
        doc_key=f"wiki:{slug}",
        title=title,
        content=content,
        change_summary="seed for test",
    )


# ═══════════════ 3 阶段进度序列 ═══════════════


class TestWikiRecompileSseProgress:
    def test_full_three_stage_sequence(self, dev_client, monkeypatch):
        """完整 3 阶段进度序列：start → compiling → done"""
        client, _, router = dev_client
        _seed_wiki_page(slug="nginx-502-troubleshooting", doc_id="test-doc-123")
        fake = _FakeWikiCompiler()
        _set_compiler(monkeypatch, router, fake)

        with client.stream(
            "POST",
            "/wiki/nginx-502-troubleshooting/recompile/stream",
        ) as resp:
            assert resp.status_code == 200
            events = _parse_sse_stream(resp)

        progress_events = [d for et, d in events if et == "progress"]
        steps = [d.get("step") for d in progress_events]
        # 必须包含 start / compiling / done 三步
        assert "start" in steps, f"缺少 start 步骤: {steps}"
        assert "compiling" in steps, f"缺少 compiling 步骤: {steps}"
        assert "done" in steps, f"缺少 done 步骤: {steps}"
        # 顺序：start 在 compiling 之前，compiling 在 done 之前
        assert steps.index("start") < steps.index("compiling") < steps.index("done"), (
            f"阶段顺序错误: {steps}"
        )
        # start 事件含 slug
        start_event = next(d for d in progress_events if d["step"] == "start")
        assert start_event["slug"] == "nginx-502-troubleshooting"
        # compiling 事件含 doc_id
        compiling_event = next(d for d in progress_events if d["step"] == "compiling")
        assert compiling_event["doc_id"] == "test-doc-123"
        # 最后一个事件应为 done
        assert events[-1][0] == "progress"
        assert events[-1][1]["step"] == "done"

    def test_done_event_contains_compile_result(self, dev_client, monkeypatch):
        """编译成功 done 事件含结果数据（pages_created/pages_updated/slugs）"""
        client, _, router = dev_client
        _seed_wiki_page(slug="nginx-502-troubleshooting", doc_id="test-doc-123")
        fake = _FakeWikiCompiler(
            result=WikiCompileResult(
                doc_id="test-doc-123",
                pages_created=3,
                pages_updated=5,
                pages_unchanged=1,
                slugs=["nginx-502-troubleshooting", "upstream-config"],
                review_needed=["upstream-config"],
                stale_marked=[],
                errors=[],
                index_rebuilt=True,
            )
        )
        _set_compiler(monkeypatch, router, fake)

        with client.stream(
            "POST",
            "/wiki/nginx-502-troubleshooting/recompile/stream",
        ) as resp:
            assert resp.status_code == 200
            events = _parse_sse_stream(resp)

        done_events = [d for et, d in events if et == "progress" and d.get("step") == "done"]
        assert len(done_events) == 1, f"应有且仅有一个 done 事件: {done_events}"
        done = done_events[0]
        # 验证结果摘要字段
        assert done["pages_created"] == 3
        assert done["pages_updated"] == 5
        assert done["pages_unchanged"] == 1
        assert done["slugs"] == ["nginx-502-troubleshooting", "upstream-config"]
        assert done["review_needed"] == ["upstream-config"]
        assert done["index_rebuilt"] is True
        assert done["doc_id"] == "test-doc-123"
        assert done["slug"] == "nginx-502-troubleshooting"
        assert "total_ms" in done
        assert isinstance(done["total_ms"], int)

    def test_compile_error_emits_error_event(self, dev_client, monkeypatch):
        """编译异常 → SSE error 事件，含 retryable=True"""
        client, _, router = dev_client
        _seed_wiki_page(slug="nginx-502-troubleshooting", doc_id="test-doc-123")
        fake = _FakeWikiCompiler(
            error=RuntimeError("LLM 调用超时"),
        )
        _set_compiler(monkeypatch, router, fake)

        with client.stream(
            "POST",
            "/wiki/nginx-502-troubleshooting/recompile/stream",
        ) as resp:
            assert resp.status_code == 200
            events = _parse_sse_stream(resp)

        event_types = [et for et, _ in events]
        assert "error" in event_types, f"应推送 error 事件: {event_types}"
        assert event_types[-1] == "error", f"最后一个事件应为 error: {event_types}"

        error_data = next(d for et, d in events if et == "error")
        assert "LLM 调用超时" in error_data["message"]
        assert error_data["retryable"] is True

    def test_on_progress_callback_passed_to_compiler(self, dev_client, monkeypatch):
        """on_progress 回调应被传递给 compiler.compile_raw_to_wiki()"""
        client, _, router = dev_client
        _seed_wiki_page(slug="nginx-502-troubleshooting", doc_id="test-doc-123")
        fake = _FakeWikiCompiler()
        _set_compiler(monkeypatch, router, fake)

        with client.stream(
            "POST",
            "/wiki/nginx-502-troubleshooting/recompile/stream",
        ) as resp:
            assert resp.status_code == 200
            list(resp.iter_lines())

        # 验证回调被传入
        assert fake.last_call_kwargs.get("on_progress") is not None, (
            "on_progress 回调应被传递给 compile_raw_to_wiki()"
        )
        # 验证 doc_id 与 slug 解析后的来源一致
        assert fake.last_call_kwargs.get("doc_id") == "test-doc-123"
        # force 应为 True（按 slug 重编译默认强制）
        assert fake.last_call_kwargs.get("force") is True
        # is_cancelled 应为 request.is_disconnected
        assert fake.last_call_kwargs.get("is_cancelled") is not None


# ═══════════════ SSE 格式 ═══════════════


class TestWikiRecompileSseFormat:
    def test_sse_headers(self, dev_client, monkeypatch):
        """SSE 响应头：content-type / x-accel-buffering / cache-control"""
        client, _, router = dev_client
        _seed_wiki_page(slug="nginx-502-troubleshooting", doc_id="test-doc-123")
        _set_compiler(monkeypatch, router, _FakeWikiCompiler())

        with client.stream(
            "POST",
            "/wiki/nginx-502-troubleshooting/recompile/stream",
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            assert resp.headers.get("x-accel-buffering") == "no"
            assert resp.headers.get("cache-control") == "no-cache"
            # 消费完流避免资源泄漏
            list(resp.iter_lines())


# ═══════════════ 异常路径 ═══════════════


class TestWikiRecompileSseErrors:
    def test_nonexistent_slug_emits_error_event(self, dev_client, monkeypatch):
        """不存在的 slug → 推送 error 事件（retryable=False）"""
        client, _, router = dev_client
        # 不 seed 任何页面，slug 不存在
        _set_compiler(monkeypatch, router, _FakeWikiCompiler())

        with client.stream(
            "POST",
            "/wiki/nonexistent-slug/recompile/stream",
        ) as resp:
            assert resp.status_code == 200  # SSE 流始终 200，错误经事件传递
            events = _parse_sse_stream(resp)

        event_types = [et for et, _ in events]
        assert "error" in event_types, f"应推送 error 事件: {event_types}"
        assert event_types[-1] == "error", f"最后一个事件应为 error: {event_types}"

        error_data = next(d for et, d in events if et == "error")
        assert "nonexistent-slug" in error_data["message"]
        # slug 不存在是确定性错误，不应建议重试
        assert error_data["retryable"] is False

    def test_compiler_internal_progress_passthrough(self, dev_client, monkeypatch):
        """compiler 内部进度事件（step_start/step_done）应透传到 SSE 流"""
        client, _, router = dev_client
        _seed_wiki_page(slug="nginx-502-troubleshooting", doc_id="test-doc-123")
        fake = _FakeWikiCompiler(
            events=[
                (
                    ProgressEventType.STEP_START,
                    {"step": "parse", "message": "开始解析文档..."},
                ),
                (
                    ProgressEventType.STEP_DONE,
                    {"step": "parse", "elements": 42},
                ),
                (
                    ProgressEventType.STEP_START,
                    {"step": "extract", "message": "开始知识抽取..."},
                ),
            ],
        )
        _set_compiler(monkeypatch, router, fake)

        with client.stream(
            "POST",
            "/wiki/nginx-502-troubleshooting/recompile/stream",
        ) as resp:
            assert resp.status_code == 200
            events = _parse_sse_stream(resp)

        event_types = [et for et, _ in events]
        # 透传的事件类型应出现
        assert "step_start" in event_types, f"应透传 step_start: {event_types}"
        assert "step_done" in event_types, f"应透传 step_done: {event_types}"
        # 仍应有 3 阶段 progress 事件
        progress_steps = [
            d["step"] for et, d in events if et == "progress"
        ]
        assert progress_steps == ["start", "compiling", "done"]
        # done 仍是最后一个事件
        assert events[-1][0] == "progress"
        assert events[-1][1]["step"] == "done"
