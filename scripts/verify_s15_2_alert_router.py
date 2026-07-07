"""S15-2 告警路由规则引擎验证脚本

验证项：
1. 数据模型：alert_rules / silence_windows 两张表已建
2. WebhookStore 规则与静默窗口 CRUD
3. AlertRouter 路由引擎核心逻辑（静默 / severity / payload_matchers / 优先级 / 向后兼容）
4. payload_matchers 各 op（eq / ne / contains / regex / gt / lt / gte / lte）
5. 集成到 WebhookManager.dispatch_event（静默拦截 / 规则收窄 / 无规则向后兼容）
6. API 端点可访问（rules / silence / rules/test CRUD + dry-run）
7. 单元测试全通过（pytest tests/test_alert_router.py）
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_s15_2_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

# 重定向 webhook db 到 tmp
import app.storage.webhook_store as wh_store_mod

wh_store_mod.DB_PATH = TMP_DIR / "webhooks.db"

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


def fresh_db(name: str) -> None:
    """为每个测试切换到独立的 DB 文件，避免规则/窗口残留互相干扰。

    _get_db() 每次读取模块级 DB_PATH，故切换后存量 store 单例也会指向新文件。
    """
    wh_store_mod.DB_PATH = TMP_DIR / f"webhooks_{name}.db"


def _iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


# ═══════════════ 1. 数据模型 ═══════════════


def test_schema() -> None:
    print("\n[1] 数据模型 / 表结构")
    from app.storage.webhook_store import _get_db

    conn = _get_db()
    try:
        # alert_rules 表存在
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        check("alert_rules" in tables, "alert_rules 表已创建")
        check("silence_windows" in tables, "silence_windows 表已创建")

        # alert_rules 关键列
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(alert_rules)").fetchall()
        }
        for col in (
            "id",
            "name",
            "event_type_pattern",
            "severity",
            "payload_matchers",
            "target_subscription_ids",
            "enabled",
            "priority",
        ):
            check(col in cols, f"alert_rules 含列 {col}")

        # silence_windows 关键列
        cols2 = {
            r[1]
            for r in conn.execute("PRAGMA table_info(silence_windows)").fetchall()
        }
        for col in ("id", "name", "event_type_pattern", "start_time", "end_time", "payload_matchers", "enabled"):
            check(col in cols2, f"silence_windows 含列 {col}")

        # 索引存在
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        check("idx_alert_rules_enabled" in idx, "alert_rules enabled 索引")
        check("idx_alert_rules_priority" in idx, "alert_rules priority 索引")
        check("idx_silence_enabled" in idx, "silence_windows enabled 索引")
    finally:
        conn.close()


# ═══════════════ 2. WebhookStore CRUD ═══════════════


def test_store_crud() -> None:
    print("\n[2] WebhookStore 规则与静默窗口 CRUD")
    fresh_db("store_crud")
    from app.storage.webhook_store import WebhookStore

    store = WebhookStore()

    # 规则 CRUD
    rule = store.create_alert_rule(
        name="critical-pd",
        event_type_pattern="incident.*",
        severity="critical",
        payload_matchers=[{"field": "host", "op": "eq", "value": "prod-01"}],
        target_subscription_ids=["wh_x"],
        priority=10,
    )
    check(rule["id"].startswith("rule_"), f"规则 id 前缀 rule_：{rule['id']}")
    check(rule["priority"] == 10, "priority 存储正确")
    check(rule["payload_matchers"] == [{"field": "host", "op": "eq", "value": "prod-01"}], "payload_matchers 反序列化正确")

    got = store.get_alert_rule(rule["id"])
    check(got is not None and got["id"] == rule["id"], "get_alert_rule 正确")

    rules = store.list_alert_rules()
    check(len(rules) >= 1, f"list_alert_rules 返回 >= 1：实际 {len(rules)}")
    # 按 priority 升序
    store.create_alert_rule(name="high", event_type_pattern="*", priority=1)
    ordered = store.list_alert_rules()
    check(ordered[0]["priority"] == 1, "list_alert_rules 按 priority 升序")

    upd = store.update_alert_rule(rule["id"], severity="warning", enabled=False)
    check(upd["severity"] == "warning" and upd["enabled"] is False, "update_alert_rule 生效")

    check(store.delete_alert_rule(rule["id"]) is True, "delete_alert_rule 返回 True")
    check(store.get_alert_rule(rule["id"]) is None, "删除后 get 返回 None")

    # 静默窗口 CRUD
    win = store.create_silence_window(
        name="maint",
        event_type_pattern="*",
        start_time=_iso(-3600),
        end_time=_iso(3600),
        reason="deploy",
    )
    check(win["id"].startswith("silence_"), f"静默 id 前缀 silence_：{win['id']}")
    check(store.get_silence_window(win["id"]) is not None, "get_silence_window 正确")
    check(len(store.list_silence_windows()) >= 1, "list_silence_windows 返回 >= 1")

    upd_w = store.update_silence_window(win["id"], reason="updated")
    check(upd_w["reason"] == "updated", "update_silence_window 生效")
    check(store.delete_silence_window(win["id"]) is True, "delete_silence_window 返回 True")


# ═══════════════ 3. AlertRouter 路由引擎 ═══════════════


def test_router_engine() -> None:
    print("\n[3] AlertRouter 路由引擎核心逻辑")
    fresh_db("router_engine")
    from app.storage.webhook_store import WebhookStore
    from app.webhooks.alert_router import AlertRouter

    store = WebhookStore()
    router = AlertRouter(store)

    # 向后兼容：无规则返回原列表
    subs = [{"id": "wh_a"}, {"id": "wh_b"}, {"id": "wh_pd"}]
    routed = router.route("incident.created", {}, subs)
    check(routed == subs, "无规则时返回原订阅列表（向后兼容）")

    # 静默：无窗口不静默
    check(router.is_silenced("incident.created", {}) is False, "无静默窗口时不静默")

    # 建静默窗口
    store.create_silence_window(
        name="maint",
        event_type_pattern="incident.*",
        start_time=_iso(-3600),
        end_time=_iso(3600),
    )
    check(router.is_silenced("incident.created", {}) is True, "活动窗口内 incident.* 静默")
    check(router.is_silenced("wiki.published", {}) is False, "incident.* 窗口不影响 wiki.published")

    # 删除静默窗口后不再静默
    wins = store.list_silence_windows()
    for w in wins:
        store.delete_silence_window(w["id"])
    check(router.is_silenced("incident.created", {}) is False, "删除窗口后不再静默")

    # severity 路由：critical → wh_pd
    store.create_alert_rule(
        name="critical-to-pd",
        event_type_pattern="incident.*",
        severity="critical",
        target_subscription_ids=["wh_pd"],
        priority=10,
    )
    routed = router.route("incident.created", {"severity": "critical"}, subs)
    check([s["id"] for s in routed] == ["wh_pd"], "severity=critical 命中 → 只投 wh_pd")
    routed = router.route("incident.created", {"severity": "warning"}, subs)
    check(len(routed) == 3, "severity=warning 不命中 critical 规则 → 返回全部")

    # payload_matchers：host=prod-01 → wh_a
    store.create_alert_rule(
        name="prod-host",
        event_type_pattern="incident.*",
        payload_matchers=[{"field": "host", "op": "eq", "value": "prod-01"}],
        target_subscription_ids=["wh_a"],
        priority=20,
    )
    # payload 同时满足 critical + host=prod-01 → 两条规则命中取并集 {wh_pd, wh_a}
    routed = router.route(
        "incident.created", {"severity": "critical", "host": "prod-01"}, subs
    )
    ids = sorted(s["id"] for s in routed)
    check(ids == ["wh_a", "wh_pd"], f"多条规则命中取并集：实际 {ids}")


# ═══════════════ 4. payload_matchers 各 op ═══════════════


def test_payload_ops() -> None:
    print("\n[4] payload_matchers 各 op")
    from app.webhooks.alert_router import _match_payload

    check(_match_payload([{"field": "h", "op": "eq", "value": "x"}], {"h": "x"}) is True, "eq 匹配")
    check(_match_payload([{"field": "h", "op": "ne", "value": "x"}], {"h": "y"}) is True, "ne 匹配")
    check(_match_payload([{"field": "h", "op": "contains", "value": "err"}], {"h": "an error"}) is True, "contains 字符串")
    check(_match_payload([{"field": "h", "op": "contains", "value": "db"}], {"h": ["web", "db"]}) is True, "contains 列表")
    check(_match_payload([{"field": "h", "op": "regex", "value": r"^prod-\d+$"}], {"h": "prod-01"}) is True, "regex 匹配")
    check(_match_payload([{"field": "h", "op": "gt", "value": 10}], {"h": 20}) is True, "gt 匹配")
    check(_match_payload([{"field": "h", "op": "lt", "value": 10}], {"h": 5}) is True, "lt 匹配")
    check(_match_payload([{"field": "h", "op": "gte", "value": 10}], {"h": 10}) is True, "gte 匹配")
    check(_match_payload([{"field": "h", "op": "lte", "value": 10}], {"h": 10}) is True, "lte 匹配")
    # AND 关系
    matchers = [
        {"field": "a", "op": "eq", "value": 1},
        {"field": "b", "op": "gt", "value": 5},
    ]
    check(_match_payload(matchers, {"a": 1, "b": 10}) is True, "多 matcher AND 全匹配")
    check(_match_payload(matchers, {"a": 1, "b": 1}) is False, "多 matcher AND 任一不匹配")


# ═══════════════ 5. 集成到 dispatch_event ═══════════════


def test_dispatch_integration() -> None:
    print("\n[5] 集成到 WebhookManager.dispatch_event")
    fresh_db("dispatch")
    from app.storage.webhook_store import WebhookStore
    from app.webhooks.manager import WebhookManager

    store = WebhookStore()
    sub_a = store.create_subscription(url="https://a.com", events=["incident.*"])
    store.create_subscription(url="https://b.com", events=["incident.*"])

    mgr = WebhookManager(store=store)

    # 无规则：两个订阅都收到
    n = mgr.dispatch_event("incident.created", {"x": 1})
    check(n == 2, f"无规则 dispatch 命中 2：实际 {n}")

    # 建静默窗口 → dispatch 返回 0
    win = store.create_silence_window(
        name="maint",
        event_type_pattern="*",
        start_time=_iso(-3600),
        end_time=_iso(3600),
    )
    n2 = mgr.dispatch_event("incident.created", {"x": 1})
    check(n2 == 0, f"静默窗口命中 dispatch 返回 0：实际 {n2}")
    store.delete_silence_window(win["id"])

    # 建路由规则收窄 → 只投 sub_a
    rule = store.create_alert_rule(
        name="to-a",
        event_type_pattern="incident.*",
        target_subscription_ids=[sub_a["id"]],
    )
    n3 = mgr.dispatch_event("incident.created", {"x": 1})
    check(n3 == 1, f"路由规则收窄 dispatch 命中 1：实际 {n3}")
    store.delete_alert_rule(rule["id"])


# ═══════════════ 6. API 端点 ═══════════════


def test_api_endpoints() -> None:
    print("\n[6] API 端点可访问")
    fresh_db("api")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    # rules CRUD
    resp = client.post(
        "/webhooks/rules",
        json={"name": "api-rule", "event_type_pattern": "incident.*", "priority": 5},
    )
    check(resp.status_code == 200, f"POST /webhooks/rules 200：实际 {resp.status_code}")
    rule_id = resp.json()["id"]

    resp = client.get("/webhooks/rules")
    check(resp.status_code == 200 and resp.json()["count"] >= 1, "GET /webhooks/rules 200")

    resp = client.get(f"/webhooks/rules/{rule_id}")
    check(resp.status_code == 200, "GET /webhooks/rules/{id} 200")

    resp = client.put(f"/webhooks/rules/{rule_id}", json={"priority": 1})
    check(resp.status_code == 200 and resp.json()["priority"] == 1, "PUT /webhooks/rules/{id} 200")

    # rules/test dry-run
    client.post("/webhooks", json={"url": "https://h.com", "events": ["incident.*"]})
    resp = client.post(
        "/webhooks/rules/test",
        json={"event_type": "incident.created", "payload": {"severity": "critical"}},
    )
    check(resp.status_code == 200, f"POST /webhooks/rules/test 200：实际 {resp.status_code}")
    check(resp.json()["silenced"] is False, "dry-run 未静默")

    resp = client.delete(f"/webhooks/rules/{rule_id}")
    check(resp.status_code == 200, "DELETE /webhooks/rules/{id} 200")

    # silence CRUD
    resp = client.post(
        "/webhooks/silence",
        json={
            "name": "api-silence",
            "event_type_pattern": "*",
            "start_time": _iso(-3600),
            "end_time": _iso(3600),
        },
    )
    check(resp.status_code == 200, f"POST /webhooks/silence 200：实际 {resp.status_code}")
    win_id = resp.json()["id"]

    resp = client.get("/webhooks/silence")
    check(resp.status_code == 200, "GET /webhooks/silence 200")

    resp = client.get(f"/webhooks/silence/{win_id}")
    check(resp.status_code == 200, "GET /webhooks/silence/{id} 200")

    resp = client.put(f"/webhooks/silence/{win_id}", json={"reason": "x"})
    check(resp.status_code == 200, "PUT /webhooks/silence/{id} 200")

    resp = client.delete(f"/webhooks/silence/{win_id}")
    check(resp.status_code == 200, "DELETE /webhooks/silence/{id} 200")


# ═══════════════ 7. 单元测试 ═══════════════


def test_unit_tests() -> None:
    print("\n[7] 单元测试 tests/test_alert_router.py")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_alert_router.py", "-q", "--tb=short"],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
        env=env,
    )
    # 提取末尾总结行
    tail = result.stdout.strip().splitlines()[-3:] if result.stdout else []
    for line in tail:
        print(f"    {line}")
    check(result.returncode == 0, f"pytest 退出码 0：实际 {result.returncode}")
    if result.returncode != 0:
        # 打印部分失败信息便于排查
        for line in result.stdout.splitlines()[-20:]:
            print(f"    {line}")
        if result.stderr:
            for line in result.stderr.splitlines()[-10:]:
                print(f"    [stderr] {line}")


def main() -> None:
    print("=" * 60)
    print("S15-2 告警路由规则引擎验证")
    print("=" * 60)
    test_schema()
    test_store_crud()
    test_router_engine()
    test_payload_ops()
    test_dispatch_integration()
    test_api_endpoints()
    test_unit_tests()

    print("\n" + "=" * 60)
    print(f"结果：✓ {PASS} 通过  ✗ {FAIL} 失败")
    print("=" * 60)
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
