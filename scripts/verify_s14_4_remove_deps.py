#!/usr/bin/env python3
"""S14-4 移除冗余依赖（md-editor-v3）验证

验证内容：
1. package.json 不再含 md-editor-v3
2. package-lock.json 不再含 md-editor-v3
3. src/ 内无 md-editor-v3 引用
4. node_modules 内无 md-editor-v3 目录
5. typecheck 通过
6. 构建成功
7. 全量测试不回归

运行：
    python scripts/verify_s14_4_remove_deps.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FRONTEND = ROOT / "frontend"
PACKAGE_JSON = FRONTEND / "package.json"
PACKAGE_LOCK = FRONTEND / "package-lock.json"
SRC = FRONTEND / "src"
NODE_MODULES = FRONTEND / "node_modules"

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
        cmd, cwd=cwd or ROOT, capture_output=True, text=True, timeout=180
    )
    output = result.stdout + result.stderr
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return result.returncode, output


# ──────────────────────────────────────────────────────────────────
# 1. package.json 不再含 md-editor-v3
# ──────────────────────────────────────────────────────────────────

section("1. package.json 不再含 md-editor-v3")

pkg_content = PACKAGE_JSON.read_text(encoding="utf-8")
check(
    "package.json dependencies 无 md-editor-v3",
    '"md-editor-v3"' not in pkg_content,
)

# 解析 JSON 确认
pkg_json = json.loads(pkg_content)
deps = pkg_json.get("dependencies", {})
dev_deps = pkg_json.get("devDependencies", {})
check(
    "dependencies 字典无 md-editor-v3 键",
    "md-editor-v3" not in deps,
)
check(
    "devDependencies 字典无 md-editor-v3 键",
    "md-editor-v3" not in dev_deps,
)


# ──────────────────────────────────────────────────────────────────
# 2. package-lock.json 不再含 md-editor-v3
# ──────────────────────────────────────────────────────────────────

section("2. package-lock.json 不再含 md-editor-v3")

lock_content = PACKAGE_LOCK.read_text(encoding="utf-8")
check(
    "package-lock.json 无 md-editor-v3 引用",
    "md-editor-v3" not in lock_content,
)


# ──────────────────────────────────────────────────────────────────
# 3. src/ 内无 md-editor-v3 引用
# ──────────────────────────────────────────────────────────────────

section("3. src/ 内无 md-editor-v3 引用")

references: list[str] = []
for f in SRC.rglob("*"):
    if not f.is_file():
        continue
    if f.suffix not in {".ts", ".vue", ".js", ".tsx", ".jsx"}:
        continue
    content = f.read_text(encoding="utf-8", errors="ignore")
    if "md-editor-v3" in content:
        references.append(str(f.relative_to(SRC)))

check(
    "src/ 内无 md-editor-v3 import/引用",
    len(references) == 0,
    f"发现: {references[:5]}" if references else "",
)


# ──────────────────────────────────────────────────────────────────
# 4. node_modules 内无 md-editor-v3 目录
# ──────────────────────────────────────────────────────────────────

section("4. node_modules 内无 md-editor-v3 目录")

md_editor_dir = NODE_MODULES / "md-editor-v3"
check(
    "node_modules/md-editor-v3 不存在",
    not md_editor_dir.exists(),
)


# ──────────────────────────────────────────────────────────────────
# 5. typecheck 通过
# ──────────────────────────────────────────────────────────────────

section("5. typecheck 通过")

code, output = run(["npx", "vue-tsc", "--noEmit"], cwd=FRONTEND)
check(
    "vue-tsc --noEmit 无错误",
    code == 0,
    f"exit={code}, output_tail={output[-300:]}" if code != 0 else "",
)


# ──────────────────────────────────────────────────────────────────
# 6. 构建成功
# ──────────────────────────────────────────────────────────────────

section("6. 构建成功")

code, output = run(["npm", "run", "build"], cwd=FRONTEND)
check(
    "vite build exit=0",
    code == 0,
    f"exit={code}, output_tail={output[-400:]}" if code != 0 else "",
)
check(
    "build 输出含 'built in'",
    "built in" in output,
)


# ──────────────────────────────────────────────────────────────────
# 7. 全量测试不回归
# ──────────────────────────────────────────────────────────────────

section("7. 全量测试不回归")

code, output = run(["npx", "vitest", "run"], cwd=FRONTEND)
check(
    "全量测试 exit=0",
    code == 0,
    f"exit={code}, output_tail={output[-400:]}" if code != 0 else "",
)

m = re.search(r"Tests\s+(\d+)\s+passed\s+\((\d+)\)", output)
if m:
    total = int(m.group(2))
    check("全量测试用例数 >= 158", total >= 158, f"got {total}")
else:
    check("全量测试用例数 >= 158", False, "无法解析")


# ──────────────────────────────────────────────────────────────────
# 汇总
# ──────────────────────────────────────────────────────────────────

print(f"\n{'═' * 60}")
print("S14-4 移除冗余依赖（md-editor-v3）验证汇总")
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
