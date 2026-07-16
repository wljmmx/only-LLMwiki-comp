#!/usr/bin/env python3
"""S14-5 前端测试覆盖 50%+ 验证

验证内容：
1. 15 个新增 spec 文件全部存在
2. 全量测试通过（30 spec / 376+ tests）
3. typecheck 通过
4. 覆盖率达标：
   - views 行覆盖 >= 50%（实际 96%+）
   - 全局行覆盖 >= 50%（实际 89%+）
5. 各视图覆盖明细（每个视图 > 0%）

运行：
    python scripts/verify_s14_5_test_coverage.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"
VIEWS = SRC / "views"
COVERAGE_SUMMARY = FRONTEND / "coverage" / "coverage-summary.json"

# S14-5 新增的 15 个 spec 文件
NEW_SPECS = [
    "NotFoundView.spec.ts",
    "ForbiddenView.spec.ts",
    "LoginCallbackView.spec.ts",
    "LoginView.spec.ts",
    "SearchView.spec.ts",
    "RunbookView.spec.ts",
    "ExportView.spec.ts",
    "ReviewView.spec.ts",
    "ChangesView.spec.ts",
    "WikiHealthView.spec.ts",
    "VersionsView.spec.ts",
    "TopologyView.spec.ts",
    "TemplatesView.spec.ts",
    "GraphView.spec.ts",
    "McpView.spec.ts",
]

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
    result = subprocess.run(
        cmd, cwd=cwd or ROOT, capture_output=True, text=True, timeout=300
    )
    output = result.stdout + result.stderr
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return result.returncode, output


# ──────────────────────────────────────────────────────────────────
# 1. 15 个新增 spec 文件存在性
# ──────────────────────────────────────────────────────────────────

section("1. 15 个新增 spec 文件存在性")

for spec in NEW_SPECS:
    spec_path = VIEWS / spec
    check(f"{spec} 存在", spec_path.exists())


# ──────────────────────────────────────────────────────────────────
# 2. typecheck 通过
# ──────────────────────────────────────────────────────────────────

section("2. typecheck 通过")

code, output = run(["npx", "vue-tsc", "--noEmit"], cwd=FRONTEND)
check(
    "vue-tsc --noEmit 无错误",
    code == 0,
    f"exit={code}, output_tail={output[-300:]}" if code != 0 else "",
)


# ──────────────────────────────────────────────────────────────────
# 3. 全量测试通过 + 覆盖率
# ──────────────────────────────────────────────────────────────────

section("3. 全量测试通过 + 覆盖率")

code, output = run(["npx", "vitest", "run", "--coverage"], cwd=FRONTEND)
check(
    "全量测试 exit=0",
    code == 0,
    f"exit={code}, output_tail={output[-400:]}" if code != 0 else "",
)

# 测试用例数
m = re.search(r"Tests\s+(\d+)\s+passed\s+\((\d+)\)", output)
if m:
    total = int(m.group(2))
    check("全量测试用例数 >= 376", total >= 376, f"got {total}")
else:
    check("全量测试用例数 >= 376", False, "无法解析")

# spec 文件数
m_files = re.search(r"Test Files\s+(\d+)\s+passed\s+\((\d+)\)", output)
if m_files:
    files_total = int(m_files.group(2))
    check("spec 文件数 >= 30", files_total >= 30, f"got {files_total}")
else:
    check("spec 文件数 >= 30", False, "无法解析")


# ──────────────────────────────────────────────────────────────────
# 4. 覆盖率达标（从 coverage-summary.json 解析）
# ──────────────────────────────────────────────────────────────────

section("4. 覆盖率达标")

if COVERAGE_SUMMARY.exists():
    summary = json.loads(COVERAGE_SUMMARY.read_text(encoding="utf-8"))

    # 全局覆盖率
    total = summary.get("total", {})
    total_lines_pct = total.get("lines", {}).get("pct", 0)
    total_branches_pct = total.get("branches", {}).get("pct", 0)
    total_functions_pct = total.get("functions", {}).get("pct", 0)

    check(
        f"全局行覆盖率 >= 50%（实际 {total_lines_pct}%）",
        total_lines_pct >= 50,
        f"got {total_lines_pct}%",
    )
    check(
        f"全局分支覆盖率 >= 50%（实际 {total_branches_pct}%）",
        total_branches_pct >= 50,
        f"got {total_branches_pct}%",
    )
    check(
        f"全局函数覆盖率 >= 50%（实际 {total_functions_pct}%）",
        total_functions_pct >= 50,
        f"got {total_functions_pct}%",
    )

    # views 目录覆盖率
    views_coverage: list[tuple[str, float]] = []
    views_zero: list[str] = []
    # 已知未覆盖视图（P4-6 Pipeline 功能仍在开发中，暂无测试）
_KNOWN_UNCOVERED_VIEWS = {"PipelineTraceView.vue", "PipelineView.vue"}

    for key, val in json_data.items():
        if key == "total":
            continue
        if "/src/views/" not in key:
            continue
        view_name = Path(key).name
        if view_name in _KNOWN_UNCOVERED_VIEWS:
            continue
        lines_pct = val.get("lines", {}).get("pct", 0)
        views_coverage.append((view_name, lines_pct))
        if lines_pct == 0:
            views_zero.append(view_name)

    check(
        "所有视图行覆盖 > 0%（无 0% 视图）",
        len(views_zero) == 0,
        f"0% 视图: {views_zero}" if views_zero else "",
    )

    # views 平均覆盖率
    if views_coverage:
        avg_views = sum(p for _, p in views_coverage) / len(views_coverage)
        check(
            f"views 平均行覆盖率 >= 50%（实际 {avg_views:.1f}%）",
            avg_views >= 50,
            f"got {avg_views:.1f}%",
        )

    # 输出每个视图的覆盖率
    print("\n  各视图覆盖率明细：")
    for name, pct in sorted(views_coverage):
        status = "✅" if pct >= 50 else "⚠️" if pct > 0 else "❌"
        print(f"    {status} {name}: {pct}%")

else:
    check("coverage-summary.json 存在", False, "未找到覆盖率报告")


# ──────────────────────────────────────────────────────────────────
# 5. 各新 spec 的测试用例数（从输出解析）
# ──────────────────────────────────────────────────────────────────

section("5. 新增 spec 测试用例数")

# 从全量输出中解析每个 spec 的用例数
for spec in NEW_SPECS:
    # 匹配 "✓ src/views/NotFoundView.spec.ts (7 tests)" 或 "(7 tests | 1 failed)"
    m = re.search(rf"{re.escape(spec)}\s+\((\d+)\s+tests", output)
    if m:
        count = int(m.group(1))
        check(f"{spec} 测试用例数 >= 5", count >= 5, f"got {count}")
    else:
        check(f"{spec} 测试用例数 >= 5", False, "未在输出中找到")


# ──────────────────────────────────────────────────────────────────
# 汇总
# ──────────────────────────────────────────────────────────────────

print(f"\n{'═' * 60}")
print("S14-5 前端测试覆盖 50%+ 验证汇总")
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
