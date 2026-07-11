#!/usr/bin/env python3
"""S14-1 路由级权限守卫验证

验证内容：
1. router/index.ts 结构：403/404 路由注册 + meta.requireRole 声明
2. ForbiddenView.vue / NotFoundView.vue 视图存在且具备关键元素
3. navigationGuard 守卫逻辑：
   - public 路由放行
   - dev 模式不拦截
   - 未登录 → login
   - 角色不足 → forbidden
   - 角色匹配 → 放行
   - 后端不可达 → 放行（不锁死）
4. hasRequiredRole 纯函数逻辑
5. 单元测试覆盖（router/index.spec.ts）
6. typecheck 通过
7. 前端测试全通过

运行：
    python scripts/verify_s14_1_router.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FRONTEND = ROOT / "frontend"
ROUTER = FRONTEND / "src" / "router" / "index.ts"
ROUTER_SPEC = FRONTEND / "src" / "router" / "index.spec.ts"
FORBIDDEN_VIEW = FRONTEND / "src" / "views" / "ForbiddenView.vue"
NOTFOUND_VIEW = FRONTEND / "src" / "views" / "NotFoundView.vue"
ROLE_UTIL = FRONTEND / "src" / "utils" / "role.ts"

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
    import re

    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return result.returncode, output


# ──────────────────────────────────────────────────────────────────
# 1. 文件存在性
# ──────────────────────────────────────────────────────────────────

section("1. 文件存在性")

check("router/index.ts 存在", ROUTER.exists())
check("router/index.spec.ts 存在", ROUTER_SPEC.exists())
check("ForbiddenView.vue 存在", FORBIDDEN_VIEW.exists())
check("NotFoundView.vue 存在", NOTFOUND_VIEW.exists())


# ──────────────────────────────────────────────────────────────────
# 2. router/index.ts 结构
# ──────────────────────────────────────────────────────────────────

section("2. router/index.ts 结构")

router_content = ROUTER.read_text(encoding="utf-8")
role_util_content = ROLE_UTIL.read_text(encoding="utf-8") if ROLE_UTIL.exists() else ""

# RouteMeta 类型扩展
check(
    "RouteMeta 接口扩展 declare module",
    "declare module 'vue-router'" in router_content
    and "interface RouteMeta" in router_content,
)
check(
    "RouteMeta 含 requireRole 字段",
    "requireRole" in router_content
    and "Array<'admin' | 'operator' | 'viewer'>" in router_content,
)

# 403 路由
check(
    "注册 /forbidden 路由",
    "path: '/forbidden'" in router_content
    and "name: 'forbidden'" in router_content,
)
check(
    "forbidden 路由为 public",
    "ForbiddenView" in router_content
    and "public: true" in router_content.split("ForbiddenView")[1].split("}")[0],
)

# 404 路由
check(
    "注册 catch-all 404 路由",
    ":pathMatch(.*)*" in router_content
    and "name: 'not-found'" in router_content
    and "NotFoundView" in router_content,
)

# /users 路由 requireRole
check(
    "/users 路由 requireRole: ['admin']",
    "name: 'users'" in router_content
    and "requireRole: ['admin']" in router_content,
)

# 守卫函数导出
check(
    "导出 navigationGuard 函数",
    "export async function navigationGuard" in router_content,
)
check(
    "导出 hasRequiredRole 函数",
    "export function hasRequiredRole" in role_util_content
    or "export { hasRequiredRole }" in router_content,
)
check(
    "导出 _resetAuthInitializedForTest 函数",
    "export function _resetAuthInitializedForTest" in router_content,
)
check(
    "router.beforeEach(navigationGuard) 注册守卫",
    "router.beforeEach(navigationGuard)" in router_content,
)


# ──────────────────────────────────────────────────────────────────
# 3. ForbiddenView.vue 内容
# ──────────────────────────────────────────────────────────────────

section("3. ForbiddenView.vue 内容")

forbidden_content = FORBIDDEN_VIEW.read_text(encoding="utf-8")

check(
    "ForbiddenView 显示 403 标题",
    "禁止访问" in forbidden_content and "403" in forbidden_content,
)
check(
    "ForbiddenView 显示当前角色",
    "currentRole" in forbidden_content and "authStore.user?.role" in forbidden_content,
)
check(
    "ForbiddenView 显示所需角色",
    "requiredRoles" in forbidden_content
    and "window.history.state" in forbidden_content,
)
check(
    "ForbiddenView 提供「返回首页」按钮",
    "返回首页" in forbidden_content and "goHome" in forbidden_content,
)
check(
    "ForbiddenView 提供「切换账号」按钮",
    "切换账号" in forbidden_content and "switchAccount" in forbidden_content,
)
check(
    "ForbiddenView 调用 authStore.logout 切换账号",
    "authStore.logout" in forbidden_content,
)


# ──────────────────────────────────────────────────────────────────
# 4. NotFoundView.vue 内容
# ──────────────────────────────────────────────────────────────────

section("4. NotFoundView.vue 内容")

notfound_content = NOTFOUND_VIEW.read_text(encoding="utf-8")

check(
    "NotFoundView 显示 404 标题",
    "页面不存在" in notfound_content and "404" in notfound_content,
)
check(
    "NotFoundView 显示尝试访问的路径",
    "attemptedPath" in notfound_content and "route.fullPath" in notfound_content,
)
check(
    "NotFoundView 提供「返回首页」按钮",
    "返回首页" in notfound_content and "goHome" in notfound_content,
)
check(
    "NotFoundView 提供「返回上一页」按钮",
    "返回上一页" in notfound_content and "goBack" in notfound_content,
)


# ──────────────────────────────────────────────────────────────────
# 5. navigationGuard 守卫逻辑（静态检查）
# ──────────────────────────────────────────────────────────────────

section("5. navigationGuard 守卫逻辑（静态检查）")

check(
    "守卫处理 public 路由放行",
    "if (to.meta.public)" in router_content and "return true" in router_content,
)
check(
    "守卫处理未登录跳 login",
    "authStore.authRequired === true" in router_content
    and "!authStore.isAuthenticated" in router_content
    and "name: 'login'" in router_content,
)
check(
    "守卫处理角色不足跳 forbidden",
    "to.meta.requireRole" in router_content
    and "authStore.authRequired === true" in router_content
    and "name: 'forbidden'" in router_content,
)
check(
    "守卫通过 state 传递 requiredRoles",
    "requiredRoles: to.meta.requireRole" in router_content,
)
check(
    "守卫在 dev 模式（authRequired === false）不检查角色",
    "authStore.authRequired === true" in router_content,
)
check(
    "守卫在后端不可达（authRequired === null）不检查角色",
    # 仅在 === true 时检查，=== false 和 === null 都不检查
    "authStore.authRequired === true" in router_content
    and "authRequired !== false" not in router_content,
)


# ──────────────────────────────────────────────────────────────────
# 6. hasRequiredRole 纯函数逻辑
# ──────────────────────────────────────────────────────────────────

section("6. hasRequiredRole 纯函数逻辑")

check(
    "hasRequiredRole 无 requireRole 时返回 true",
    "if (!requireRole || requireRole.length === 0)" in role_util_content
    and "return true" in role_util_content,
)
check(
    "hasRequiredRole userRole 为空时返回 false",
    "if (!userRole)" in role_util_content and "return false" in role_util_content,
)
check(
    "hasRequiredRole 使用 includes 检查角色匹配",
    "requireRole.includes" in role_util_content,
)


# ──────────────────────────────────────────────────────────────────
# 7. 单元测试覆盖
# ──────────────────────────────────────────────────────────────────

section("7. 单元测试覆盖")

spec_content = ROUTER_SPEC.read_text(encoding="utf-8")

# 路由结构验证
check(
    "spec 测试 /forbidden 路由注册",
    "注册 /forbidden 路由且为 public" in spec_content,
)
check(
    "spec 测试 /users requireRole",
    "注册 /users 路由且 requireRole 包含 admin" in spec_content,
)
check(
    "spec 测试 catch-all 404 路由",
    "注册 catch-all 404 路由" in spec_content,
)

# hasRequiredRole 纯函数
check(
    "spec 测试 hasRequiredRole 无 requireRole 放行",
    "hasRequiredRole: 无 requireRole 时总是放行" in spec_content,
)
check(
    "spec 测试 hasRequiredRole userRole undefined 拒绝",
    "hasRequiredRole: userRole 为 undefined 时拒绝" in spec_content,
)
check(
    "spec 测试 hasRequiredRole 角色匹配",
    "hasRequiredRole: 角色匹配时放行" in spec_content,
)
check(
    "spec 测试 hasRequiredRole 角色不匹配",
    "hasRequiredRole: 角色不匹配时拒绝" in spec_content,
)

# 守卫场景
check(
    "spec 测试 public 路由放行",
    "public 路由（login）放行" in spec_content,
)
check(
    "spec 测试 dev 模式不拦截",
    "dev 模式（authRequired=false）下 viewer 可访问 admin 路由" in spec_content,
)
check(
    "spec 测试未登录跳 login",
    "未登录访问 admin 路由 → 跳 login 并带 redirect" in spec_content,
)
check(
    "spec 测试 viewer 访问 admin 跳 forbidden",
    "viewer 访问 admin 路由 → 跳 forbidden" in spec_content,
)
check(
    "spec 测试 operator 访问 admin 跳 forbidden",
    "operator 访问 admin 路由 → 跳 forbidden" in spec_content,
)
check(
    "spec 测试 admin 访问 admin 放行",
    "admin 访问 admin 路由 → 放行" in spec_content,
)
check(
    "spec 测试 viewer 访问无 requireRole 路由放行",
    "viewer 访问无 requireRole 路由" in spec_content,
)
check(
    "spec 测试后端不可达放行",
    "后端不可达（500）时访问 admin 路由 → 放行" in spec_content,
)
check(
    "spec 测试 authInitialized 缓存",
    "第二次导航不重复 fetchMe" in spec_content,
)


# ──────────────────────────────────────────────────────────────────
# 8. typecheck 通过
# ──────────────────────────────────────────────────────────────────

section("8. typecheck 通过")

code, output = run(["npx", "vue-tsc", "--noEmit"], cwd=FRONTEND)
check(
    "vue-tsc --noEmit 无错误",
    code == 0,
    f"exit={code}, output_tail={output[-300:]}" if code != 0 else "",
)


# ──────────────────────────────────────────────────────────────────
# 9. 前端测试全通过
# ──────────────────────────────────────────────────────────────────

section("9. 前端测试全通过")

code, output = run(
    ["npx", "vitest", "run", "src/router/index.spec.ts"], cwd=FRONTEND
)
check(
    "router/index.spec.ts 全通过",
    code == 0 and "failed" not in output.lower(),
    f"exit={code}, output_tail={output[-400:]}" if code != 0 else "",
)

# 检查测试用例数（19 个）
if "Tests" in output:
    import re

    m = re.search(r"Tests\s+(\d+)\s+passed", output)
    if m:
        count = int(m.group(1))
        check("router spec 用例数 >= 19", count >= 19, f"got {count}")
    else:
        check("router spec 用例数 >= 19", False, "无法解析用例数")
else:
    check("router spec 用例数 >= 19", False, "无 Tests 输出")


# ──────────────────────────────────────────────────────────────────
# 10. 全量前端测试不回归
# ──────────────────────────────────────────────────────────────────

section("10. 全量前端测试不回归")

code, output = run(["npx", "vitest", "run"], cwd=FRONTEND)
check(
    "全量 vitest run 无失败",
    code == 0,
    f"exit={code}, output_tail={output[-500:]}" if code != 0 else "",
)

# 解析总用例数
import re

m = re.search(r"Tests\s+(\d+)\s+passed(?:\s*\|\s*(\d+)\s+failed)?", output)
if m:
    total = int(m.group(1)) + (int(m.group(2)) if m.group(2) else 0)
    # 原 129 + 19 新增 = 148
    check("总用例数 >= 148（原 129 + 新 19）", total >= 148, f"got {total}")
else:
    check("总用例数 >= 148", False, "无法解析总用例数")


# ──────────────────────────────────────────────────────────────────
# 总结
# ──────────────────────────────────────────────────────────────────

print("\n" + "=" * 70)
print(f"S14-1 路由级权限守卫验证：{PASS} 通过 / {FAIL} 失败")
print("=" * 70)

if FAIL > 0:
    print("\n失败项:")
    for name, ok, detail in TESTS:
        if not ok:
            print(f"  - {name}: {detail}")
    sys.exit(1)

print("\n✅ S14-1 路由级权限守卫全部验证通过")
sys.exit(0)
