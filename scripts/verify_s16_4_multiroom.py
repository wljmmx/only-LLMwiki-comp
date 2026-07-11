"""S16-4 CollabHub 多房间压测 - 验证脚本

验证内容：
1. 后端源码：metric 指标定义 + collab_hub metric 调用 + 上限守卫 + config + collector
2. 后端单元测试：68 个用例（55 旧 + 6 上限 + 7 metric）全过
3. 多房间并发压测：50 房间 × 5 连接 = 250 WebSocket 并发 connect/broadcast
4. 心跳清理压测：100 连接混合 active/stale，cleanup_stale 性能
5. 锁竞争压测：5 房间 × 10 用户同时 acquire_lock，验证无死锁
6. 广播延迟压测：250 连接 broadcast，p99 < 100ms
7. 内存压测：tracemalloc 监控 50 房间 × 5 连接建拆，内存增长合理
8. 集成测试：FastAPI TestClient WebSocket 端到端 connect + heartbeat
9. 上限守卫压测：max_rooms=10 时第 11 个房间被拒绝

运行：
    python scripts/verify_s16_4_multiroom.py
"""
from __future__ import annotations

import asyncio
import os
import re
import statistics
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
BACKEND_DIR = ROOT / "backend"

# 确保可以 import backend.app.*
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("OPSKG_API_TOKEN", "")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("LLM_BACKEND", "openai_compat")
os.environ.setdefault("OPENAI_COMPAT_API_KEY", "test")

PASS = 0
FAIL = 0


def check(p1, p2, p3: str = "") -> None:
    """智能 check：兼容 (cond, msg) 与 (name, cond, detail) 两种调用风格

    - 若第一个参数是 bool，识别为旧式 (cond, msg[, detail])
    - 否则识别为新式 (name, cond[, detail])
    """
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


def fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.1f} ms"
    return f"{seconds:.2f} s"


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


# ═══════════════ MockWebSocket（与 test_collab_hub 同语义） ═══════════════


class MockWebSocket:
    """模拟 starlette.WebSocket 的最小可用实现"""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.client_state = None  # 延迟初始化，避免依赖 starlette
        self._incoming: asyncio.Queue = asyncio.Queue()

    async def accept(self) -> None:
        from starlette.websockets import WebSocketState

        self.client_state = WebSocketState.CONNECTED

    async def send_json(self, message: dict[str, Any]) -> None:
        if self.closed:
            raise RuntimeError("WebSocket is closed")
        self.sent.append(message)

    async def receive_json(self) -> dict[str, Any]:
        item = await self._incoming.get()
        if item is None:
            from starlette.websockets import WebSocketDisconnect

            raise WebSocketDisconnect(code=1000)
        return item

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = True
        from starlette.websockets import WebSocketState

        self.client_state = WebSocketState.DISCONNECTED

    def push_message(self, message: dict[str, Any]) -> None:
        self._incoming.put_nowait(message)


# ═══════════════ 压测用例 ═══════════════


async def scenario_multi_room_connect(room_count: int, conns_per_room: int) -> dict:
    """场景 3：50 房间 × 5 连接 = 250 WebSocket 并发 connect

    验证：
    - 全部连接成功
    - 总耗时 < 5s
    - 单连接延迟 p99 < 200ms
    - 无死锁/异常
    """
    section(f"3. 多房间并发 connect 压测（{room_count} 房间 × {conns_per_room} 连接）")
    from app.realtime.collab_hub import CollabHub

    hub = CollabHub()
    latencies: list[float] = []
    errors = 0
    success = 0

    async def one_connect(room_idx: int, user_idx: int) -> None:
        slug = f"room-{room_idx:03d}"
        user_id = f"u-{room_idx:03d}-{user_idx}"
        ws = MockWebSocket()
        await ws.accept()
        t0 = time.perf_counter()
        try:
            await hub.connect(
                slug=slug,
                user_id=user_id,
                username=user_id,
                display_name=user_id,
                role="viewer",
                ws=ws,
            )
            nonlocal success
            success += 1
        except Exception as e:
            nonlocal errors
            errors += 1
            print(f"  ⚠️  connect 失败 {user_id}: {e}")
        latencies.append(time.perf_counter() - t0)

    # 并发 connect
    tasks = []
    for r in range(room_count):
        for u in range(conns_per_room):
            tasks.append(one_connect(r, u))
    start = time.perf_counter()
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start

    latencies.sort()
    p50 = statistics.median(latencies) if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0
    max_lat = max(latencies) if latencies else 0

    total = room_count * conns_per_room
    print(f"  总数: {total}, 成功: {success}, 失败: {errors}")
    print(f"  总耗时: {fmt_duration(elapsed)}")
    print(
        f"  延迟: p50={fmt_duration(p50)}, p95={fmt_duration(p95)}, "
        f"p99={fmt_duration(p99)}, max={fmt_duration(max_lat)}"
    )

    check(f"全部 {total} 连接成功", success == total, f"got {success}")
    check("无错误", errors == 0, f"{errors} errors")
    check("总耗时 < 5s", elapsed < 5.0, f"got {fmt_duration(elapsed)}")
    check("p99 < 200ms", p99 < 0.2, f"got {fmt_duration(p99)}")

    # 验证状态一致性
    rooms = hub.list_rooms()
    check(f"房间数 = {room_count}", len(rooms) == room_count, f"got {len(rooms)}")
    total_conns = sum(r["online_count"] for r in rooms)
    check(
        f"总连接数 = {total}",
        total_conns == total,
        f"got {total_conns}",
    )

    return {
        "total": total,
        "success": success,
        "errors": errors,
        "elapsed": elapsed,
        "p50": p50,
        "p99": p99,
        "rooms": len(rooms),
        "total_conns": total_conns,
        "hub": hub,  # 后续场景复用
    }


async def scenario_heartbeat_cleanup(hub: Any, stale_count: int) -> dict:
    """场景 4：心跳清理压测

    让 stale_count 个连接的心跳超时（patch HEARTBEAT_TIMEOUT 极短），
    验证 cleanup_stale 一次扫描能清理全部超时连接。
    """
    section(f"4. 心跳清理压测（清理 {stale_count} 个 stale 连接）")
    from app.realtime import collab_hub as ch_module

    # 把超时阈值设极短，让所有连接立即变 stale
    original_timeout = ch_module.HEARTBEAT_TIMEOUT
    ch_module.HEARTBEAT_TIMEOUT = 0.0
    try:
        time.sleep(0.01)  # 让 last_heartbeat 真正过期
        start = time.perf_counter()
        removed = await hub.cleanup_stale()
        elapsed = time.perf_counter() - start
    finally:
        ch_module.HEARTBEAT_TIMEOUT = original_timeout

    print(f"  清理 {removed} 连接，耗时 {fmt_duration(elapsed)}")
    check(f"清理数 = {stale_count}", removed == stale_count, f"got {removed}")
    check("清理耗时 < 2s", elapsed < 2.0, f"got {fmt_duration(elapsed)}")

    # 验证状态：全部房间应被回收
    rooms_after = hub.list_rooms()
    check("清理后房间全部回收", len(rooms_after) == 0, f"got {len(rooms_after)}")

    return {"removed": removed, "elapsed": elapsed, "rooms_after": len(rooms_after)}


async def scenario_lock_contention(room_count: int, users_per_room: int) -> dict:
    """场景 5：锁竞争压测

    room_count 个房间，每房间 users_per_room 个用户同时 acquire_lock。
    验证：每房间恰好 1 个用户拿到锁，其余被拒；无死锁。
    """
    section(
        f"5. 锁竞争压测（{room_count} 房间 × {users_per_room} 用户同时 acquire_lock）"
    )
    from app.realtime.collab_hub import CollabHub

    hub = CollabHub()
    # 先建立所有连接
    ws_map: dict[tuple[str, str], MockWebSocket] = {}
    for r in range(room_count):
        slug = f"lock-room-{r:03d}"
        for u in range(users_per_room):
            user_id = f"lu-{r:03d}-{u}"
            ws = MockWebSocket()
            await ws.accept()
            await hub.connect(
                slug=slug,
                user_id=user_id,
                username=user_id,
                display_name=user_id,
                role="viewer",
                ws=ws,
            )
            ws_map[(slug, user_id)] = ws

    # 同时 acquire_lock
    async def one_acquire(slug: str, user_id: str) -> bool:
        return await hub.acquire_lock(slug, user_id)

    tasks = []
    for r in range(room_count):
        slug = f"lock-room-{r:03d}"
        for u in range(users_per_room):
            user_id = f"lu-{r:03d}-{u}"
            tasks.append(one_acquire(slug, user_id))
    start = time.perf_counter()
    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - start

    ok_count = sum(1 for r in results if r)
    denied_count = len(results) - ok_count
    expected_ok = room_count  # 每房间 1 个
    expected_denied = room_count * (users_per_room - 1)

    print(f"  成功获取锁: {ok_count}（预期 {expected_ok}）")
    print(f"  被拒绝: {denied_count}（预期 {expected_denied}）")
    print(f"  总耗时: {fmt_duration(elapsed)}")

    check(
        f"成功锁数 = {expected_ok}",
        ok_count == expected_ok,
        f"got {ok_count}",
    )
    check(
        f"拒绝锁数 = {expected_denied}",
        denied_count == expected_denied,
        f"got {denied_count}",
    )
    check("无死锁（< 2s）", elapsed < 2.0, f"got {fmt_duration(elapsed)}")

    # 验证每房间恰好 1 个 lock_holder
    for r in range(room_count):
        slug = f"lock-room-{r:03d}"
        state = hub.get_room_state(slug)
        assert state is not None and state["lock_holder"] is not None, (
            f"房间 {slug} 应有锁持有者"
        )

    check("所有房间都有锁持有者", True)
    return {
        "ok_count": ok_count,
        "denied_count": denied_count,
        "elapsed": elapsed,
    }


async def scenario_broadcast_latency(hub: Any, broadcast_count: int) -> dict:
    """场景 6：广播延迟压测

    在已有 250 连接的 hub 上执行 broadcast_count 次 broadcast，
    测量延迟分布。
    """
    section(
        f"6. 广播延迟压测（{broadcast_count} 次 broadcast 到多连接房间）"
    )
    latencies: list[float] = []
    total_delivered = 0

    # 在 hub 已有房间内 broadcast
    slugs = list(hub.rooms.keys())
    if not slugs:
        check("有房间可用于广播", False, "hub.rooms 为空")
        return {}

    for i in range(broadcast_count):
        slug = slugs[i % len(slugs)]
        t0 = time.perf_counter()
        delivered = await hub.broadcast(
            slug, {"type": "stress_test", "seq": i}
        )
        latencies.append(time.perf_counter() - t0)
        total_delivered += delivered

    latencies.sort()
    p50 = statistics.median(latencies) if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0
    max_lat = max(latencies) if latencies else 0
    avg_delivered = total_delivered / broadcast_count if broadcast_count else 0

    print(f"  广播 {broadcast_count} 次，平均送达 {avg_delivered:.1f} 连接/次")
    print(
        f"  延迟: p50={fmt_duration(p50)}, p95={fmt_duration(p95)}, "
        f"p99={fmt_duration(p99)}, max={fmt_duration(max_lat)}"
    )

    check("广播有送达", total_delivered > 0, f"got {total_delivered}")
    check("p50 < 20ms", p50 < 0.02, f"got {fmt_duration(p50)}")
    check("p99 < 100ms", p99 < 0.1, f"got {fmt_duration(p99)}")

    return {
        "broadcast_count": broadcast_count,
        "total_delivered": total_delivered,
        "p50": p50,
        "p99": p99,
    }


async def scenario_memory_stress(room_count: int, conns_per_room: int) -> dict:
    """场景 7：内存压测

    tracemalloc 监控建拆 N 房间 × M 连接的内存增长。
    """
    section(
        f"7. 内存压测（{room_count} 房间 × {conns_per_room} 连接 建→拆）"
    )
    from app.realtime.collab_hub import CollabHub

    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()

    hub = CollabHub()
    # 建立
    for r in range(room_count):
        slug = f"mem-room-{r:03d}"
        for u in range(conns_per_room):
            user_id = f"mu-{r:03d}-{u}"
            ws = MockWebSocket()
            await ws.accept()
            await hub.connect(
                slug=slug,
                user_id=user_id,
                username=user_id,
                display_name=user_id,
                role="viewer",
                ws=ws,
            )
    peak_after_build = tracemalloc.get_traced_memory()[0]

    # 拆除
    for r in range(room_count):
        slug = f"mem-room-{r:03d}"
        for u in range(conns_per_room):
            user_id = f"mu-{r:03d}-{u}"
            await hub.disconnect(slug, user_id)

    snapshot_after = tracemalloc.take_snapshot()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # 计算净增长（拆除后剩余）
    diff_stats = snapshot_after.compare_to(snapshot_before, "lineno")
    total_diff = sum(s.size_diff for s in diff_stats[:10])

    total_conns = room_count * conns_per_room
    print(f"  建立后峰值: {peak_after_build / 1024:.1f} KB")
    print(f"  拆除后当前: {current / 1024:.1f} KB")
    print(f"  拆除后峰值: {peak / 1024:.1f} KB")
    print(f"  Top10 行净增长合计: {total_diff / 1024:.1f} KB")
    print(f"  每连接峰值开销: {peak_after_build / total_conns:.0f} B")

    check("建立后峰值 < 50MB", peak_after_build < 50 * 1024 * 1024)
    check("拆除后房间全部回收", len(hub.rooms) == 0, f"got {len(hub.rooms)}")
    # 拆除后净增长应较小（允许 GC 残留）
    check(
        "拆除后净增长 < 5MB",
        current < 5 * 1024 * 1024,
        f"got {current / 1024:.1f} KB",
    )

    return {
        "peak_after_build": peak_after_build,
        "current_after_teardown": current,
        "peak": peak,
        "rooms_remaining": len(hub.rooms),
    }


async def scenario_limits_guard() -> dict:
    """场景 9：上限守卫压测

    monkeypatch _get_limits 返回 (10, 5)，第 11 个房间被拒绝；
    单房间第 6 个连接被拒绝。
    """
    section("9. 上限守卫压测（max_rooms=10, max_per_room=5）")
    from app.realtime.collab_hub import CollabHub, CollabRoomFull

    hub = CollabHub()
    # monkeypatch 静态方法
    CollabHub._get_limits = staticmethod(lambda: (10, 5))  # type: ignore[assignment]

    max_rooms_rejected = 0
    max_per_room_rejected = 0

    # 创建 10 个房间（每房间 1 连接）
    for r in range(10):
        ws = MockWebSocket()
        await ws.accept()
        await hub.connect(
            slug=f"limit-room-{r:03d}",
            user_id=f"ru-{r:03d}",
            username=f"ru-{r:03d}",
            display_name=f"ru-{r:03d}",
            role="viewer",
            ws=ws,
        )

    # 第 11 个房间应被拒绝
    ws11 = MockWebSocket()
    await ws11.accept()
    try:
        await hub.connect(
            slug="limit-room-overflow",
            user_id="ru-overflow",
            username="ru-overflow",
            display_name="ru-overflow",
            role="viewer",
            ws=ws11,
        )
    except CollabRoomFull as e:
        max_rooms_rejected += 1
        check(
            "第 11 房间被拒绝 (max_rooms_exceeded)",
            e.reason == "max_rooms_exceeded",
            f"got reason={e.reason}",
        )

    check("第 11 房间拒绝触发", max_rooms_rejected == 1)

    # 在 limit-room-000 中已有 1 连接，再加 4 个（达到 5）
    for u in range(1, 5):
        ws = MockWebSocket()
        await ws.accept()
        await hub.connect(
            slug="limit-room-000",
            user_id=f"cu-{u}",
            username=f"cu-{u}",
            display_name=f"cu-{u}",
            role="viewer",
            ws=ws,
        )

    # 第 6 个连接应被拒绝
    ws6 = MockWebSocket()
    await ws6.accept()
    try:
        await hub.connect(
            slug="limit-room-000",
            user_id="cu-5",
            username="cu-5",
            display_name="cu-5",
            role="viewer",
            ws=ws6,
        )
    except CollabRoomFull as e:
        max_per_room_rejected += 1
        check(
            "第 6 连接被拒绝 (room_full)",
            e.reason == "room_full",
            f"got reason={e.reason}",
        )

    check("第 6 连接拒绝触发", max_per_room_rejected == 1)
    check(
        "limit-room-000 仍为 5 连接",
        hub.get_room_state("limit-room-000")["online_count"] == 5,
    )

    return {
        "max_rooms_rejected": max_rooms_rejected,
        "max_per_room_rejected": max_per_room_rejected,
    }


async def scenario_integration_websocket() -> dict:
    """场景 8：集成测试 - FastAPI TestClient WebSocket 端到端

    启动 FastAPI app，通过 TestClient.websocket_connect 验证：
    - 匿名连接成功
    - heartbeat → heartbeat_ack
    - acquire_lock → lock_acquired_ack
    - presence 推送
    """
    section("8. 集成测试：FastAPI WebSocket 端到端")
    try:
        from fastapi.testclient import TestClient

        from app.main import app  # noqa: F401
    except Exception as e:
        check("导入 FastAPI app 成功", False, f"err={e}")
        return {}

    try:
        client = TestClient(app)
        ok_connect = False
        ok_heartbeat = False
        ok_lock = False
        ok_presence = False

        with client.websocket_connect("/realtime/collab/integration-slug") as ws:
            ok_connect = True
            # 收取初始 user_joined + presence
            for _ in range(5):
                try:
                    msg = ws.receive_json()
                    if msg.get("type") == "presence":
                        ok_presence = True
                        break
                except Exception:
                    break
            # 发心跳
            ws.send_json({"type": "heartbeat"})
            for _ in range(5):
                try:
                    msg = ws.receive_json()
                    if msg.get("type") == "heartbeat_ack":
                        ok_heartbeat = True
                        break
                except Exception:
                    break
            # 申请锁
            ws.send_json({"type": "acquire_lock"})
            for _ in range(10):
                try:
                    msg = ws.receive_json()
                    if msg.get("type") == "lock_acquired_ack":
                        ok_lock = True
                        break
                except Exception:
                    break

        check("WebSocket 连接成功", ok_connect)
        check("收到 presence 推送", ok_presence)
        check("heartbeat_ack 回执", ok_heartbeat)
        check("lock_acquired_ack 回执", ok_lock)

        return {
            "connect": ok_connect,
            "presence": ok_presence,
            "heartbeat": ok_heartbeat,
            "lock": ok_lock,
        }
    except Exception as e:
        check("集成测试执行成功", False, f"err={e}")
        return {}


# ═══════════════ 主流程 ═══════════════


def verify_source_code() -> None:
    """1. 后端源码静态检查"""
    section("1. 后端源码静态检查")

    metrics_path = BACKEND_DIR / "app" / "observability" / "metrics.py"
    metrics_content = metrics_path.read_text(encoding="utf-8")
    check(
        'opskg_collab_rooms_total' in metrics_content,
        "metrics.py 定义 opskg_collab_rooms_total Gauge",
    )
    check(
        'opskg_collab_connections_total' in metrics_content,
        "metrics.py 定义 opskg_collab_connections_total Gauge",
    )
    check(
        'opskg_collab_messages_total' in metrics_content,
        "metrics.py 定义 opskg_collab_messages_total Counter",
    )
    check(
        'opskg_collab_broadcast_duration_seconds' in metrics_content,
        "metrics.py 定义 opskg_collab_broadcast_duration_seconds Histogram",
    )

    hub_path = BACKEND_DIR / "app" / "realtime" / "collab_hub.py"
    hub_content = hub_path.read_text(encoding="utf-8")
    check(
        "class CollabRoomFull(Exception)" in hub_content,
        "collab_hub.py 定义 CollabRoomFull 异常",
    )
    check(
        "_update_collab_gauges" in hub_content
        and "_inc_collab_messages" in hub_content
        and "_observe_broadcast_duration" in hub_content,
        "collab_hub.py 实现 3 个 metric helper 方法",
    )
    check(
        "_get_limits" in hub_content
        and "collab_max_rooms" in hub_content
        and "collab_max_connections_per_room" in hub_content,
        "collab_hub.py 实现 _get_limits 上限配置读取",
    )
    check(
        "raise CollabRoomFull" in hub_content
        and "max_rooms_exceeded" in hub_content
        and "room_full" in hub_content,
        "collab_hub.py 在 connect 中抛 CollabRoomFull（两种 reason）",
    )
    check(
        'self._update_collab_gauges()' in hub_content,
        "collab_hub.py connect/disconnect/cleanup_stale 调用 _update_collab_gauges",
    )
    check(
        'self._observe_broadcast_duration(' in hub_content
        and 'self._inc_collab_messages(' in hub_content,
        "collab_hub.py broadcast 注入延迟观察 + 消息计数",
    )

    config_path = BACKEND_DIR / "app" / "config.py"
    config_content = config_path.read_text(encoding="utf-8")
    check(
        "collab_max_rooms: int = 1000" in config_content,
        "config.py 默认 collab_max_rooms=1000",
    )
    check(
        "collab_max_connections_per_room: int = 50" in config_content,
        "config.py 默认 collab_max_connections_per_room=50",
    )

    collector_path = BACKEND_DIR / "app" / "observability" / "collector.py"
    collector_content = collector_path.read_text(encoding="utf-8")
    check(
        'record_business_metric("collab_rooms_total"' in collector_content,
        "collector.py 采集 collab_rooms_total",
    )
    check(
        "collab_connections_total" in collector_content,
        "collector.py 采集 collab_connections_total",
    )

    router_path = BACKEND_DIR / "app" / "routers" / "realtime_router.py"
    router_content = router_path.read_text(encoding="utf-8")
    check(
        "from app.realtime import CollabRoomFull" in router_content
        or "CollabRoomFull" in router_content,
        "realtime_router.py 导入 CollabRoomFull",
    )
    check(
        "except CollabRoomFull" in router_content,
        "realtime_router.py 捕获 CollabRoomFull 并关闭 WebSocket",
    )


def verify_unit_tests() -> None:
    """2. 后端单元测试"""
    section("2. 后端单元测试（pytest test_collab_hub.py）")
    code, output = run(
        ["python", "-m", "pytest", "tests/test_collab_hub.py", "-q", "--no-header"],
        cwd=BACKEND_DIR,
        timeout=120,
    )
    check("pytest 退出码 0", code == 0, f"got {code}")
    m = re.search(r"(\d+) passed", output)
    if m:
        n = int(m.group(1))
        check(
            f"通过用例数 >= 68（实际 {n}，含 6 上限 + 7 metric 新增）",
            n >= 68,
            f"got {n}",
        )

    # S16-4 测试类存在
    test_path = BACKEND_DIR / "tests" / "test_collab_hub.py"
    test_content = test_path.read_text(encoding="utf-8")
    check(
        "class TestS16_4Limits" in test_content,
        "test_collab_hub.py 含 TestS16_4Limits 测试类",
    )
    check(
        "class TestS16_4Metrics" in test_content,
        "test_collab_hub.py 含 TestS16_4Metrics 测试类",
    )
    check(
        "test_max_rooms_exceeded_rejects_new_room" in test_content,
        "测试用例 test_max_rooms_exceeded_rejects_new_room 存在",
    )
    check(
        "test_room_full_rejects_new_user" in test_content,
        "测试用例 test_room_full_rejects_new_user 存在",
    )
    check(
        "test_reconnect_does_not_trigger_room_full" in test_content,
        "测试用例 test_reconnect_does_not_trigger_room_full 存在（重连不触发上限）",
    )
    check(
        "test_broadcast_increments_message_counter" in test_content,
        "测试用例 test_broadcast_increments_message_counter 存在",
    )
    check(
        "test_broadcast_duration_observed" in test_content,
        "测试用例 test_broadcast_duration_observed 存在",
    )
    check(
        "test_cleanup_stale_updates_gauges" in test_content,
        "测试用例 test_cleanup_stale_updates_gauges 存在",
    )


async def run_async_scenarios() -> None:
    """3-9: 异步压测场景"""
    # 场景 3 + 复用 hub 到场景 4 + 6
    stats3 = await scenario_multi_room_connect(room_count=50, conns_per_room=5)
    hub = stats3["hub"]

    # 场景 4：心跳清理（复用场景 3 的 hub，250 个连接全部 stale）
    await scenario_heartbeat_cleanup(hub, stale_count=250)

    # 场景 5：锁竞争（独立 hub）
    await scenario_lock_contention(room_count=5, users_per_room=10)

    # 场景 6：广播延迟（独立 hub，重建立 50 房间 × 5 连接）
    stats6_hub = await scenario_multi_room_connect(
        room_count=20, conns_per_room=10
    )
    await scenario_broadcast_latency(stats6_hub["hub"], broadcast_count=200)

    # 场景 7：内存压测
    await scenario_memory_stress(room_count=50, conns_per_room=5)

    # 场景 9：上限守卫
    await scenario_limits_guard()

    # 场景 8：集成测试（最后跑，独立 FastAPI app）
    await scenario_integration_websocket()


def main() -> int:
    print("=" * 60)
    print("S16-4 CollabHub 多房间压测 - 验证开始")
    print("=" * 60)

    # 1. 后端源码静态检查
    verify_source_code()

    # 2. 后端单元测试
    verify_unit_tests()

    # 3-9. 异步压测场景
    asyncio.run(run_async_scenarios())

    # 总结
    print("\n" + "=" * 60)
    print(f"验证总计: {PASS} 通过 / {FAIL} 失败")
    print("=" * 60)

    if FAIL > 0:
        print("\n失败项见上方 ✗ 标记")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
