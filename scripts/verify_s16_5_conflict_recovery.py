"""S16-5 协作编辑冲突恢复 - 验证脚本

验证内容：
1. 源码静态检查：useEditDraft.ts + useCollab.ts + WikiEditor.vue
2. useEditDraft.spec.ts：草稿持久化单元测试（>= 32）
3. useCollab.spec.ts：onReconnect 回调测试（>= 70）
4. WikiEditor.spec.ts：草稿恢复 UI 测试（>= 28）
5. 前端 typecheck 通过
6. 前端全量测试不回归（>= 581）

运行：
    python scripts/verify_s16_5_conflict_recovery.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
FRONTEND_DIR = ROOT / "frontend"

PASS = 0
FAIL = 0


def check(p1, p2, p3: str = "") -> None:
    """智能 check：兼容 (cond, msg) 与 (name, cond, detail) 两种调用风格"""
    global PASS, FAIL
    if isinstance(p1, bool):
        cond, name, detail = p1, p2, p3
    else:
        name, cond, detail = p1, p2, p3
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n── {title} ──")


def run(cmd: list[str], cwd: Path, timeout: int = 600) -> tuple[int, str]:
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
    print("S16-5 协作编辑冲突恢复 - 验证开始")
    print("=" * 60)

    # ────────── 1. 源码静态检查：useEditDraft.ts ──────────
    section("1. useEditDraft.ts：草稿持久化 helper")
    ed_path = FRONTEND_DIR / "src" / "composables" / "useEditDraft.ts"
    ed_content = ed_path.read_text(encoding="utf-8")

    check("EditDraft 接口定义（content/version/savedAt/summary）",
          "export interface EditDraft" in ed_content
          and "content: string" in ed_content
          and "version: number" in ed_content
          and "savedAt: number" in ed_content
          and "summary?" in ed_content)

    check("DRAFT_PREFIX = 'collab_draft:'",
          "DRAFT_PREFIX = 'collab_draft:'" in ed_content)

    check("draftKey 按 slug 拼接 key",
          "function draftKey" in ed_content and "DRAFT_PREFIX" in ed_content)

    check("hasDraft 纯函数导出",
          "export function hasDraft" in ed_content)

    check("loadDraft 纯函数导出 + 字段校验",
          "export function loadDraft" in ed_content
          and "typeof parsed.content !== 'string'" in ed_content
          and "typeof parsed.version !== 'number'" in ed_content)

    check("saveDraft 纯函数导出 + 异常静默",
          "export function saveDraft" in ed_content
          and "console.warn" in ed_content)

    check("clearDraft 纯函数导出",
          "export function clearDraft" in ed_content)

    check("useEditDraft composable 导出",
          "export function useEditDraft" in ed_content)

    check("useEditDraft 返回 draft ref + save/clear/reload/has/isConflictWith",
          "draft = ref" in ed_content
          and "function reload" in ed_content
          and "function save" in ed_content
          and "function clear" in ed_content
          and "function isConflictWith" in ed_content
          and "draft.value !== null && draft.value.version !== serverVersion" in ed_content)

    check("localStorage 异常 try/catch 包裹（4 处）",
          ed_content.count("try {") >= 4 and ed_content.count("} catch") >= 4)

    # ────────── 2. 源码静态检查：useCollab.ts ──────────
    section("2. useCollab.ts：onReconnect 回调机制")
    uc_path = FRONTEND_DIR / "src" / "composables" / "useCollab.ts"
    uc_content = uc_path.read_text(encoding="utf-8")

    check("reconnectCallbacks 内部状态",
          "reconnectCallbacks" in uc_content
          and "Array<() => void>" in uc_content)

    check("onReconnect 函数定义",
          "function onReconnect" in uc_content
          and "reconnectCallbacks.push(cb)" in uc_content)

    check("onReconnect 返回注销函数",
          "reconnectCallbacks.indexOf(cb)" in uc_content
          and "reconnectCallbacks.splice" in uc_content)

    check("socket.onopen 中 wasReconnect 判断",
          "const wasReconnect = reconnectAttempts > 0" in uc_content)

    check("重连成功后触发回调循环",
          "wasReconnect" in uc_content
          and "for (const cb of reconnectCallbacks)" in uc_content)

    check("回调异常 try/catch 保护",
          "console.error('[useCollab] onReconnect 回调执行失败" in uc_content)

    check("disconnect 清空回调（防泄漏）",
          "reconnectCallbacks.length = 0" in uc_content)

    check("返回值暴露 onReconnect",
          "onReconnect," in uc_content)

    # ────────── 3. 源码静态检查：WikiEditor.vue ──────────
    section("3. WikiEditor.vue：草稿恢复集成")
    we_path = FRONTEND_DIR / "src" / "components" / "wiki" / "WikiEditor.vue"
    we_content = we_path.read_text(encoding="utf-8")

    check("import useEditDraft + EditDraft 类型",
          "import { useEditDraft" in we_content
          and "type EditDraft" in we_content)

    check("draft = useEditDraft(props.slug)",
          "useEditDraft(props.slug)" in we_content)

    check("draftRecovery ref 初始化（setup 时同步）",
          "draftRecovery = ref<EditDraft | null>" in we_content
          and "draft.draft.value" in we_content)

    check("draftConflict ref 初始化",
          "draftConflict = ref(draft.isConflictWith" in we_content)

    check("editingContent watch 持久化草稿",
          "watch(" in we_content
          and "editingContent" in we_content
          and "draft.save(newVal, props.version" in we_content)

    check("changeSummary watch 同步草稿",
          "watch(changeSummary" in we_content)

    check("handleSave 成功后清除草稿",
          "draft.clear()" in we_content
          and "draftRecovery.value = null" in we_content)

    check("handleRestoreDraft 恢复草稿函数",
          "function handleRestoreDraft" in we_content
          and "draftRecovery.value.content" in we_content)

    check("handleDiscardDraft 丢弃草稿函数",
          "function handleDiscardDraft" in we_content)

    # P4-1: formatDraftTime 重构为共享 formatRelativeTime（从 @/utils/format 导入）
    check("formatRelativeTime 时间格式化函数（P4-1 共享工具）",
          "formatRelativeTime" in we_content
          and "from '@/utils/format'" in we_content)

    check("模板：NAlert v-if draftRecovery 草稿提示",
          'v-if="draftRecovery"' in we_content
          and "draft-content" in we_content)

    check("模板：冲突时 type=warning，正常时 type=info",
          "draftConflict ? 'warning' : 'info'" in we_content)

    check("模板：恢复草稿按钮",
          "handleRestoreDraft" in we_content
          and "恢复草稿" in we_content)

    check("模板：丢弃草稿按钮",
          "handleDiscardDraft" in we_content
          and "丢弃草稿" in we_content)

    check("模板：冲突提示含版本号对比",
          "draftRecovery.version" in we_content
          and "{{ version }}" in we_content)

    check("样式：editor-draft + draft-content + draft-message + draft-actions",
          "editor-draft" in we_content
          and "draft-content" in we_content
          and "draft-message" in we_content
          and "draft-actions" in we_content)

    # ────────── 4. useEditDraft.spec.ts 测试 ──────────
    section("4. useEditDraft.spec.ts：草稿持久化单元测试")
    ed_spec_path = FRONTEND_DIR / "src" / "composables" / "useEditDraft.spec.ts"
    ed_spec_content = ed_spec_path.read_text(encoding="utf-8")

    check("测试文件包含 5 个 describe 块",
          ed_spec_content.count("describe(") >= 6)  # 1 外层 + 5 内层

    check("纯函数 API 测试（saveDraft/loadDraft/clearDraft/hasDraft）",
          "saveDraft" in ed_spec_content
          and "loadDraft" in ed_spec_content
          and "clearDraft" in ed_spec_content
          and "hasDraft" in ed_spec_content)

    check("slug 隔离测试",
          "slug 隔离" in ed_spec_content)

    check("损坏数据防御测试",
          "损坏数据防御" in ed_spec_content)

    check("composable useEditDraft 测试",
          "composable useEditDraft" in ed_spec_content)

    check("localStorage 异常静默处理测试",
          "localStorage 异常静默处理" in ed_spec_content)

    check("isConflictWith 冲突检测测试",
          "isConflictWith" in ed_spec_content
          and "版本号一致时返回 false" in ed_spec_content
          and "版本号不一致时返回 true" in ed_spec_content)

    code, output = run(
        ["npx", "vitest", "run", "src/composables/useEditDraft.spec.ts"],
        cwd=FRONTEND_DIR, timeout=120,
    )
    check(f"useEditDraft.spec.ts 退出码 0（{code}）", code == 0)
    if code != 0:
        print("\n--- vitest 错误（最后 20 行）---")
        print("\n".join(output.splitlines()[-20:]))
        print("--- end ---")
    m = re.search(r"Tests\s+(\d+) passed", output)
    if m:
        n = int(m.group(1))
        check(f"useEditDraft 用例数 >= 32（实际 {n}）", n >= 32)

    # ────────── 5. useCollab.spec.ts onReconnect 测试 ──────────
    section("5. useCollab.spec.ts：onReconnect 回调测试")
    uc_spec_path = FRONTEND_DIR / "src" / "composables" / "useCollab.spec.ts"
    uc_spec_content = uc_spec_path.read_text(encoding="utf-8")

    check("重连回调 describe 块",
          "重连回调 onReconnect（S16-5）" in uc_spec_content)

    check("首次连接不触发回调测试",
          "首次连接成功不触发 onReconnect 回调" in uc_spec_content)

    check("断线重连触发回调测试",
          "断线重连成功后触发 onReconnect 回调" in uc_spec_content)

    check("多次重连触发回调测试",
          "多次断线重连，每次都触发回调" in uc_spec_content)

    check("多回调注册测试",
          "可注册多个回调，全部触发" in uc_spec_content)

    check("注销函数测试",
          "onReconnect 返回注销函数" in uc_spec_content)

    check("回调异常隔离测试",
          "回调内抛错不影响其他回调" in uc_spec_content)

    check("disconnect 清空回调测试",
          "disconnect 后清空回调" in uc_spec_content)

    code, output = run(
        ["npx", "vitest", "run", "src/composables/useCollab.spec.ts"],
        cwd=FRONTEND_DIR, timeout=120,
    )
    check(f"useCollab.spec.ts 退出码 0（{code}）", code == 0)
    m = re.search(r"Tests\s+(\d+) passed", output)
    if m:
        n = int(m.group(1))
        check(f"useCollab 用例数 >= 70（实际 {n}，新增 9 个 onReconnect 测试）", n >= 70)

    # ────────── 6. WikiEditor.spec.ts 草稿恢复测试 ──────────
    section("6. WikiEditor.spec.ts：草稿恢复 UI 测试")
    we_spec_path = FRONTEND_DIR / "src" / "components" / "wiki" / "WikiEditor.spec.ts"
    we_spec_content = we_spec_path.read_text(encoding="utf-8")

    check("S16-5 describe 块",
          "S16-5 草稿持久化与冲突恢复" in we_spec_content)

    check("import saveDraft/loadDraft/hasDraft",
          "saveDraft" in we_spec_content
          and "loadDraft" in we_spec_content
          and "hasDraft" in we_spec_content)

    check("beforeEach localStorage.clear()",
          "localStorage.clear()" in we_spec_content)

    check("无草稿时不显示提示测试",
          "无草稿时不显示草稿恢复提示" in we_spec_content)

    check("版本一致显示恢复提示测试",
          "版本一致" in we_spec_content and "恢复" in we_spec_content)

    check("版本不一致显示冲突提示测试",
          "版本不一致" in we_spec_content and "冲突" in we_spec_content)

    check("恢复草稿按钮测试",
          "恢复草稿" in we_spec_content and "草稿内容" in we_spec_content)

    check("丢弃草稿按钮测试",
          "丢弃草稿" in we_spec_content and "草稿从 localStorage 清除" in we_spec_content)

    check("编辑持久化草稿测试",
          "编辑内容变化时持久化草稿" in we_spec_content)

    check("保存成功清除草稿测试",
          "保存成功后清除 localStorage 草稿" in we_spec_content)

    check("保存失败保留草稿测试",
          "保存失败后保留草稿" in we_spec_content)

    check("version 缺失不持久化测试",
          "version prop 缺失" in we_spec_content)

    code, output = run(
        ["npx", "vitest", "run", "src/components/wiki/WikiEditor.spec.ts"],
        cwd=FRONTEND_DIR, timeout=120,
    )
    check(f"WikiEditor.spec.ts 退出码 0（{code}）", code == 0)
    m = re.search(r"Tests\s+(\d+) passed", output)
    if m:
        n = int(m.group(1))
        check(f"WikiEditor 用例数 >= 28（实际 {n}，新增 13 个草稿恢复测试）", n >= 28)

    # ────────── 7. 前端 typecheck ──────────
    section("7. 前端 typecheck (vue-tsc --noEmit)")
    code, output = run(
        ["npx", "vue-tsc", "--noEmit"], cwd=FRONTEND_DIR, timeout=180,
    )
    check(f"vue-tsc 退出码 0（{code}）", code == 0)
    if code != 0:
        print("\n--- typecheck 错误（最后 20 行）---")
        print("\n".join(output.splitlines()[-20:]))
        print("--- end ---")

    # ────────── 8. 前端全量测试不回归 ──────────
    section("8. 前端全量测试不回归")
    code, output = run(
        ["npx", "vitest", "run"], cwd=FRONTEND_DIR, timeout=600,
    )
    check(f"全量 vitest 退出码 0（{code}）", code == 0)
    if code != 0:
        print("\n--- vitest 错误（最后 30 行）---")
        print("\n".join(output.splitlines()[-30:]))
        print("--- end ---")
    m = re.search(r"Tests\s+(\d+) passed", output)
    if m:
        n = int(m.group(1))
        check(f"全量用例数 >= 581（实际 {n}，原 527 + 新增 54）", n >= 581)

    # ────────── 总结 ──────────
    print("\n" + "=" * 60)
    print(f"S16-5 验证结果: {PASS} passed / {FAIL} failed")
    print("=" * 60)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
