#!/usr/bin/env python3
"""S14-3 export.ts/mcp.ts 统一 axios 验证

验证内容：
1. api/index.ts：导出 getAuthToken / AUTH_TOKEN_KEY / apiRaw
2. api/index.ts：apiRaw 共享请求拦截器（auth + loading bar）但保留完整 response
3. export.ts：使用 apiRaw（不再 import axios / 不再手动注入 token）
4. mcp.ts：callToolStream 使用 getAuthToken + getApiBaseUrl（不再硬编码）
5. stores/auth.ts：TOKEN_KEY 从 AUTH_TOKEN_KEY 导入
6. 4 个视图（Export/Templates/Versions/Mcp）使用 getAuthToken()
7. 无生产代码硬编码 'opskg_token'（仅 api/index.ts + 测试 + 注释允许）
8. 无 src/ 内 `import axios from 'axios'`（仅 api/index.ts 允许）
9. typecheck 通过
10. 单元测试全通过
11. 全量测试不回归

运行：
    python scripts/verify_s14_3_unified_axios.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"
API_INDEX = SRC / "api" / "index.ts"
EXPORT_TS = SRC / "api" / "export.ts"
MCP_TS = SRC / "api" / "mcp.ts"
AUTH_STORE = SRC / "stores" / "auth.ts"
EXPORT_SPEC = SRC / "api" / "export.spec.ts"

VIEWS = [
    SRC / "views" / "ExportView.vue",
    SRC / "views" / "TemplatesView.vue",
    SRC / "views" / "VersionsView.vue",
    SRC / "views" / "McpView.vue",
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
        cmd, cwd=cwd or ROOT, capture_output=True, text=True, timeout=180
    )
    output = result.stdout + result.stderr
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return result.returncode, output


# ──────────────────────────────────────────────────────────────────
# 1. api/index.ts 导出
# ──────────────────────────────────────────────────────────────────

section("1. api/index.ts 导出")

api_content = API_INDEX.read_text(encoding="utf-8")

check(
    "导出 getAuthToken 函数",
    "export function getAuthToken(): string | null" in api_content,
)
check(
    "getAuthToken 读取 AUTH_TOKEN_KEY",
    "localStorage.getItem('opskg_token')" in api_content
    or "localStorage.getItem(AUTH_TOKEN_KEY)" in api_content,
)
check(
    "导出 AUTH_TOKEN_KEY 常量",
    "export const AUTH_TOKEN_KEY = 'opskg_token'" in api_content,
)
check(
    "导出 apiRaw 实例",
    "export { apiRaw }" in api_content,
)
check(
    "apiRaw 共享 applyRequestInterceptor",
    "applyRequestInterceptor(apiRaw)" in api_content,
)
check(
    "apiRaw response 拦截器返回完整 response（不解包 data）",
    "return response" in api_content.split("const apiRaw")[1],
)
check(
    "api 标准 response 拦截器返回 response.data",
    "return response.data" in api_content.split("const api =")[1].split("const apiRaw")[0],
)


# ──────────────────────────────────────────────────────────────────
# 2. export.ts 重构
# ──────────────────────────────────────────────────────────────────

section("2. export.ts 重构")

export_content = EXPORT_TS.read_text(encoding="utf-8")

check(
    "export.ts 不再 import axios",
    "import axios from 'axios'" not in export_content,
)
check(
    "export.ts 导入 apiRaw + getApiBaseUrl from ./index",
    "import { apiRaw, getApiBaseUrl } from './index'" in export_content,
)
check(
    "export.ts 不再定义 getAuthToken 函数",
    "function getAuthToken()" not in export_content,
)
check(
    "export.ts 不再手动设置 Authorization 头",
    "Authorization" not in export_content
    and "headers.Authorization" not in export_content,
)
check(
    "export.ts 使用 apiRaw.post",
    "apiRaw.post(" in export_content,
)
check(
    "export.ts 仍保留 responseType: 'blob'",
    "responseType: 'blob'" in export_content,
)
check(
    "export.ts 仍访问 res.headers['content-disposition']",
    "res.headers['content-disposition']" in export_content,
)


# ──────────────────────────────────────────────────────────────────
# 3. mcp.ts callToolStream 重构（P4-3: SSE 解析抽取到 utils/sse.ts）
# ──────────────────────────────────────────────────────────────────

section("3. mcp.ts callToolStream 重构")

mcp_content = MCP_TS.read_text(encoding="utf-8")

# P4-3: callToolStream 委托给共享 streamSse（auth 头注入由 sse.ts 统一处理）
SSE_TS = SRC / "utils" / "sse.ts"
sse_content = SSE_TS.read_text(encoding="utf-8") if SSE_TS.exists() else ""

check(
    "mcp.ts 不再直接读 localStorage.getItem('opskg_token')",
    "localStorage.getItem('opskg_token')" not in mcp_content,
)
check(
    "mcp.ts 导入 streamSse（P4-3 共享 SSE 工具）",
    "streamSse" in mcp_content and "from '@/utils/sse'" in mcp_content,
)
check(
    "utils/sse.ts 存在并导出 streamSse",
    "export function streamSse" in sse_content,
)
check(
    "utils/sse.ts 统一注入 Authorization 头（getAuthToken）",
    "getAuthToken" in sse_content and "Authorization" in sse_content,
)
check(
    "utils/sse.ts 统一使用 getApiBaseUrl",
    "getApiBaseUrl" in sse_content,
)
check(
    "callToolStream 使用 getApiBaseUrl()/mcp/stream",
    "${getApiBaseUrl()}/mcp/stream" in mcp_content
    or "getApiBaseUrl()" in mcp_content,
)


# ──────────────────────────────────────────────────────────────────
# 4. stores/auth.ts 使用 AUTH_TOKEN_KEY
# ──────────────────────────────────────────────────────────────────

section("4. stores/auth.ts 使用 AUTH_TOKEN_KEY")

auth_content = AUTH_STORE.read_text(encoding="utf-8")

check(
    "auth.ts 导入 AUTH_TOKEN_KEY from @/api/index",
    "import { AUTH_TOKEN_KEY } from '@/api/index'" in auth_content,
)
check(
    "auth.ts TOKEN_KEY 赋值为 AUTH_TOKEN_KEY",
    "const TOKEN_KEY = AUTH_TOKEN_KEY" in auth_content,
)
check(
    "auth.ts 不再硬编码 const TOKEN_KEY = 'opskg_token'",
    "const TOKEN_KEY = 'opskg_token'" not in auth_content,
)


# ──────────────────────────────────────────────────────────────────
# 5. 4 个视图使用 getAuthToken()
# ──────────────────────────────────────────────────────────────────

section("5. 4 个视图使用 getAuthToken()")

for view_path in VIEWS:
    view_name = view_path.name
    view_content = view_path.read_text(encoding="utf-8")
    check(
        f"{view_name} 导入 getAuthToken",
        "import { getAuthToken } from '@/api/index'" in view_content,
        "" if "import { getAuthToken } from '@/api/index'" in view_content else "缺少导入",
    )
    check(
        f"{view_name} 使用 computed(() => !!getAuthToken())",
        "computed(() => !!getAuthToken())" in view_content,
        ""
        if "computed(() => !!getAuthToken())" in view_content
        else "未使用 getAuthToken()",
    )
    check(
        f"{view_name} 不再直接读 localStorage.getItem('opskg_token')",
        "localStorage.getItem('opskg_token')" not in view_content,
    )


# ──────────────────────────────────────────────────────────────────
# 6. 全局无 'opskg_token' 硬编码（生产代码）
# ──────────────────────────────────────────────────────────────────

section("6. 全局无 'opskg_token' 硬编码（生产代码）")

# 允许的位置：api/index.ts（中央来源）、注释、测试文件
allowed_files = {API_INDEX}
hardcoded_count = 0
hardcoded_files: list[str] = []

for ts_file in SRC.rglob("*.ts"):
    if ts_file in allowed_files:
        continue
    if ts_file.name.endswith(".spec.ts"):
        continue  # 测试文件允许
    content = ts_file.read_text(encoding="utf-8")
    # 检查是否在注释中（行首为 * 或 //）
    for i, line in enumerate(content.split("\n"), 1):
        if "'opskg_token'" in line or '"opskg_token"' in line:
            stripped = line.lstrip()
            if stripped.startswith("*") or stripped.startswith("//"):
                continue
            hardcoded_count += 1
            hardcoded_files.append(f"{ts_file.relative_to(SRC)}:{i}: {line.strip()}")

for vue_file in SRC.rglob("*.vue"):
    content = vue_file.read_text(encoding="utf-8")
    for i, line in enumerate(content.split("\n"), 1):
        if "'opskg_token'" in line or '"opskg_token"' in line:
            stripped = line.lstrip()
            if stripped.startswith("*") or stripped.startswith("//"):
                continue
            hardcoded_count += 1
            hardcoded_files.append(f"{vue_file.relative_to(SRC)}:{i}: {line.strip()}")

check(
    "生产代码无硬编码 'opskg_token'（非注释、非测试）",
    hardcoded_count == 0,
    f"发现 {hardcoded_count} 处: {hardcoded_files[:5]}" if hardcoded_count else "",
)


# ──────────────────────────────────────────────────────────────────
# 7. 无 src/ 内直接 import axios from 'axios'（仅 api/index.ts 允许）
# ──────────────────────────────────────────────────────────────────

section("7. 无 src/ 内直接 import axios（仅 api/index.ts 允许）")

axios_import_files: list[str] = []
for ts_file in SRC.rglob("*.ts"):
    if ts_file == API_INDEX:
        continue
    content = ts_file.read_text(encoding="utf-8")
    if re.search(r"^import\s+axios\s+from\s+['\"]axios['\"]", content, re.MULTILINE):
        axios_import_files.append(str(ts_file.relative_to(SRC)))

for vue_file in SRC.rglob("*.vue"):
    content = vue_file.read_text(encoding="utf-8")
    if re.search(r"^import\s+axios\s+from\s+['\"]axios['\"]", content, re.MULTILINE):
        axios_import_files.append(str(vue_file.relative_to(SRC)))

check(
    "无 src/ 内 `import axios from 'axios'`（api/index.ts 除外）",
    len(axios_import_files) == 0,
    f"发现: {axios_import_files}" if axios_import_files else "",
)


# ──────────────────────────────────────────────────────────────────
# 8. export.spec.ts 更新 mock
# ──────────────────────────────────────────────────────────────────

section("8. export.spec.ts 更新 mock")

spec_content = EXPORT_SPEC.read_text(encoding="utf-8")

check(
    "export.spec.ts 不再 mock 'axios'",
    "vi.mock('axios'" not in spec_content,
)
check(
    "export.spec.ts mock './index' 提供 apiRaw",
    "apiRaw:" in spec_content and "vi.mock('./index'" in spec_content,
)
check(
    "export.spec.ts 导入 apiRaw from ./index",
    "import { apiRaw } from './index'" in spec_content,
)
check(
    "export.spec.ts 断言 apiRaw.post 调用",
    "apiRaw.post).toHaveBeenCalledWith" in spec_content,
)


# ──────────────────────────────────────────────────────────────────
# 9. typecheck 通过
# ──────────────────────────────────────────────────────────────────

section("9. typecheck 通过")

code, output = run(["npx", "vue-tsc", "--noEmit"], cwd=FRONTEND)
check(
    "vue-tsc --noEmit 无错误",
    code == 0,
    f"exit={code}, output_tail={output[-300:]}" if code != 0 else "",
)


# ──────────────────────────────────────────────────────────────────
# 10. 单元测试全通过
# ──────────────────────────────────────────────────────────────────

section("10. 单元测试全通过")

code, output = run(
    ["npx", "vitest", "run", "src/api/export.spec.ts", "src/api/mcp.spec.ts"],
    cwd=FRONTEND,
)
check(
    "export.spec.ts + mcp.spec.ts 全通过",
    code == 0 and "failed" not in output.lower(),
    f"exit={code}, output_tail={output[-400:]}" if code != 0 else "",
)

m = re.search(r"Tests\s+(\d+)\s+passed", output)
if m:
    count = int(m.group(1))
    check("export + mcp spec 用例数 >= 16", count >= 16, f"got {count}")
else:
    check("export + mcp spec 用例数 >= 16", False, "无法解析")


# ──────────────────────────────────────────────────────────────────
# 11. 全量前端测试不回归
# ──────────────────────────────────────────────────────────────────

section("11. 全量前端测试不回归")

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
print("S14-3 export.ts/mcp.ts 统一 axios 验证汇总")
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
