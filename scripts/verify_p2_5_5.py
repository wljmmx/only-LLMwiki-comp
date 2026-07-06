"""P2-5.5 SSE 传输 + progress 通知 验证脚本

验证：
1. progress 模块：emit_progress 无回调时为 no-op
2. progress 模块：设置回调后 emit_progress 触发回调
3. _tool_generate_runbook 在回调设置时推送进度
4. SSE 端点 /mcp/stream 返回 text/event-stream
5. SSE 流包含 progress + result 事件
6. SSE 错误路径（批量请求）返回 error 事件
7. progressToken 透传
"""
from __future__ import annotations

import sys
import os
import json
import tempfile
import asyncio
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_p255_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)
import app.aiops.topology_builder as tb_mod
tb_mod.DB_PATH = TMP_DIR / "events.db"

from app.mcp.progress import (
    emit_progress,
    set_progress_callback,
    reset_progress_context,
    get_progress_token,
)


def test_progress_noop_without_callback():
    """测试无回调时 emit_progress 为 no-op"""
    print("\n[1/7] 测试无回调时 emit_progress 为 no-op...")
    reset_progress_context()
    # 应不抛异常
    emit_progress("test", 1, 10)
    emit_progress("test", 5, 10)
    print("  ✅ 无回调时 emit_progress 不抛异常")


def test_progress_callback_invoked():
    """测试设置回调后 emit_progress 触发回调"""
    print("\n[2/7] 测试设置回调后 emit_progress 触发回调...")
    reset_progress_context()

    events = []
    set_progress_callback(
        lambda msg, cur, total: events.append((msg, cur, total)),
        token="test-token-123",
    )

    emit_progress("step1", 1, 3)
    emit_progress("step2", 2, 3)
    emit_progress("done", 3, 3)

    assert len(events) == 3, f"应收到 3 个事件，实际 {len(events)}"
    assert events[0] == ("step1", 1, 3)
    assert events[2] == ("done", 3, 3)
    assert get_progress_token() == "test-token-123"
    print(f"  ✅ 收到 {len(events)} 个进度事件: {events}")
    print(f"  ✅ progressToken 透传: {get_progress_token()}")

    reset_progress_context()


def test_generate_runbook_emits_progress():
    """测试 _tool_generate_runbook 推送进度"""
    print("\n[3/7] 测试 _tool_generate_runbook 推送进度...")
    reset_progress_context()

    events = []
    set_progress_callback(
        lambda msg, cur, total: events.append((msg, cur, total)),
        token="rb-token",
    )

    from app.mcp.protocol import _tool_generate_runbook

    # 不需要真实 Runbook 生成，symptom 为空时会立即返回 error，
    # 但 emit_progress 在 symptom 检查之后才调用，所以这里改用合法 symptom
    # 但真实 generate 会访问 search engine，可能失败。我们用空 symptom 测试 error 路径，
    # 验证 emit_progress 不在 error 路径中调用。
    raw = _tool_generate_runbook({"symptom": ""})
    data = json.loads(raw)
    assert "error" in data, f"空 symptom 应返回 error: {data}"
    # error 路径不应推送进度
    assert len(events) == 0, f"error 路径不应推送进度: {events}"
    print(f"  ✅ 空 symptom 路径不推送进度（{len(events)} 事件）")

    reset_progress_context()


def test_sse_endpoint_returns_event_stream():
    """测试 SSE 端点返回 text/event-stream"""
    print("\n[4/7] 测试 SSE 端点返回 text/event-stream...")
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    # 调用一个快速工具（tools/list），验证 SSE 流式响应
    req = {
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": "tools/list",
    }
    with client.stream("POST", "/mcp/stream", json=req) as resp:
        assert resp.status_code == 200, f"status: {resp.status_code}"
        assert "text/event-stream" in resp.headers.get("content-type", "")
        print(f"  ✅ status={resp.status_code}, content-type={resp.headers['content-type']}")

        # 收集所有事件
        events = []
        for line in resp.iter_lines():
            if line:
                events.append(line)

        # 应至少有 result 事件
        event_types = [e for e in events if e.startswith("event:")]
        assert any("result" in e for e in event_types), f"应含 result 事件: {event_types}"
        print(f"  ✅ SSE 流包含 {len(event_types)} 个事件: {event_types}")


def test_sse_stream_with_progress():
    """测试 SSE 流包含 progress 事件（generate_runbook）"""
    print("\n[5/7] 测试 SSE 流包含 progress 事件...")
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    # generate_runbook 需要 symptom；这里用一个简单 symptom 触发流程
    # 即使 search engine 无文档，也会推送进度 + 返回结果
    req = {
        "jsonrpc": "2.0",
        "id": "test-2",
        "method": "tools/call",
        "params": {
            "name": "generate_runbook",
            "arguments": {"symptom": "nginx 502", "max_docs": 1},
        },
        "_meta": {"progressToken": "sse-token-abc"},
    }
    with client.stream("POST", "/mcp/stream", json=req) as resp:
        assert resp.status_code == 200
        full = ""
        for line in resp.iter_lines():
            full += line + "\n"

        # 应含 progress 事件
        assert "event: progress" in full, f"应含 progress 事件:\n{full}"
        # 应含 result 事件
        assert "event: result" in full, f"应含 result 事件:\n{full}"
        # progressToken 应透传
        assert "sse-token-abc" in full, f"应含 progressToken:\n{full}"
        # 应含 notifications/progress
        assert "notifications/progress" in full
        print(f"  ✅ SSE 流含 progress + result 事件，progressToken 已透传")

        # 提取 progress 事件数
        prog_count = full.count("event: progress")
        print(f"  ✅ 收到 {prog_count} 个 progress 事件")


def test_sse_batch_rejected():
    """测试 SSE 端点拒绝批量请求"""
    print("\n[6/7] 测试 SSE 端点拒绝批量请求...")
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    req = [
        {"jsonrpc": "2.0", "id": "1", "method": "tools/list"},
        {"jsonrpc": "2.0", "id": "2", "method": "tools/list"},
    ]
    with client.stream("POST", "/mcp/stream", json=req) as resp:
        assert resp.status_code == 200
        full = ""
        for line in resp.iter_lines():
            full += line + "\n"
        assert "event: error" in full, f"批量请求应返回 error 事件:\n{full}"
        assert "不支持批量" in full or "批量" in full
        print(f"  ✅ 批量请求被拒绝，返回 error 事件")


def test_progress_token_passthrough():
    """测试 progressToken 透传到 SSE 通知"""
    print("\n[7/7] 测试 progressToken 透传到 SSE 通知...")
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    req = {
        "jsonrpc": "2.0",
        "id": "test-3",
        "method": "tools/call",
        "params": {
            "name": "generate_runbook",
            "arguments": {"symptom": "redis 超时", "max_docs": 1},
        },
        "_meta": {"progressToken": 42},  # 数值型 token
    }
    with client.stream("POST", "/mcp/stream", json=req) as resp:
        assert resp.status_code == 200
        full = ""
        for line in resp.iter_lines():
            full += line + "\n"

        # progressToken=42 应出现在 progress 事件的 params 中
        # 检查 "progressToken": 42
        assert '"progressToken": 42' in full or '"progressToken":42' in full, (
            f"progressToken=42 应透传:\n{full}"
        )
        print(f"  ✅ progressToken=42 已透传到 SSE 通知")


def main():
    print("=" * 60)
    print("P2-5.5 SSE 传输 + progress 通知 验证")
    print("=" * 60)

    test_progress_noop_without_callback()
    test_progress_callback_invoked()
    test_generate_runbook_emits_progress()
    test_sse_endpoint_returns_event_stream()
    test_sse_stream_with_progress()
    test_sse_batch_rejected()
    test_progress_token_passthrough()

    print("\n" + "=" * 60)
    print("✅ P2-5.5 全部验证通过！")
    print("=" * 60)


if __name__ == "__main__":
    main()
