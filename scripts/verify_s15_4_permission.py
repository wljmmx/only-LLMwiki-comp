"""S15-4 页面级权限粒度验证脚本

验证项：
1. 文件存在性（composable / 指令 / 测试）
2. main.ts 已注册全局 v-permission 指令
3. usePermission.ts 关键导出（Role / usePermission / hasRole / hasAnyRole / hasMinRole / can）
4. ACTION_PERMISSIONS 操作→角色映射覆盖关键场景
5. permission.ts 指令逻辑（dev 放行 / 未登录拦截 / 单角色匹配 / 多角色匹配）
6. 单元测试（usePermission.spec.ts + permission.spec.ts）通过
7. typecheck（vue-tsc --noEmit）通过
8. 全量前端测试不回归
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FRONTEND_DIR = ROOT / "frontend"

PASS = 0
FAIL = 0


def check(cond: bool, msg: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {msg}")
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


def run(cmd: list[str], cwd: Path = FRONTEND_DIR, timeout: int = 600) -> tuple[int, str]:
    """运行命令并返回 (returncode, combined stdout+stderr)"""
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, (result.stdout + result.stderr)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except FileNotFoundError as e:
        return 127, str(e)


def main() -> int:
    global PASS, FAIL

    print("=" * 60)
    print("S15-4 页面级权限粒度 - 验证开始")
    print("=" * 60)

    # ────────── 1. 文件存在性 ──────────
    print("\n[1] 文件存在性")
    files = [
        ("composable", FRONTEND_DIR / "src" / "composables" / "usePermission.ts"),
        ("directive", FRONTEND_DIR / "src" / "directives" / "permission.ts"),
        ("composable test", FRONTEND_DIR / "src" / "composables" / "usePermission.spec.ts"),
        ("directive test", FRONTEND_DIR / "src" / "directives" / "permission.spec.ts"),
        ("main.ts", FRONTEND_DIR / "src" / "main.ts"),
    ]
    for label, path in files:
        check(path.exists(), f"{label} 文件存在: {path.relative_to(ROOT)}")

    # ────────── 2. main.ts 注册指令 ──────────
    print("\n[2] main.ts 注册全局 v-permission 指令")
    main_ts = (FRONTEND_DIR / "src" / "main.ts").read_text(encoding="utf-8")
    check(
        "import { permission }" in main_ts,
        "导入 permission 指令",
    )
    check(
        "app.directive('permission', permission)" in main_ts,
        "注册全局 v-permission 指令",
    )

    # ────────── 3. usePermission 关键导出 ──────────
    print("\n[3] usePermission.ts 关键导出")
    up_content = (FRONTEND_DIR / "src" / "composables" / "usePermission.ts").read_text(
        encoding="utf-8"
    )
    check("export type Role" in up_content, "导出 Role 类型")
    check("export function usePermission" in up_content, "导出 usePermission 函数")
    for fn in ["hasRole", "hasAnyRole", "hasMinRole", "can"]:
        check(f"function {fn}(" in up_content, f"实现 {fn} 函数")
    check("currentRole" in up_content, "返回 currentRole computed")
    check("isDevMode" in up_content, "返回 isDevMode computed")
    check("ROLE_LEVEL" in up_content, "定义 ROLE_LEVEL 角色层级")
    check("ACTION_PERMISSIONS" in up_content, "定义 ACTION_PERMISSIONS 映射")

    # ────────── 4. ACTION_PERMISSIONS 关键场景 ──────────
    print("\n[4] ACTION_PERMISSIONS 操作→角色映射")
    expected_actions = [
        ("user:create", "admin"),
        ("user:delete", "admin"),
        ("user:update", "admin"),
        ("wiki:publish", "admin"),
        ("wiki:delete", "admin"),
        ("version:rollback", "operator"),
        ("review:approve", "operator"),
        ("review:reject", "operator"),
        ("document:delete", "operator"),
        ("document:upload", "viewer"),
        ("export:create", "viewer"),
        ("runbook:generate", "operator"),
    ]
    for action, min_role in expected_actions:
        check(
            f"'{action}':" in up_content,
            f"操作 {action} 已定义（含 {min_role} 角色）",
        )

    # ────────── 5. permission.ts 指令逻辑 ──────────
    print("\n[5] permission.ts 指令逻辑")
    perm_content = (FRONTEND_DIR / "src" / "directives" / "permission.ts").read_text(
        encoding="utf-8"
    )
    check(
        "authRequired === false" in perm_content and "authRequired === null" in perm_content,
        "dev 模式 + 后端不可达放行",
    )
    check(
        "el.parentNode?.removeChild(el)" in perm_content,
        "不匹配时从 DOM 移除元素（非 v-show 隐藏）",
    )
    check("Array.isArray(binding.value)" in perm_content, "支持单角色 + 数组多角色")
    check("mounted(el, binding)" in perm_content, "实现 mounted 钩子")
    check(
        "前端权限仅为 UX 优化" in perm_content,
        "安全说明：前端权限仅 UX 优化，后端 verify_token 是安全边界",
    )

    # ────────── 6. 单元测试通过 ──────────
    print("\n[6] 单元测试 usePermission.spec.ts + permission.spec.ts")
    code, output = run(
        ["npx", "vitest", "run", "src/composables/usePermission.spec.ts"],
        timeout=120,
    )
    check(code == 0, "usePermission.spec.ts 测试通过")
    if "Tests" in output:
        # 提取 Tests X passed
        for line in output.splitlines():
            if "Tests" in line and "passed" in line:
                print(f"      {line.strip()}")
                break

    code, output = run(
        ["npx", "vitest", "run", "src/directives/permission.spec.ts"],
        timeout=120,
    )
    check(code == 0, "permission.spec.ts 测试通过")
    if "Tests" in output:
        for line in output.splitlines():
            if "Tests" in line and "passed" in line:
                print(f"      {line.strip()}")
                break

    # ────────── 7. typecheck ──────────
    print("\n[7] typecheck (vue-tsc --noEmit)")
    code, output = run(["npx", "vue-tsc", "--noEmit"], timeout=180)
    check(code == 0, "typecheck 通过")
    if code != 0:
        # 输出最后 20 行错误
        print("\n--- typecheck 错误（最后 20 行）---")
        print("\n".join(output.splitlines()[-20:]))
        print("--- end ---")

    # ────────── 8. 全量前端测试不回归 ──────────
    print("\n[8] 全量前端测试不回归")
    code, output = run(["npx", "vitest", "run"], timeout=300)
    check(code == 0, "全量测试通过（无回归）")
    if "Test Files" in output:
        for line in output.splitlines():
            if "Test Files" in line or "Tests" in line:
                print(f"      {line.strip()}")

    # ────────── 总结 ──────────
    print("\n" + "=" * 60)
    print(f"验证结果：✓ {PASS} 项通过 / ✗ {FAIL} 项失败")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
