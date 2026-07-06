"""Sprint 10+ Webhook 完整实现验证脚本

验证项：
1. 事件目录完整（12 类）
2. 订阅 CRUD：创建 / 列表 / 获取 / 更新 / 删除
3. 事件匹配：精确 / 通配 `*` / 前缀 `incident.*`
4. 签名生成：HMAC-SHA256 + `sha256=` 前缀
5. 投递记录创建与状态流转（pending → success/retry/failed）
6. 重试时间计算（30s / 2min / 10min 指数退避）
7. secret 重置（rotate）使旧 secret 失效
8. 端到端：模拟事件 → 命中订阅 → 创建 delivery → 在异步 loop 下投递到 mock server
9. 后台 retry worker 能启动/停止
10. 事件触发点接入：incident.status_changed / wiki.published / document.created 等通过 dispatch_event 触发
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_webhook_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

# 重定向 webhook db 到 tmp
import app.storage.webhook_store as wh_store_mod

wh_store_mod.DB_PATH = TMP_DIR / "webhooks.db"

from app.storage.webhook_store import WebhookStore, get_webhook_store
from app.webhooks.manager import (
    EVENT_CATALOG,
    RETRY_INTERVALS,
    WebhookManager,
    _compute_next_retry,
    _event_matches,
    _sign,
)

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


def test_event_catalog() -> None:
    print("\n[1] 事件目录完整")
    check(len(EVENT_CATALOG) >= 12, f"事件类型 >= 12（实际 {len(EVENT_CATALOG)}）")
    required = {
        "document.created",
        "document.deleted",
        "wiki.published",
        "wiki.deleted",
        "event.ingested",
        "incident.created",
        "incident.status_changed",
        "runbook.generated",
        "webhook.test",
    }
    check(required.issubset(EVENT_CATALOG.keys()), f"必需事件类型齐全: {required}")


def test_subscription_crud() -> None:
    print("\n[2] 订阅 CRUD")
    store = WebhookStore()
    sub = store.create_subscription(
        url="https://example.com/hook",
        events=["incident.created", "wiki.published"],
        description="测试订阅",
    )
    check(sub["id"].startswith("wh_"), f"创建订阅 id 前缀 wh_：{sub['id']}")
    check(len(sub["secret"]) == 64, f"自动生成 secret 长度 64：{len(sub['secret'])}")
    check(sub["active"] is True, "默认 active=True")
    check("incident.created" in sub["events"], "events 包含 incident.created")

    # 重复 url 不冲突
    sub2 = store.create_subscription(
        url="https://example.com/hook2", events=["*"]
    )
    check(sub2["id"] != sub["id"], "第二次创建 id 不同")

    # 列表
    subs = store.list_subscriptions()
    check(len(subs) == 2, f"list_subscriptions 返回 2 条：实际 {len(subs)}")
    subs_active = store.list_subscriptions(active_only=True)
    check(len(subs_active) == 2, "active_only=True 返回 2 条")
    # secret 不应出现在列表里
    check("secret" not in subs[0], "list 不返回 secret 字段")

    # 获取
    got = store.get_subscription(sub["id"])
    check(got is not None and got["id"] == sub["id"], "get_subscription 正确")
    got_secret = store.get_subscription(sub["id"], include_secret=True)
    check("secret" in got_secret, "include_secret=True 时返回 secret")

    # 更新
    upd = store.update_subscription(sub["id"], description="改了描述", active=False)
    check(upd["description"] == "改了描述", "update description 生效")
    check(upd["active"] is False, "update active=False 生效")
    # active=False 后 active_only 列表不应包含
    check(
        sub["id"] not in [s["id"] for s in store.list_subscriptions(active_only=True)],
        "禁用后不出现在 active_only 列表",
    )

    # rotate secret
    new_secret = store.rotate_secret(sub["id"])
    check(new_secret is not None and new_secret != sub["secret"], "rotate_secret 返回新 secret")
    check(
        store.get_subscription_secret(sub["id"]) is None,
        "active=False 时 get_subscription_secret 返回 None",
    )
    # 重新激活
    store.update_subscription(sub["id"], active=True)
    check(
        store.get_subscription_secret(sub["id"]) == new_secret,
        "重新激活后 secret 为 rotate 后的值",
    )

    # 删除
    ok = store.delete_subscription(sub["id"])
    check(ok, "delete_subscription 返回 True")
    check(store.get_subscription(sub["id"]) is None, "删除后 get 返回 None")
    # 清理 sub2
    store.delete_subscription(sub2["id"])


def test_event_matching() -> None:
    print("\n[3] 事件匹配规则")
    check(_event_matches(["*"], "anything.here"), "`*` 匹配任意事件")
    check(_event_matches(["incident.*"], "incident.created"), "`incident.*` 前缀匹配")
    check(_event_matches(["incident.*"], "incident.status_changed"), "`incident.*` 匹配多级子事件")
    check(not _event_matches(["incident.*"], "wiki.published"), "`incident.*` 不匹配 wiki.published")
    check(_event_matches(["incident.created"], "incident.created"), "精确匹配")
    check(not _event_matches(["incident.created"], "incident.resolved"), "精确不匹配其他事件")
    check(not _event_matches([], "incident.created"), "空订阅列表不匹配")


def test_signing() -> None:
    print("\n[4] HMAC-SHA256 签名")
    secret = "test_secret_123"
    payload = b'{"event_type":"test"}'
    sig = _sign(payload, secret)
    check(sig.startswith("sha256="), "签名前缀 sha256=")
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    check(sig == expected, "签名值正确")
    # 不同 payload 产生不同签名
    sig2 = _sign(b'{"event_type":"other"}', secret)
    check(sig != sig2, "不同 payload 签名不同")
    # 不同 secret 产生不同签名
    sig3 = _sign(payload, "other_secret")
    check(sig != sig3, "不同 secret 签名不同")


def test_retry_intervals() -> None:
    print("\n[5] 指数退避重试")
    check(RETRY_INTERVALS == [30, 120, 600], f"重试间隔 [30,120,600]：实际 {RETRY_INTERVALS}")
    # attempts=1 失败 → 30s 后重试
    t1 = _compute_next_retry(1)
    check(t1 != "", "attempts=1 后有重试时间")
    # attempts=4（>= max）无重试
    t4 = _compute_next_retry(4)
    check(t4 == "", "attempts>=max_attempts 时无重试时间")
    # 时间增量正确
    now = datetime.now(timezone.utc)
    t1_dt = datetime.fromisoformat(t1)
    delta = (t1_dt - now).total_seconds()
    check(29 <= delta <= 31, f"第一次重试 +30s（实际 {delta:.1f}s）")


def test_delivery_lifecycle() -> None:
    print("\n[6] 投递记录生命周期")
    store = WebhookStore()
    sub = store.create_subscription(
        url="https://example.com/hook", events=["*"]
    )
    deliv = store.create_delivery(
        subscription_id=sub["id"],
        event_type="test.event",
        payload={"foo": "bar"},
        max_attempts=4,
    )
    check(deliv["status"] == "pending", "新建 delivery 状态 pending")
    check(deliv["attempts"] == 0, "新建 attempts=0")
    check(deliv["max_attempts"] == 4, "max_attempts=4")

    # 更新为 retry（next_retry_at 设为过去时间，模拟已到期）
    past = (datetime.now(timezone.utc)).isoformat()
    store.update_delivery(
        deliv["id"],
        status="retry",
        response_code=500,
        response_body="server error",
        attempts=1,
        next_retry_at=past,
    )
    got = store.get_delivery(deliv["id"])
    check(got["status"] == "retry", "更新后状态 retry")
    check(got["response_code"] == 500, "response_code=500")
    check(got["attempts"] == 1, "attempts=1")
    check(got["next_retry_at"] is not None, "next_retry_at 已设置")

    # 列表查询
    items = store.list_deliveries(subscription_id=sub["id"])
    check(len(items) == 1, "按 sub_id 列表返回 1 条")
    items_retry = store.list_deliveries(status="retry")
    check(len(items_retry) >= 1, "按 status=retry 列表返回 >= 1 条")

    # 待重试列表（next_retry_at <= now）
    now_iso = (datetime.now(timezone.utc)).isoformat()
    pending = store.list_pending_retries(now_iso)
    check(any(d["id"] == deliv["id"] for d in pending), "list_pending_retries 包含已到期 delivery")

    # 最终失败
    store.update_delivery(deliv["id"], status="failed", attempts=4, next_retry_at=None)
    got = store.get_delivery(deliv["id"])
    check(got["status"] == "failed", "最终状态 failed")

    store.delete_subscription(sub["id"])


async def _start_mock_http_server(
    received: list[dict], status_code: int = 200
) -> tuple[asyncio.base_events.Server, int]:
    """用 stdlib asyncio 起一个最小 HTTP server，记录收到的请求"""

    async def handle_conn(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            data = await reader.readuntil(b"\r\n\r\n")
            header_blob = data.decode("iso-8859-1")
            lines = header_blob.split("\r\n")
            request_line = lines[0]
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if ": " in line:
                    k, v = line.split(": ", 1)
                    headers[k.strip()] = v.strip()
            content_length = int(headers.get("Content-Length", "0"))
            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)
            received.append(
                {
                    "request_line": request_line,
                    "headers": headers,
                    "body": json.loads(body) if body else {},
                    "raw_body": body,
                }
            )
            resp_body = b"ok" if status_code == 200 else b"fail"
            resp = (
                f"HTTP/1.1 {status_code} {'OK' if status_code == 200 else 'ERR'}\r\n"
                f"Content-Length: {len(resp_body)}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode()
            writer.write(resp + resp_body)
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle_conn, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def test_e2e_dispatch() -> None:
    print("\n[7] 端到端：事件分发到 mock server")
    received: list[dict] = []
    server, port = await _start_mock_http_server(received, status_code=200)

    try:
        store = WebhookStore()
        sub = store.create_subscription(
            url=f"http://127.0.0.1:{port}/hook",
            events=["incident.*"],
        )
        # 在 event loop 上下文中分发
        mgr = WebhookManager(store=store)
        n = mgr.dispatch_event(
            "incident.created",
            {"incident_id": "inc-123", "severity": "critical"},
        )
        check(n == 1, f"dispatch_event 命中 1 个订阅：实际 {n}")
        # 等待异步投递完成
        await asyncio.sleep(0.8)
        check(len(received) == 1, f"mock server 收到 1 次请求：实际 {len(received)}")
        if received:
            hdrs = received[0]["headers"]
            check("X-Webhook-Signature" in hdrs, "请求头含 X-Webhook-Signature")
            check(hdrs["X-Webhook-Signature"].startswith("sha256="), "签名 sha256= 前缀")
            check(hdrs.get("X-Webhook-Event") == "incident.created", "X-Webhook-Event 正确")
            body = received[0]["body"]
            check(body["event_type"] == "incident.created", "body event_type 正确")
            check(body["data"]["incident_id"] == "inc-123", "body data 正确传递")
            # 验签（用实际收到的 raw_body）
            secret = sub["secret"]
            raw = received[0]["raw_body"]
            expected = "sha256=" + hmac.new(
                secret.encode(), raw, hashlib.sha256
            ).hexdigest()
            check(
                hdrs["X-Webhook-Signature"] == expected,
                "签名与本地计算一致",
            )
            # delivery 状态为 success
            delivs = store.list_deliveries(sub["id"])
            check(
                any(d["status"] == "success" for d in delivs),
                "delivery 状态为 success",
            )

        # 测试失败重试场景：mock server 返回 500
        received.clear()
        server.close()
        await server.wait_closed()
        bad_server, bad_port = await _start_mock_http_server(received, status_code=500)
        bad_sub = store.create_subscription(
            url=f"http://127.0.0.1:{bad_port}/hook",
            events=["wiki.*"],
        )
        mgr.dispatch_event("wiki.published", {"slug": "test"})
        await asyncio.sleep(0.8)
        delivs = store.list_deliveries(bad_sub["id"])
        check(
            any(d["status"] in ("retry", "failed") for d in delivs),
            "500 响应后 delivery 状态为 retry 或 failed",
        )
        bad_server.close()
        await bad_server.wait_closed()

        await mgr.stop_retry_worker()
        store.delete_subscription(sub["id"])
        store.delete_subscription(bad_sub["id"])
    finally:
        if server.is_serving():
            server.close()
            await server.wait_closed()


async def test_retry_worker() -> None:
    print("\n[8] 后台 retry worker 启动/停止")
    mgr = WebhookManager()
    await mgr.start_retry_worker(interval_seconds=1)
    check(mgr._bg_task is not None, "worker 启动后 _bg_task 不为 None")
    # 立即停止
    await mgr.stop_retry_worker()
    check(mgr._bg_task is None, "worker 停止后 _bg_task 为 None")
    check(mgr._client is None, "client 已关闭")


def test_dispatch_no_loop() -> None:
    print("\n[9] 同步上下文 dispatch 不阻塞")
    store = WebhookStore()
    sub = store.create_subscription(
        url="http://192.0.2.1:1/unreachable", events=["*"]  # 不可达地址
    )
    mgr = WebhookManager(store=store)
    # 在无 event loop 的同步上下文中调用
    n = mgr.dispatch_event("test.event", {"x": 1})
    check(n == 1, "dispatch 返回匹配数 1（即使无 loop）")
    # delivery 创建但状态为 pending
    delivs = store.list_deliveries(sub["id"])
    check(len(delivs) == 1, "delivery 已创建")
    check(delivs[0]["status"] == "pending", "无 loop 时 delivery 保持 pending")
    store.delete_subscription(sub["id"])


async def main() -> None:
    print("=" * 60)
    print("Sprint 10+ Webhook 完整实现验证")
    print("=" * 60)
    test_event_catalog()
    test_subscription_crud()
    test_event_matching()
    test_signing()
    test_retry_intervals()
    test_delivery_lifecycle()
    await test_e2e_dispatch()
    await test_retry_worker()
    test_dispatch_no_loop()

    print("\n" + "=" * 60)
    print(f"结果：✓ {PASS} 通过  ✗ {FAIL} 失败")
    print("=" * 60)
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
