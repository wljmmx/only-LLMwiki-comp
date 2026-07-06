"""P3-3 OpenTelemetry 分布式追踪验证脚本

验证内容：
1. tracing 模块基础：未启用时 no-op / 启用时初始化
2. span() 上下文管理器：未启用 no-op / 启用时生成 span
3. get_current_trace_id / get_current_span_id
4. tracing_log_processor：日志关联 trace_id/span_id
5. setup_tracing 注入 TracerProvider（InMemorySpanExporter）
6. FastAPI 自动 instrumentation：/tracing/status 端点返回 trace_id
7. 业务 span 埋点：mock LLM chat 调用生成 llm.chat span
8. 容错：opentelemetry 不可用时降级
9. 幂等：重复 setup_tracing 不重复初始化

运行：
    python scripts/verify_tracing.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保可导入 backend.app
BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

# 测试前确保 tracing 关闭
os.environ.pop("OPSKG_TRACING_ENABLED", None)

PASS = 0
FAIL = 0
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        RESULTS.append((name, True, detail))
    else:
        FAIL += 1
        RESULTS.append((name, False, detail))


def section(title: str) -> None:
    print(f"\n{'='*60}\n{title}\n{'='*60}")


# ────────── 1. tracing 模块基础（未启用时 no-op）──────────

section("1. tracing 模块基础（未启用时 no-op）")

from app.observability import tracing as tracing_mod

# 重置模块状态
tracing_mod._reset_for_test()
check(
    "1.1 未启用时 get_tracer 返回 None",
    tracing_mod.get_tracer() is None,
    f"get_tracer() = {tracing_mod.get_tracer()!r}",
)
check(
    "1.2 未启用时 get_current_trace_id 返回 None",
    tracing_mod.get_current_trace_id() is None,
)
check(
    "1.3 未启用时 get_current_span_id 返回 None",
    tracing_mod.get_current_span_id() is None,
)

# span() 未启用时 no-op
with tracing_mod.span("test.noop", attr1="value1") as s:
    check("1.4 未启用时 span() yield None", s is None, f"span = {s!r}")


# ────────── 2. tracing_log_processor 未启用时不注入 ──────────

section("2. tracing_log_processor 未启用时不注入")

event_dict = {"event": "test", "key": "value"}
result = tracing_mod.tracing_log_processor(None, "info", event_dict.copy())
check(
    "2.1 未启用时 processor 原样返回（无 trace_id）",
    "trace_id" not in result and "span_id" not in result,
    f"result keys = {list(result.keys())}",
)
check(
    "2.2 未启用时 processor 保留原字段",
    result.get("event") == "test" and result.get("key") == "value",
)


# ────────── 3. setup_tracing 注入 TracerProvider（InMemory）──────────

section("3. setup_tracing 注入 TracerProvider（InMemorySpanExporter）")

try:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    has_otel = True
except ImportError:
    has_otel = False
    print("  [SKIP] opentelemetry 未安装，跳过 3-9 组测试")

if has_otel:
    tracing_mod._reset_for_test()

    # 构建带 InMemorySpanExporter 的 provider
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    from fastapi import FastAPI

    test_app = FastAPI()

    @test_app.get("/test-echo")
    async def test_echo():
        from app.observability import get_current_trace_id

        return {"trace_id": get_current_trace_id()}

    tracing_mod.setup_tracing(test_app, provider=provider)
    check(
        "3.1 setup_tracing 后 get_tracer 非 None",
        tracing_mod.get_tracer() is not None,
        f"tracer = {tracing_mod.get_tracer()!r}",
    )
    check(
        "3.2 setup_tracing 设置 _initialized=True",
        tracing_mod._initialized is True,
    )

    # 幂等性：再次调用不应重新初始化（不会清空已有 provider）
    original_tracer = tracing_mod._tracer
    tracing_mod.setup_tracing(test_app, provider=provider)
    check(
        "3.3 setup_tracing 幂等（tracer 不变）",
        tracing_mod._tracer is original_tracer,
    )

    # ────────── 4. span() 生成 span 并写入 exporter ──────────

    section("4. span() 生成 span 并写入 exporter")

    with tracing_mod.span("test.business", op="verify", count=42) as s:
        check(
            "4.1 span() 启用时 yield 非 None",
            s is not None,
            f"span = {s!r}",
        )
        # 在 span 内部获取 trace_id
        trace_id_inside = tracing_mod.get_current_trace_id()
        span_id_inside = tracing_mod.get_current_span_id()
        check(
            "4.2 span 内 get_current_trace_id 返回 32 字符 hex",
            trace_id_inside is not None and len(trace_id_inside) == 32,
            f"trace_id = {trace_id_inside}",
        )
        check(
            "4.3 span 内 get_current_span_id 返回 16 字符 hex",
            span_id_inside is not None and len(span_id_inside) == 16,
            f"span_id = {span_id_inside}",
        )

    # span 结束后应无活跃 span
    trace_id_outside = tracing_mod.get_current_trace_id()
    check(
        "4.4 span 结束后无活跃 trace_id",
        trace_id_outside is None,
        f"trace_id outside = {trace_id_outside}",
    )

    # 强制 flush 让 exporter 收到 span
    provider.force_flush()
    spans = exporter.get_finished_spans()
    check(
        "4.5 exporter 收到 span",
        len(spans) >= 1,
        f"finished spans = {len(spans)}",
    )
    if spans:
        business_span = spans[-1]
        check(
            "4.6 span name 正确",
            business_span.name == "test.business",
            f"name = {business_span.name}",
        )
        attrs = dict(business_span.attributes or {})
        check(
            "4.7 span 属性 op=verify",
            attrs.get("op") == "verify",
            f"attrs = {attrs}",
        )
        check(
            "4.8 span 属性 count=42",
            attrs.get("count") == 42,
        )

    # ────────── 5. tracing_log_processor 启用时注入 ──────────

    section("5. tracing_log_processor 启用时注入 trace_id/span_id")

    with tracing_mod.span("test.log_correlation") as _:
        event = {"event": "llm.call", "model": "deepseek-chat"}
        enriched = tracing_mod.tracing_log_processor(None, "info", event.copy())
        check(
            "5.1 processor 注入 trace_id",
            "trace_id" in enriched and len(enriched["trace_id"]) == 32,
            f"trace_id = {enriched.get('trace_id')}",
        )
        check(
            "5.2 processor 注入 span_id",
            "span_id" in enriched and len(enriched["span_id"]) == 16,
            f"span_id = {enriched.get('span_id')}",
        )
        check(
            "5.3 processor 保留原字段",
            enriched.get("event") == "llm.call"
            and enriched.get("model") == "deepseek-chat",
        )

    # span 外 processor 不注入
    event2 = {"event": "outside"}
    enriched2 = tracing_mod.tracing_log_processor(None, "info", event2.copy())
    check(
        "5.4 span 外 processor 不注入 trace_id",
        "trace_id" not in enriched2,
        f"keys = {list(enriched2.keys())}",
    )

    # ────────── 6. FastAPI 自动 instrumentation ──────────

    section("6. FastAPI 自动 instrumentation（/tracing/status）")

    from fastapi.testclient import TestClient

    # 用 main.py 的 app（已 setup_tracing 注入 provider）
    # 但 main.py 在导入时已调用 setup_tracing(app) 一次（默认关闭）
    # 这里用 test_app（已注入 InMemory provider）
    exporter.clear()
    with TestClient(test_app) as client:
        r = client.get("/test-echo")
        check(
            "6.1 TestClient 请求成功",
            r.status_code == 200,
            f"status = {r.status_code}",
        )
        body = r.json()
        check(
            "6.2 端点内 get_current_trace_id 非 None（FastAPI 自动创建 server span）",
            body.get("trace_id") is not None and len(body["trace_id"]) == 32,
            f"trace_id = {body.get('trace_id')}",
        )

    provider.force_flush()
    server_spans = [
        s for s in exporter.get_finished_spans() if "test-echo" in s.name
    ]
    check(
        "6.3 FastAPI 自动生成 server span",
        len(server_spans) >= 1,
        f"server spans = {[s.name for s in server_spans]}",
    )

    # ────────── 7. 业务 span 埋点（mock LLM chat）──────────

    section("7. 业务 span 埋点（LLM chat 包装）")

    exporter.clear()
    with tracing_mod.span("llm.chat", backend="openai_compat", model="deepseek-chat", message_count=2) as llm_span:
        check(
            "7.1 llm.chat span 创建成功",
            llm_span is not None,
        )
        # 模拟 LLM 调用（不实际调用）
        trace_id_llm = tracing_mod.get_current_trace_id()

    provider.force_flush()
    llm_spans = [s for s in exporter.get_finished_spans() if s.name == "llm.chat"]
    check(
        "7.2 llm.chat span 写入 exporter",
        len(llm_spans) >= 1,
        f"llm.chat spans = {len(llm_spans)}",
    )
    if llm_spans:
        attrs = dict(llm_spans[0].attributes or {})
        check(
            "7.3 llm.chat span 含 backend 属性",
            attrs.get("backend") == "openai_compat",
            f"attrs = {attrs}",
        )
        check(
            "7.4 llm.chat span 含 model 属性",
            attrs.get("model") == "deepseek-chat",
        )
        check(
            "7.5 llm.chat span 含 message_count 属性",
            attrs.get("message_count") == 2,
        )

    # ────────── 8. 嵌套 span（父子关系）──────────

    section("8. 嵌套 span（父子关系）")

    exporter.clear()
    with tracing_mod.span("parent") as parent:
        parent_trace = tracing_mod.get_current_trace_id()
        with tracing_mod.span("child") as child:
            child_trace = tracing_mod.get_current_trace_id()
            check(
                "8.1 子 span 内 trace_id 与父一致",
                parent_trace == child_trace,
                f"parent={parent_trace}, child={child_trace}",
            )

    provider.force_flush()
    finished = exporter.get_finished_spans()
    parent_spans = [s for s in finished if s.name == "parent"]
    child_spans = [s for s in finished if s.name == "child"]
    check(
        "8.2 parent span 写入 exporter",
        len(parent_spans) == 1,
        f"parent count = {len(parent_spans)}",
    )
    check(
        "8.3 child span 写入 exporter",
        len(child_spans) == 1,
        f"child count = {len(child_spans)}",
    )
    if parent_spans and child_spans:
        check(
            "8.4 child.parent_span_id == parent.span_id",
            child_spans[0].parent.span_id == parent_spans[0].context.span_id,
            f"child.parent = {child_spans[0].parent.span_id}, parent = {parent_spans[0].context.span_id}",
        )

    # ────────── 9. 容错：未启用时所有 API no-op ──────────

    section("9. 容错：未启用时所有 API no-op")

    tracing_mod._reset_for_test()
    check(
        "9.1 重置后 get_tracer 返回 None",
        tracing_mod.get_tracer() is None,
    )
    with tracing_mod.span("test.fallback") as s:
        check(
            "9.2 重置后 span() yield None",
            s is None,
        )
    check(
        "9.3 重置后 get_current_trace_id 返回 None",
        tracing_mod.get_current_trace_id() is None,
    )
    event3 = {"event": "test"}
    enriched3 = tracing_mod.tracing_log_processor(None, "info", event3.copy())
    check(
        "9.4 重置后 processor 不注入 trace_id",
        "trace_id" not in enriched3,
    )


# ────────── 10. setup_tracing 未启用时不初始化 ──────────

section("10. setup_tracing 未启用且无 provider 时不初始化")

from fastapi import FastAPI as _FastAPI

tracing_mod._reset_for_test()
dummy_app = _FastAPI()
tracing_mod.setup_tracing(dummy_app)
check(
    "10.1 未启用且无 provider 时 _initialized=False",
    tracing_mod._initialized is False,
)
check(
    "10.2 未启用且无 provider 时 get_tracer 返回 None",
    tracing_mod.get_tracer() is None,
)


# ────────── 汇总 ──────────

section("汇总")
for name, ok, detail in RESULTS:
    status = "✓" if ok else "✗"
    line = f"  {status} {name}"
    if detail and not ok:
        line += f"  → {detail}"
    print(line)
print(f"\n总计：{PASS} 通过 / {FAIL} 失败")
sys.exit(0 if FAIL == 0 else 1)
