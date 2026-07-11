#!/usr/bin/env python3
"""S14-2 全局 loading bar 验证

验证内容：
1. 文件存在性：loadingBar.ts / LoadingBarBridge.ts / loadingBar.spec.ts
2. loadingBar.ts：单例管理 + 并发计数 + 防御性 no-op + 测试辅助
3. LoadingBarBridge.ts：useLoadingBar + onMounted/onBeforeUnmount 桥接
4. api/index.ts：axios 拦截器集成（request.start / response.finish / response.error）
5. App.vue：NLoadingBarProvider 包裹 + LoadingBarBridge 挂载
6. loadingBar.spec.ts：10 个测试用例覆盖单例/并发/错误/防御
7. typecheck 通过
8. 单元测试全通过
9. 全量测试不回归

运行：
    python scripts/verify_s14_2_loading_bar.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FRONTEND = ROOT / "frontend"
LOADING_BAR = FRONTEND / "src" / "api" / "loadingBar.ts"
LOADING_BAR_SPEC = FRONTEND / "src" / "api" / "loadingBar.spec.ts"
BRIDGE = FRONTEND / "src" / "components" / "common" / "LoadingBarBridge.ts"
API_INDEX = FRONTEND / "src" / "api" / "index.ts"
APP_VUE = FRONTEND / "src" / "App.vue"

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


def run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """运行命令，返回 (exit_code, output)"""
    result = subprocess.run(
        cmd,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = result.stdout + result.stderr
    # 剥离 ANSI 颜色代码（vitest 输出带颜色）
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return result.returncode, output


# ──────────────────────────────────────────────────────────────────
# 1. 文件存在性
# ──────────────────────────────────────────────────────────────────

section("1. 文件存在性")

check("loadingBar.ts 存在", LOADING_BAR.exists())
check("LoadingBarBridge.ts 存在", BRIDGE.exists())
check("loadingBar.spec.ts 存在", LOADING_BAR_SPEC.exists())
check("api/index.ts 存在", API_INDEX.exists())
check("App.vue 存在", APP_VUE.exists())


# ──────────────────────────────────────────────────────────────────
# 2. loadingBar.ts 结构
# ──────────────────────────────────────────────────────────────────

section("2. loadingBar.ts 结构")

lb_content = LOADING_BAR.read_text(encoding="utf-8")

# 单例管理
check(
    "导入 LoadingBarApi 类型",
    "import type { LoadingBarApi }" in lb_content or "import type { LoadingBarInst }" in lb_content,
)
check(
    "模块级 loadingBar 单例变量",
    "let loadingBar: LoadingBarApi | null = null" in lb_content
    or "let loadingBar: LoadingBarInst | null = null" in lb_content,
)
check(
    "模块级 activeCount 计数器",
    "let activeCount = 0" in lb_content,
)
check(
    "导出 setLoadingBar",
    "export function setLoadingBar(bar: LoadingBarApi): void" in lb_content
    or "export function setLoadingBar(bar: LoadingBarInst): void" in lb_content,
)
check(
    "导出 clearLoadingBar（重置单例 + 计数）",
    "export function clearLoadingBar(): void" in lb_content
    and "loadingBar = null" in lb_content
    and "activeCount = 0" in lb_content,
)

# 并发计数
check(
    "导出 startLoadingBar",
    "export function startLoadingBar(): void" in lb_content,
)
check(
    "startLoadingBar 并发仅首次触发 bar.start()",
    "if (activeCount === 1)" in lb_content
    and "loadingBar.start()" in lb_content,
)
check(
    "导出 finishLoadingBar",
    "export function finishLoadingBar(): void" in lb_content,
)
check(
    "finishLoadingBar 已结束时不重复触发 bar.finish()",
    "if (activeCount === 0) return" in lb_content,
)
check(
    "finishLoadingBar 计数归零时触发 bar.finish()",
    "activeCount -= 1" in lb_content
    and "loadingBar.finish()" in lb_content,
)

# 错误处理
check(
    "导出 errorLoadingBar",
    "export function errorLoadingBar(): void" in lb_content,
)
check(
    "errorLoadingBar 立即触发 bar.error() 并重置计数",
    "loadingBar.error()" in lb_content
    and "activeCount = 0" in lb_content,
)

# 防御性
check(
    "未初始化时 startLoadingBar no-op",
    "if (!loadingBar) return" in lb_content,
)

# 测试辅助
check(
    "导出 _getActiveCountForTest",
    "export function _getActiveCountForTest(): number" in lb_content
    and "return activeCount" in lb_content,
)
check(
    "导出 _resetForTest",
    "export function _resetForTest(): void" in lb_content,
)


# ──────────────────────────────────────────────────────────────────
# 3. LoadingBarBridge.ts 结构
# ──────────────────────────────────────────────────────────────────

section("3. LoadingBarBridge.ts 结构")

bridge_content = BRIDGE.read_text(encoding="utf-8")

check(
    "导入 useLoadingBar",
    "import { useLoadingBar }" in bridge_content
    and "from 'naive-ui'" in bridge_content,
)
check(
    "导入 onMounted / onBeforeUnmount",
    "import { onMounted, onBeforeUnmount }" in bridge_content
    and "from 'vue'" in bridge_content,
)
check(
    "导入 setLoadingBar / clearLoadingBar",
    "import { setLoadingBar, clearLoadingBar }" in bridge_content
    and "from '@/api/loadingBar'" in bridge_content,
)
check(
    "onMounted 调用 setLoadingBar(loadingBar)",
    "onMounted(()" in bridge_content
    and "setLoadingBar(loadingBar)" in bridge_content,
)
check(
    "onBeforeUnmount 调用 clearLoadingBar()",
    "onBeforeUnmount(()" in bridge_content
    and "clearLoadingBar()" in bridge_content,
)
check(
    "组件名 LoadingBarBridge",
    "name: 'LoadingBarBridge'" in bridge_content,
)
check(
    "渲染函数返回 null（无 UI）",
    "return () => null" in bridge_content,
)


# ──────────────────────────────────────────────────────────────────
# 4. api/index.ts axios 拦截器集成
# ──────────────────────────────────────────────────────────────────

section("4. api/index.ts axios 拦截器集成")

api_content = API_INDEX.read_text(encoding="utf-8")

check(
    "导入 startLoadingBar / finishLoadingBar / errorLoadingBar",
    "import {" in api_content
    and "startLoadingBar" in api_content
    and "finishLoadingBar" in api_content
    and "errorLoadingBar" in api_content
    and "from './loadingBar'" in api_content,
)

# 请求拦截器（P4-1: 可能通过 applyRequestInterceptor 函数共享）
check(
    "request 拦截器调用 startLoadingBar()",
    "interceptors.request.use" in api_content
    and "startLoadingBar()" in api_content,
)
check(
    "request 错误回调调用 errorLoadingBar()",
    "errorLoadingBar()" in api_content.split("request.use")[1].split("response.use")[0],
)

# 响应拦截器
check(
    "response 成功调用 finishLoadingBar()",
    "interceptors.response.use" in api_content
    and "finishLoadingBar()" in api_content,
)
check(
    "response 错误调用 errorLoadingBar()",
    "errorLoadingBar()" in api_content.split("response.use")[1],
)


# ──────────────────────────────────────────────────────────────────
# 5. App.vue NLoadingBarProvider
# ──────────────────────────────────────────────────────────────────

section("5. App.vue NLoadingBarProvider")

app_content = APP_VUE.read_text(encoding="utf-8")

check(
    "导入 NLoadingBarProvider",
    "NLoadingBarProvider" in app_content.split("from 'naive-ui'")[0],
)
check(
    "导入 LoadingBarBridge 组件",
    "import LoadingBarBridge" in app_content
    and "from '@/components/common/LoadingBarBridge'" in app_content,
)
check(
    "template 含 <NLoadingBarProvider>",
    "<NLoadingBarProvider>" in app_content
    and "</NLoadingBarProvider>" in app_content,
)
check(
    "<LoadingBarBridge /> 在 NLoadingBarProvider 内部",
    "<LoadingBarBridge />" in app_content.split("<NLoadingBarProvider>")[1].split(
        "</NLoadingBarProvider>"
    )[0],
)


# ──────────────────────────────────────────────────────────────────
# 6. loadingBar.spec.ts 测试覆盖
# ──────────────────────────────────────────────────────────────────

section("6. loadingBar.spec.ts 测试覆盖")

spec_content = LOADING_BAR_SPEC.read_text(encoding="utf-8")

# 单例管理
check(
    "spec 测试 setLoadingBar 设置实例",
    "setLoadingBar 设置实例后可调用 start" in spec_content,
)
check(
    "spec 测试 clearLoadingBar 清除实例",
    "clearLoadingBar 后 start 不再调用" in spec_content,
)

# 并发计数
check(
    "spec 测试并发 start 只触发一次",
    "并发 3 个 start 只调用 1 次 bar.start" in spec_content,
)
check(
    "spec 测试并发 finish 只触发一次",
    "并发 3 个 start + 3 个 finish 只调用 1 次 bar.finish" in spec_content,
)
check(
    "spec 测试 finish 过量不变负数",
    "finish 过量不会变为负数" in spec_content,
)

# 错误处理
check(
    "spec 测试 error 重置计数",
    "errorLoadingBar 立即调用 bar.error 并重置计数" in spec_content,
)
check(
    "spec 测试 error 后 finish 不重复",
    "error 后再 finish 不会重复调用 bar.finish" in spec_content,
)

# 防御性
check(
    "spec 测试未初始化 start no-op",
    "未初始化时 startLoadingBar 静默 no-op" in spec_content,
)
check(
    "spec 测试未初始化 finish no-op",
    "未初始化时 finishLoadingBar 静默 no-op" in spec_content,
)
check(
    "spec 测试未初始化 error no-op",
    "未初始化时 errorLoadingBar 静默 no-op" in spec_content,
)

# 测试用例数（应为 10）
test_count = spec_content.count("  it('")
check(
    "spec 测试用例数 >= 10",
    test_count >= 10,
    f"got {test_count}",
)


# ──────────────────────────────────────────────────────────────────
# 7. typecheck 通过
# ──────────────────────────────────────────────────────────────────

section("7. typecheck 通过")

code, output = run(["npx", "vue-tsc", "--noEmit"], cwd=FRONTEND)
check(
    "vue-tsc --noEmit 无错误",
    code == 0,
    f"exit={code}, output_tail={output[-300:]}" if code != 0 else "",
)


# ──────────────────────────────────────────────────────────────────
# 8. 单元测试全通过
# ──────────────────────────────────────────────────────────────────

section("8. 单元测试全通过")

code, output = run(
    ["npx", "vitest", "run", "src/api/loadingBar.spec.ts"], cwd=FRONTEND
)
check(
    "loadingBar.spec.ts 全通过",
    code == 0 and "failed" not in output.lower(),
    f"exit={code}, output_tail={output[-400:]}" if code != 0 else "",
)

# 检查测试用例数（10 个）
m = re.search(r"Tests\s+(\d+)\s+passed", output)
if m:
    count = int(m.group(1))
    check("loadingBar spec 用例数 >= 10", count >= 10, f"got {count}")
else:
    check("loadingBar spec 用例数 >= 10", False, "无法解析用例数")


# ──────────────────────────────────────────────────────────────────
# 9. 全量前端测试不回归
# ──────────────────────────────────────────────────────────────────

section("9. 全量前端测试不回归")

code, output = run(["npx", "vitest", "run"], cwd=FRONTEND)
check(
    "全量测试 exit=0",
    code == 0,
    f"exit={code}, output_tail={output[-400:]}" if code != 0 else "",
)

m = re.search(r"Tests\s+(\d+)\s+passed\s+\((\d+)\)", output)
if m:
    total = int(m.group(2))
    check("全量测试用例数 >= 158（含 loadingBar 10 个）", total >= 158, f"got {total}")
else:
    check("全量测试用例数 >= 158", False, "无法解析用例数")

m_files = re.search(r"Test Files\s+(\d+)\s+passed\s+\((\d+)\)", output)
if m_files:
    files_total = int(m_files.group(2))
    check(
        "全量 spec 文件数 >= 15（含 loadingBar.spec.ts）",
        files_total >= 15,
        f"got {files_total}",
    )


# ──────────────────────────────────────────────────────────────────
# 汇总
# ──────────────────────────────────────────────────────────────────

print(f"\n{'═' * 60}")
print("S14-2 全局 loading bar 验证汇总")
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
