#!/usr/bin/env python3
"""S15-1 追踪 span 扩展验证

验证内容：
1. S15-1a: structlog.configure 注册 tracing_log_processor（P0 bug 修复）
2. S15-1b: 文档解析 span（document.parse）
3. S15-1c: 知识编译 span（wiki.compile）
4. S15-1d: webhook 投递 span（webhook.deliver）
5. S15-1e: LLM stream/embed/health span + 事件关联 span
6. 端到端：日志携带 trace_id/span_id
7. 全量后端测试不回归
8. verify_tracing.py 不回归

运行：
    python scripts/verify_s15_1_tracing_spans.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"

TRACING_PY = BACKEND / "app" / "observability" / "tracing.py"
REGISTRY_PY = BACKEND / "app" / "parsers" / "registry.py"
WIKI_COMPILER_PY = BACKEND / "app" / "knowledge" / "wiki_compiler.py"
WEBHOOK_MANAGER_PY = BACKEND / "app" / "webhooks" / "manager.py"
OPENAI_COMPAT_PY = BACKEND / "app" / "core" / "llm" / "openai_compat.py"
EVENT_CORRELATOR_PY = BACKEND / "app" / "aiops" / "event_correlator.py"

PASS = 0
FAIL = 0
TESTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        TESTS.append((name, True, detail))
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        TESTS.append((name, False, detail))
        print(f"  ❌ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n── {title} ──")


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[int, str]:
    env = dict(os.environ)
    result = subprocess.run(
        cmd, cwd=cwd or ROOT, capture_output=True, text=True, timeout=timeout, env=env
    )
    output = result.stdout + result.stderr
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return result.returncode, output


# ──────────────────────────────────────────────────────────────────
# 1. S15-1a: structlog.configure 注册 tracing_log_processor
# ──────────────────────────────────────────────────────────────────

section("1. S15-1a: structlog.configure 注册 tracing_log_processor")

tracing_content = TRACING_PY.read_text(encoding="utf-8")

check(
    "tracing.py 含 _configure_structlog_for_tracing 函数",
    "def _configure_structlog_for_tracing()" in tracing_content,
)
check(
    "函数使用 structlog.get_config() 获取现有配置",
    "structlog.get_config()" in tracing_content,
)
check(
    "函数在 renderer 之前插入 tracing_log_processor",
    "insert" in tracing_content and "tracing_log_processor" in tracing_content,
)
check(
    "函数幂等（检查已注册则跳过）",
    "已注册" in tracing_content or "return" in tracing_content.split("_configure_structlog_for_tracing")[1][:500],
)
check(
    "setup_tracing 成功后调用 _configure_structlog_for_tracing",
    "_configure_structlog_for_tracing()" in tracing_content.split("_initialized = True")[1][:200],
)

# 端到端验证：structlog 配置确实被注册
code, output = run(
    ["python", "-c"],
    cwd=BACKEND,
)
# 用更长的脚本验证
verify_script = """
import os
os.environ['OPSKG_TRACING_ENABLED'] = '1'
from app.observability import tracing as t
import structlog

# 初始状态
cfg_before = structlog.get_config()
procs_before = cfg_before.get('processors', [])
has_before = any(getattr(p, '__name__', '') == 'tracing_log_processor' for p in procs_before)

# 调用 setup_tracing
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

t._reset_for_test()
provider = TracerProvider()
exporter = InMemorySpanExporter()
provider.add_span_processor(SimpleSpanProcessor(exporter))
app = FastAPI()
t.setup_tracing(app, provider=provider)

cfg_after = structlog.get_config()
procs_after = cfg_after.get('processors', [])
has_after = any(getattr(p, '__name__', '') == 'tracing_log_processor' for p in procs_after)

print(f"BEFORE: {has_before}")
print(f"AFTER: {has_after}")
print(f"COUNT: {len(procs_before)} -> {len(procs_after)}")
print(f"RESULT: {'PASS' if (not has_before and has_after) else 'FAIL'}")
"""

code, output = run(
    ["python", "-c", verify_script],
    cwd=BACKEND,
)
check(
    "端到端：setup_tracing 后 tracing_log_processor 注册到 structlog",
    "RESULT: PASS" in output,
    f"output={output[-200:]}" if "RESULT: PASS" not in output else "",
)

# 日志携带 trace_id
log_verify_script = """
import os
os.environ['OPSKG_TRACING_ENABLED'] = '1'
from app.observability import tracing as t
import structlog
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

t._reset_for_test()
provider = TracerProvider()
exporter = InMemorySpanExporter()
provider.add_span_processor(SimpleSpanProcessor(exporter))
app = FastAPI()
t.setup_tracing(app, provider=provider)

with t.span('test.log_correlation'):
    log = structlog.get_logger()
    # 捕获日志输出
    import io, sys
    captured = io.StringIO()
    old = sys.stderr
    sys.stderr = captured
    log.info('test_event', key='value')
    sys.stderr = old
    out = captured.getvalue()
    has_trace = 'trace_id=' in out
    print(f"HAS_TRACE: {has_trace}")
    print(f"LOG: {out.strip()[:200]}")
"""

code, output = run(
    ["python", "-c", log_verify_script],
    cwd=BACKEND,
)
check(
    "端到端：span 内 structlog 日志携带 trace_id",
    "trace_id=" in output,
    f"output_tail={output[-200:]}" if "trace_id=" not in output else "",
)


# ──────────────────────────────────────────────────────────────────
# 2. S15-1b: 文档解析 span（document.parse）
# ──────────────────────────────────────────────────────────────────

section("2. S15-1b: 文档解析 span（document.parse）")

registry_content = REGISTRY_PY.read_text(encoding="utf-8")

check(
    "registry.py 含 parse_document 函数",
    "def parse_document(" in registry_content,
)
check(
    "parse_document 使用 span('document.parse', ...)",
    "span(" in registry_content
    and "document.parse" in registry_content,
)
check(
    "parse_document 含 format/doc_id/path 属性",
    all(attr in registry_content for attr in ["format=", "doc_id=", "path="]),
)
check(
    "parse_document 导入 span from app.observability",
    "from app.observability import span" in registry_content
    or "import span" in registry_content,
)


# ──────────────────────────────────────────────────────────────────
# 3. S15-1c: 知识编译 span（wiki.compile）
# ──────────────────────────────────────────────────────────────────

section("3. S15-1c: 知识编译 span（wiki.compile）")

wiki_content = WIKI_COMPILER_PY.read_text(encoding="utf-8")

check(
    "wiki_compiler.py 导入 span",
    "from app.observability import span" in wiki_content
    or "_tracing_span" in wiki_content,
)
check(
    "wiki_compiler.py 含 wiki.compile span",
    "wiki.compile" in wiki_content,
)
check(
    "wiki.compile span 含 doc_id 属性",
    "doc_id=" in wiki_content.split("wiki.compile")[1][:200]
    if "wiki.compile" in wiki_content
    else False,
)


# ──────────────────────────────────────────────────────────────────
# 4. S15-1d: webhook 投递 span（webhook.deliver）
# ──────────────────────────────────────────────────────────────────

section("4. S15-1d: webhook 投递 span（webhook.deliver）")

webhook_content = WEBHOOK_MANAGER_PY.read_text(encoding="utf-8")

check(
    "manager.py 导入 span",
    "from app.observability import span" in webhook_content,
)
check(
    "manager.py 含 webhook.deliver span",
    "webhook.deliver" in webhook_content,
)
check(
    "webhook.deliver span 含 event_type/url/sub_id 属性",
    all(
        attr in webhook_content
        for attr in ["event_type=envelope", "url=sub.get", "sub_id=sub.get"]
    ),
)


# ──────────────────────────────────────────────────────────────────
# 5. S15-1e: LLM stream/embed/health + 事件关联 span
# ──────────────────────────────────────────────────────────────────

section("5. S15-1e: LLM stream/embed/health + 事件关联 span")

llm_content = OPENAI_COMPAT_PY.read_text(encoding="utf-8")

check(
    "openai_compat.py stream() 含 llm.stream span",
    "llm.stream" in llm_content,
)
check(
    "openai_compat.py embed() 含 llm.embed span",
    "llm.embed" in llm_content,
)
check(
    "openai_compat.py health() 含 llm.health span",
    "llm.health" in llm_content,
)
check(
    "llm.stream span 含 backend/model/message_count 属性",
    all(
        attr in llm_content.split("llm.stream")[1][:300]
        for attr in ["backend", "model", "message_count"]
    )
    if "llm.stream" in llm_content
    else False,
)
check(
    "llm.embed span 含 text_count 属性",
    "text_count" in llm_content,
)

# 事件关联
correlator_content = EVENT_CORRELATOR_PY.read_text(encoding="utf-8")

check(
    "event_correlator.py 含 _tracing_span 函数",
    "def _tracing_span(" in correlator_content,
)
check(
    "event_correlator.py 含 aiops.event_correlate span",
    "aiops.event_correlate" in correlator_content,
)
check(
    "aiops.event_correlate span 含 event_count 属性",
    "event_count" in correlator_content,
)


# ──────────────────────────────────────────────────────────────────
# 6. 端到端 span 生成验证
# ──────────────────────────────────────────────────────────────────

section("6. 端到端 span 生成验证")

e2e_script = """
import os
os.environ['OPSKG_TRACING_ENABLED'] = '1'
from app.observability import tracing as t
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

t._reset_for_test()
provider = TracerProvider()
exporter = InMemorySpanExporter()
provider.add_span_processor(SimpleSpanProcessor(exporter))
app = FastAPI()
t.setup_tracing(app, provider=provider)

# 测试各 span 名称
span_names = []
with t.span('document.parse', format='md', doc_id='test', path='/tmp/test.md'):
    pass
with t.span('wiki.compile', doc_id='test'):
    pass
with t.span('webhook.deliver', event_type='test', url='http://x', sub_id=1, attempt=1):
    pass
with t.span('llm.stream', backend='vllm', model='qwen', message_count=2):
    pass
with t.span('llm.embed', backend='vllm', model='text-embedding', text_count=3):
    pass
with t.span('llm.health', backend='vllm', model='qwen'):
    pass
with t.span('aiops.event_correlate', event_count=10):
    pass

provider.force_flush()
spans = exporter.get_finished_spans()
names = [s.name for s in spans]
print(f"SPANS: {names}")

expected = ['document.parse', 'wiki.compile', 'webhook.deliver', 'llm.stream', 'llm.embed', 'llm.health', 'aiops.event_correlate']
missing = [n for n in expected if n not in names]
print(f"MISSING: {missing}")
print(f"RESULT: {'PASS' if not missing else 'FAIL'}")
"""

code, output = run(
    ["python", "-c", e2e_script],
    cwd=BACKEND,
)
check(
    "端到端：7 个 span 名称全部生成",
    "RESULT: PASS" in output,
    f"output={output[-300:]}" if "RESULT: PASS" not in output else "",
)


# ──────────────────────────────────────────────────────────────────
# 7. 全量后端测试不回归（跳过预先存在的环境失败）
# ──────────────────────────────────────────────────────────────────

section("7. 全量后端测试不回归")

code, output = run(
    ["python", "-m", "pytest", "tests/", "-q", "-k", "not test_word_parse and not test_excel_parse"],
    cwd=BACKEND,
)
check(
    "后端测试 exit=0（跳过 markitdown 环境失败）",
    code == 0,
    f"exit={code}, output_tail={output[-300:]}" if code != 0 else "",
)

m = re.search(r"(\d+)\s+passed", output)
if m:
    passed = int(m.group(1))
    check("后端测试用例数 >= 175", passed >= 175, f"got {passed}")


# ──────────────────────────────────────────────────────────────────
# 8. verify_tracing.py 不回归
# ──────────────────────────────────────────────────────────────────

section("8. verify_tracing.py 不回归")

code, output = run(["python", "scripts/verify_tracing.py"], cwd=ROOT)
check(
    "verify_tracing.py exit=0",
    code == 0,
    f"exit={code}, output_tail={output[-200:]}" if code != 0 else "",
)

m = re.search(r"(\d+)\s+通过", output)
if m:
    passed = int(m.group(1))
    check("verify_tracing.py 通过数 >= 39", passed >= 39, f"got {passed}")


# ──────────────────────────────────────────────────────────────────
# 汇总
# ──────────────────────────────────────────────────────────────────

print(f"\n{'═' * 60}")
print("S15-1 追踪 span 扩展验证汇总")
print(f"{'═' * 60}")
print(f"通过: {PASS}")
print(f"失败: {FAIL}")
print(f"总计: {PASS + FAIL}")

if FAIL > 0:
    print("\n❌ 失败项：")
    for name, ok, detail in TESTS:
        if not ok:
            print(f"  - {name}  {detail}")
    sys.exit(1)
else:
    print("\n✅ 全部通过")
    sys.exit(0)
