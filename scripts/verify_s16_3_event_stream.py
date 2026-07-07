"""S16-3 协作事件流可视化 - 验证脚本

验证项：
1. 后端 collab_hub.py：broadcast / _send_to 注入 timestamp
2. 后端 test_collab_hub.py：S16-3 timestamp 测试用例
3. 后端 pytest 通过
4. 前端 realtime.ts：CollabEvent 类型 + ServerMessage 各分支 timestamp?
5. 前端 useCollab.ts：events ref + appendEvent + 5 个 case + 清空逻辑
6. 前端 CollabPanel.vue：NCollapse 事件流区域 + 倒序 + formatTime
7. 前端 typecheck 通过
8. 前端 useCollab.spec.ts 事件流测试通过
9. 前端 CollabPanel.spec.ts 事件流 UI 测试通过
10. 前端全量测试不回归
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
BACKEND_DIR = ROOT / "backend"
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
    print("S16-3 协作事件流可视化 - 验证开始")
    print("=" * 60)

    # ────────── 1. 后端 collab_hub.py ──────────
    print("\n[1] 后端 collab_hub.py：timestamp 注入")
    hub_path = BACKEND_DIR / "app" / "realtime" / "collab_hub.py"
    hub_content = hub_path.read_text(encoding="utf-8")
    check(
        'if "timestamp" not in message:' in hub_content
        and "time.time()" in hub_content,
        "broadcast 自动注入 timestamp（time.time()）",
    )
    # _send_to 也注入
    send_to_section = hub_content.split("async def _send_to")[1] if "_send_to" in hub_content else ""
    check(
        'if "timestamp" not in message:' in send_to_section,
        "_send_to 单播也注入 timestamp",
    )
    # 已有 timestamp 时不覆盖
    check(
        hub_content.count('if "timestamp" not in message:') >= 2,
        "broadcast + _send_to 共 2 处 timestamp 守卫（已有不覆盖）",
    )

    # ────────── 2. 后端 test_collab_hub.py ──────────
    print("\n[2] 后端 test_collab_hub.py：S16-3 timestamp 测试用例")
    test_path = BACKEND_DIR / "tests" / "test_collab_hub.py"
    test_content = test_path.read_text(encoding="utf-8")
    check(
        "test_broadcast_auto_injects_timestamp" in test_content,
        "test_broadcast_auto_injects_timestamp 测试存在",
    )
    check(
        "test_broadcast_preserves_existing_timestamp" in test_content,
        "test_broadcast_preserves_existing_timestamp 测试存在",
    )
    check(
        "test_send_to_auto_injects_timestamp" in test_content,
        "test_send_to_auto_injects_timestamp 测试存在",
    )
    # 修复现有测试断言（不再用 == [{"type": "test"}]）
    check(
        'ws1.sent == [{"type": "test"}]' not in test_content,
        "已修复精确匹配断言（不再忽略 timestamp）",
    )

    # ────────── 3. 后端 pytest test_collab_hub.py ──────────
    print("\n[3] 后端 pytest test_collab_hub.py")
    code, output = run(
        ["python", "-m", "pytest", "tests/test_collab_hub.py", "-q", "--no-header"],
        cwd=BACKEND_DIR,
        timeout=120,
    )
    check(code == 0, f"pytest 退出码 0（{code}）")
    m = re.search(r"(\d+) passed", output)
    if m:
        n = int(m.group(1))
        check(n >= 55, f"通过用例数 >= 55（实际 {n}，新增 3 个 S16-3 测试）")

    # ────────── 4. 前端 realtime.ts ──────────
    print("\n[4] 前端 realtime.ts：CollabEvent + ServerMessage timestamp?")
    rt_path = FRONTEND_DIR / "src" / "api" / "realtime.ts"
    rt_content = rt_path.read_text(encoding="utf-8")
    check(
        "export interface CollabEvent" in rt_content,
        "导出 CollabEvent 接口",
    )
    check(
        "timestamp: number" in rt_content,
        "CollabEvent.timestamp 字段（毫秒）",
    )
    check(
        "type:" in rt_content
        and "'user_joined'" in rt_content
        and "'user_left'" in rt_content
        and "'lock_acquired'" in rt_content
        and "'lock_released'" in rt_content
        and "'lock_denied'" in rt_content,
        "CollabEvent.type 覆盖 5 类事件",
    )
    check(
        "userId: string" in rt_content and "displayName: string" in rt_content,
        "CollabEvent.userId / displayName 字段",
    )
    check(
        "message: string" in rt_content,
        "CollabEvent.message 字段（预拼描述）",
    )
    # ServerMessage 各分支 timestamp?
    check(
        rt_content.count("timestamp?: number") >= 11,
        "ServerMessage 11 个分支均加 timestamp? 可选字段",
    )

    # ────────── 5. 前端 useCollab.ts ──────────
    print("\n[5] 前端 useCollab.ts：events ref + appendEvent + 5 个 case")
    uc_path = FRONTEND_DIR / "src" / "composables" / "useCollab.ts"
    uc_content = uc_path.read_text(encoding="utf-8")
    check(
        "const MAX_EVENTS = 50" in uc_content,
        "MAX_EVENTS = 50 常量",
    )
    check(
        "const events = ref<CollabEvent[]>" in uc_content,
        "events ref<CollabEvent[]>",
    )
    check(
        "function lookupDisplayName" in uc_content,
        "lookupDisplayName helper（从 onlineUsers 反查）",
    )
    check(
        "function appendEvent" in uc_content,
        "appendEvent helper",
    )
    check(
        "events.value.length >= MAX_EVENTS" in uc_content,
        "appendEvent 实现 cap 50（超出保留最新 N 条）",
    )
    check(
        "serverTimestamp * 1000" in uc_content or "timestamp * 1000" in uc_content,
        "appendEvent 把后端秒级 timestamp × 1000 转毫秒",
    )
    check(
        "Date.now()" in uc_content,
        "appendEvent 缺失 timestamp 时用 Date.now() 兜底",
    )
    # 5 个 case 调用 appendEvent
    check(
        uc_content.count("appendEvent(") >= 5,
        "5 个事件 case 调用 appendEvent（user_joined/user_left/lock_acquired/lock_released/lock_denied）",
    )
    # user_left 在移除前反查
    user_left_section = uc_content.split("case 'user_left'")[1].split("case '")[0] if "case 'user_left'" in uc_content else ""
    check(
        "lookupDisplayName" in user_left_section
        and "onlineUsers.value.filter" in user_left_section,
        "user_left 先 lookupDisplayName 再 filter 移除（避免反查失败）",
    )
    # onclose 清空 events
    onclose_section = uc_content.split("socket.onclose")[1].split("}")[0] if "socket.onclose" in uc_content else ""
    check(
        "events.value = []" in onclose_section,
        "onclose 清空 events",
    )
    # disconnect 清空 events
    disconnect_section = uc_content.split("function disconnect")[1].split("function ")[0] if "function disconnect" in uc_content else ""
    check(
        "events.value = []" in disconnect_section,
        "disconnect 清空 events",
    )
    # 返回值暴露 events
    check(
        "events: readonly(events)" in uc_content,
        "返回值暴露 events（readonly）",
    )

    # ────────── 6. 前端 CollabPanel.vue ──────────
    print("\n[6] 前端 CollabPanel.vue：NCollapse 事件流区域")
    cp_path = FRONTEND_DIR / "src" / "components" / "collab" / "CollabPanel.vue"
    cp_content = cp_path.read_text(encoding="utf-8")
    check(
        "NCollapse" in cp_content and "NCollapseItem" in cp_content,
        "导入 NCollapse + NCollapseItem",
    )
    check(
        "events" in cp_content
        and "reversedEvents" in cp_content,
        "从 useCollab 取 events 并计算 reversedEvents",
    )
    check(
        "[...events.value].reverse()" in cp_content,
        "reversedEvents 用 reverse() 倒序展示",
    )
    check(
        "eventTypeColor" in cp_content,
        "eventTypeColor 颜色映射（按事件类型区分）",
    )
    check(
        "function formatTime" in cp_content,
        "formatTime 函数（HH:MM:SS 格式化）",
    )
    check(
        'pad(d.getHours())' in cp_content
        and 'pad(d.getMinutes())' in cp_content
        and 'pad(d.getSeconds())' in cp_content,
        "formatTime 输出 HH:MM:SS",
    )
    check(
        'default-expanded-names' in cp_content and "'events'" in cp_content,
        "NCollapse 默认展开 events 项",
    )
    check(
        "暂无事件" in cp_content,
        "无事件时显示 '暂无事件'",
    )
    check(
        "event-dot" in cp_content and "event-time" in cp_content and "event-msg" in cp_content,
        "事件项 3 元素：圆点 + 时间 + 消息",
    )
    check(
        "event-list" in cp_content and "max-height" in cp_content,
        "事件列表 max-height 限制 + 滚动",
    )

    # ────────── 7. 前端 typecheck ──────────
    print("\n[7] 前端 typecheck (vue-tsc --noEmit)")
    code, output = run(
        ["npx", "vue-tsc", "--noEmit"], cwd=FRONTEND_DIR, timeout=180
    )
    check(code == 0, f"vue-tsc 退出码 0（{code}）")
    if code != 0:
        print("\n--- typecheck 错误（最后 20 行）---")
        print("\n".join(output.splitlines()[-20:]))
        print("--- end ---")

    # ────────── 8. useCollab.spec.ts 事件流测试 ──────────
    print("\n[8] 前端 useCollab.spec.ts 事件流测试")
    code, output = run(
        ["npx", "vitest", "run", "src/composables/useCollab.spec.ts"],
        cwd=FRONTEND_DIR,
        timeout=120,
    )
    check(code == 0, f"useCollab.spec.ts 退出码 0（{code}）")
    m = re.search(r"Tests\s+(\d+) passed", output)
    if m:
        n = int(m.group(1))
        check(n >= 61, f"useCollab 用例数 >= 61（实际 {n}，新增 13 个事件流测试）")

    # ────────── 9. CollabPanel.spec.ts 事件流 UI 测试 ──────────
    print("\n[9] 前端 CollabPanel.spec.ts 事件流 UI 测试")
    code, output = run(
        ["npx", "vitest", "run", "src/components/collab/CollabPanel.spec.ts"],
        cwd=FRONTEND_DIR,
        timeout=120,
    )
    check(code == 0, f"CollabPanel.spec.ts 退出码 0（{code}）")
    m = re.search(r"Tests\s+(\d+) passed", output)
    if m:
        n = int(m.group(1))
        check(n >= 28, f"CollabPanel 用例数 >= 28（实际 {n}，新增 10 个事件流 UI 测试）")

    # ────────── 10. 前端全量测试不回归 ──────────
    print("\n[10] 前端全量测试不回归")
    code, output = run(
        ["npx", "vitest", "run"], cwd=FRONTEND_DIR, timeout=600
    )
    check(code == 0, f"全量 vitest 退出码 0（{code}）")
    if code != 0:
        print("\n--- vitest 错误（最后 30 行）---")
        print("\n".join(output.splitlines()[-30:]))
        print("--- end ---")
    m = re.search(r"Tests\s+(\d+) passed", output)
    if m:
        n = int(m.group(1))
        check(n >= 527, f"全量用例数 >= 527（实际 {n}，原 504 + 新增 23）")

    # ────────── 总结 ──────────
    print("\n" + "=" * 60)
    print(f"S16-3 验证结果: {PASS} passed / {FAIL} failed")
    print("=" * 60)
    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
