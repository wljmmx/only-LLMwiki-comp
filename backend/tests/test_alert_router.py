"""告警路由规则引擎单元测试（S15-2）

覆盖：
- 静默窗口匹配（时间范围 / 事件类型 / payload）
- 路由规则匹配（severity / payload_matchers / target_subscription_ids）
- payload_matchers 各种 op（eq / ne / contains / regex / gt / lt / gte / lte）
- 优先级排序
- 集成测试（dispatch_event 经过路由引擎）
- 向后兼容（无规则时行为不变）
- WebhookStore 规则与静默窗口 CRUD
- API 端点（TestClient）

DB 隔离：每个测试通过 monkeypatch 将 webhooks.db 重定向到 tmp_path。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════ 公共 fixture ═══════════════


@pytest.fixture
def isolated_webhook_db(tmp_path, monkeypatch):
    """将 webhooks 数据库重定向到 tmp_path，并重置 store/manager 单例"""
    import app.storage.webhook_store as wstore
    import app.webhooks.manager as wmanager

    db_file = tmp_path / "webhooks.db"
    monkeypatch.setattr(wstore, "DB_PATH", db_file)
    if hasattr(wstore, "_store"):
        monkeypatch.setattr(wstore, "_store", None)
    monkeypatch.setattr(wmanager, "_manager", None)
    yield db_file


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_offset(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


# ═══════════════ _event_type_matches ═══════════════


class TestEventTypeMatches:
    def test_wildcard_star(self):
        from app.webhooks.alert_router import _event_type_matches

        assert _event_type_matches("*", "anything.at.all") is True

    def test_exact(self):
        from app.webhooks.alert_router import _event_type_matches

        assert _event_type_matches("incident.created", "incident.created") is True
        assert _event_type_matches("incident.created", "incident.resolved") is False

    def test_prefix_wildcard(self):
        from app.webhooks.alert_router import _event_type_matches

        assert _event_type_matches("incident.*", "incident.created") is True
        assert _event_type_matches("incident.*", "incident.status_changed") is True
        assert _event_type_matches("incident.*", "wiki.published") is False

    def test_empty_pattern(self):
        from app.webhooks.alert_router import _event_type_matches

        assert _event_type_matches("", "incident.created") is False


# ═══════════════ _match_payload 各 op ═══════════════


class TestMatchPayloadOps:
    def test_eq_match(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "host", "op": "eq", "value": "prod-01"}], {"host": "prod-01"}) is True

    def test_eq_no_match(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "host", "op": "eq", "value": "prod-01"}], {"host": "prod-02"}) is False

    def test_eq_field_missing(self):
        from app.webhooks.alert_router import _match_payload

        # 字段缺失，actual=None，eq 与 "prod-01" 不等
        assert _match_payload([{"field": "host", "op": "eq", "value": "prod-01"}], {}) is False

    def test_ne_match(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "env", "op": "ne", "value": "prod"}], {"env": "staging"}) is True

    def test_ne_no_match(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "env", "op": "ne", "value": "prod"}], {"env": "prod"}) is False

    def test_ne_field_missing(self):
        from app.webhooks.alert_router import _match_payload

        # 字段缺失 actual=None，None != "prod" → True
        assert _match_payload([{"field": "env", "op": "ne", "value": "prod"}], {}) is True

    def test_contains_string(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "msg", "op": "contains", "value": "error"}], {"msg": "disk error occurred"}) is True
        assert _match_payload([{"field": "msg", "op": "contains", "value": "warn"}], {"msg": "disk error"}) is False

    def test_contains_list(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "tags", "op": "contains", "value": "db"}], {"tags": ["web", "db", "cache"]}) is True
        assert _match_payload([{"field": "tags", "op": "contains", "value": "mq"}], {"tags": ["web", "db"]}) is False

    def test_regex_match(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "host", "op": "regex", "value": r"^prod-\d+$"}], {"host": "prod-01"}) is True
        assert _match_payload([{"field": "host", "op": "regex", "value": r"^prod-\d+$"}], {"host": "staging-01"}) is False

    def test_regex_partial_match(self):
        from app.webhooks.alert_router import _match_payload

        # re.search 部分匹配即可
        assert _match_payload([{"field": "msg", "op": "regex", "value": r"error"}], {"msg": "an error here"}) is True

    def test_regex_invalid_pattern(self):
        from app.webhooks.alert_router import _match_payload

        # 非法正则不抛错，返回 False
        assert _match_payload([{"field": "msg", "op": "regex", "value": r"("}], {"msg": "abc"}) is False

    def test_gt_match(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "latency", "op": "gt", "value": 100}], {"latency": 200}) is True
        assert _match_payload([{"field": "latency", "op": "gt", "value": 100}], {"latency": 50}) is False

    def test_lt_match(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "latency", "op": "lt", "value": 100}], {"latency": 50}) is True
        assert _match_payload([{"field": "latency", "op": "lt", "value": 100}], {"latency": 200}) is False

    def test_gte_match(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "latency", "op": "gte", "value": 100}], {"latency": 100}) is True
        assert _match_payload([{"field": "latency", "op": "gte", "value": 100}], {"latency": 99}) is False

    def test_lte_match(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "latency", "op": "lte", "value": 100}], {"latency": 100}) is True
        assert _match_payload([{"field": "latency", "op": "lte", "value": 100}], {"latency": 101}) is False

    def test_numeric_string_value(self):
        from app.webhooks.alert_router import _match_payload

        # 数值 op 能将字符串 "200" 转 float
        assert _match_payload([{"field": "latency", "op": "gt", "value": "100"}], {"latency": "200"}) is True

    def test_numeric_non_convertible(self):
        from app.webhooks.alert_router import _match_payload

        # 无法转 float → False
        assert _match_payload([{"field": "latency", "op": "gt", "value": 100}], {"latency": "abc"}) is False

    def test_multiple_matchers_and(self):
        from app.webhooks.alert_router import _match_payload

        matchers = [
            {"field": "host", "op": "eq", "value": "prod-01"},
            {"field": "severity", "op": "eq", "value": "critical"},
            {"field": "latency", "op": "gt", "value": 100},
        ]
        payload = {"host": "prod-01", "severity": "critical", "latency": 200}
        assert _match_payload(matchers, payload) is True
        # 任一不匹配 → False
        payload["host"] = "prod-02"
        assert _match_payload(matchers, payload) is False

    def test_empty_matchers_always_match(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([], {}) is True
        assert _match_payload([], {"any": "thing"}) is True

    def test_unsupported_op(self):
        from app.webhooks.alert_router import _match_payload

        assert _match_payload([{"field": "x", "op": "weird", "value": 1}], {"x": 1}) is False

    def test_invalid_matcher_structure(self):
        from app.webhooks.alert_router import _match_payload

        # 非 dict matcher
        assert _match_payload(["notadict"], {"x": 1}) is False
        # 缺 field
        assert _match_payload([{"op": "eq", "value": 1}], {"x": 1}) is False
        # 缺 op
        assert _match_payload([{"field": "x", "value": 1}], {"x": 1}) is False


# ═══════════════ WebhookStore 规则 CRUD ═══════════════


class TestWebhookStoreAlertRules:
    def test_create_alert_rule(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        rule = store.create_alert_rule(
            name="critical-to-pagerduty",
            event_type_pattern="incident.*",
            severity="critical",
            payload_matchers=[{"field": "host", "op": "eq", "value": "prod-01"}],
            target_subscription_ids=["wh_aaa"],
            priority=10,
        )
        assert rule["id"].startswith("rule_")
        assert rule["name"] == "critical-to-pagerduty"
        assert rule["event_type_pattern"] == "incident.*"
        assert rule["severity"] == "critical"
        assert rule["priority"] == 10
        assert rule["enabled"] is True
        assert rule["payload_matchers"] == [{"field": "host", "op": "eq", "value": "prod-01"}]
        assert rule["target_subscription_ids"] == ["wh_aaa"]

    def test_create_alert_rule_missing_required(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        with pytest.raises(ValueError):
            store.create_alert_rule(name="", event_type_pattern="incident.*")
        with pytest.raises(ValueError):
            store.create_alert_rule(name="x", event_type_pattern="")

    def test_list_alert_rules_ordered_by_priority(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        store.create_alert_rule(name="low", event_type_pattern="*", priority=200)
        store.create_alert_rule(name="high", event_type_pattern="*", priority=10)
        store.create_alert_rule(name="mid", event_type_pattern="*", priority=100)
        rules = store.list_alert_rules()
        assert [r["name"] for r in rules] == ["high", "mid", "low"]

    def test_list_alert_rules_enabled_only(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        store.create_alert_rule(name="on", event_type_pattern="*", enabled=True)
        store.create_alert_rule(name="off", event_type_pattern="*", enabled=False)
        enabled = store.list_alert_rules(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0]["name"] == "on"

    def test_get_alert_rule(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        created = store.create_alert_rule(name="r", event_type_pattern="*")
        got = store.get_alert_rule(created["id"])
        assert got is not None
        assert got["id"] == created["id"]
        assert store.get_alert_rule("rule_nonexistent") is None

    def test_update_alert_rule(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        created = store.create_alert_rule(name="r", event_type_pattern="*")
        updated = store.update_alert_rule(
            created["id"],
            severity="critical",
            priority=5,
            target_subscription_ids=["wh_x"],
            enabled=False,
        )
        assert updated["severity"] == "critical"
        assert updated["priority"] == 5
        assert updated["target_subscription_ids"] == ["wh_x"]
        assert updated["enabled"] is False

    def test_update_alert_rule_not_found(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        assert store.update_alert_rule("rule_nope", name="x") is None

    def test_delete_alert_rule(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        created = store.create_alert_rule(name="r", event_type_pattern="*")
        assert store.delete_alert_rule(created["id"]) is True
        assert store.get_alert_rule(created["id"]) is None
        assert store.delete_alert_rule(created["id"]) is False


# ═══════════════ WebhookStore 静默窗口 CRUD ═══════════════


class TestWebhookStoreSilenceWindows:
    def test_create_silence_window(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        win = store.create_silence_window(
            name="维护窗口",
            event_type_pattern="incident.*",
            start_time=_iso_offset(-60),
            end_time=_iso_offset(3600),
            reason="deploy",
            payload_matchers=[{"field": "env", "op": "eq", "value": "prod"}],
        )
        assert win["id"].startswith("silence_")
        assert win["name"] == "维护窗口"
        assert win["reason"] == "deploy"
        assert win["enabled"] is True
        assert win["payload_matchers"] == [{"field": "env", "op": "eq", "value": "prod"}]

    def test_create_silence_window_missing_required(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        with pytest.raises(ValueError):
            store.create_silence_window(name="", event_type_pattern="*", start_time=_now_iso(), end_time=_now_iso())
        with pytest.raises(ValueError):
            store.create_silence_window(name="x", event_type_pattern="", start_time=_now_iso(), end_time=_now_iso())
        with pytest.raises(ValueError):
            store.create_silence_window(name="x", event_type_pattern="*", start_time="", end_time="")

    def test_create_silence_window_invalid_time(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        with pytest.raises(ValueError):
            store.create_silence_window(
                name="x", event_type_pattern="*", start_time="not-a-time", end_time=_now_iso()
            )

    def test_list_silence_windows(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        store.create_silence_window(name="a", event_type_pattern="*", start_time=_iso_offset(0), end_time=_iso_offset(100))
        store.create_silence_window(name="b", event_type_pattern="*", start_time=_iso_offset(-100), end_time=_iso_offset(0), enabled=False)
        all_wins = store.list_silence_windows()
        assert len(all_wins) == 2
        enabled = store.list_silence_windows(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0]["name"] == "a"

    def test_get_silence_window(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        win = store.create_silence_window(name="x", event_type_pattern="*", start_time=_now_iso(), end_time=_now_iso())
        got = store.get_silence_window(win["id"])
        assert got is not None
        assert got["id"] == win["id"]
        assert store.get_silence_window("silence_nope") is None

    def test_update_silence_window(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        win = store.create_silence_window(name="x", event_type_pattern="*", start_time=_now_iso(), end_time=_now_iso())
        updated = store.update_silence_window(win["id"], reason="updated", enabled=False)
        assert updated["reason"] == "updated"
        assert updated["enabled"] is False

    def test_delete_silence_window(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        win = store.create_silence_window(name="x", event_type_pattern="*", start_time=_now_iso(), end_time=_now_iso())
        assert store.delete_silence_window(win["id"]) is True
        assert store.get_silence_window(win["id"]) is None
        assert store.delete_silence_window(win["id"]) is False


# ═══════════════ AlertRouter.is_silenced ═══════════════


class TestAlertRouterSilenced:
    def test_no_windows_not_silenced(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        router = AlertRouter(store)
        assert router.is_silenced("incident.created", {"severity": "critical"}) is False

    def test_active_window_silences(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_silence_window(
            name="maint",
            event_type_pattern="incident.*",
            start_time=_iso_offset(-3600),
            end_time=_iso_offset(3600),
        )
        router = AlertRouter(store)
        assert router.is_silenced("incident.created", {}) is True

    def test_past_window_not_silenced(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_silence_window(
            name="past",
            event_type_pattern="incident.*",
            start_time=_iso_offset(-7200),
            end_time=_iso_offset(-3600),
        )
        router = AlertRouter(store)
        assert router.is_silenced("incident.created", {}) is False

    def test_future_window_not_silenced(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_silence_window(
            name="future",
            event_type_pattern="incident.*",
            start_time=_iso_offset(3600),
            end_time=_iso_offset(7200),
        )
        router = AlertRouter(store)
        assert router.is_silenced("incident.created", {}) is False

    def test_event_type_mismatch_not_silenced(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_silence_window(
            name="incident-only",
            event_type_pattern="incident.*",
            start_time=_iso_offset(-3600),
            end_time=_iso_offset(3600),
        )
        router = AlertRouter(store)
        # wiki.published 不匹配 incident.*
        assert router.is_silenced("wiki.published", {}) is False

    def test_wildstar_window_silences_all(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_silence_window(
            name="all",
            event_type_pattern="*",
            start_time=_iso_offset(-3600),
            end_time=_iso_offset(3600),
        )
        router = AlertRouter(store)
        assert router.is_silenced("any.event", {}) is True

    def test_disabled_window_not_silenced(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_silence_window(
            name="disabled",
            event_type_pattern="*",
            start_time=_iso_offset(-3600),
            end_time=_iso_offset(3600),
            enabled=False,
        )
        router = AlertRouter(store)
        assert router.is_silenced("incident.created", {}) is False

    def test_payload_matcher_silences(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_silence_window(
            name="prod-only",
            event_type_pattern="incident.*",
            start_time=_iso_offset(-3600),
            end_time=_iso_offset(3600),
            payload_matchers=[{"field": "env", "op": "eq", "value": "prod"}],
        )
        router = AlertRouter(store)
        # env=prod 命中
        assert router.is_silenced("incident.created", {"env": "prod"}) is True
        # env=staging 不命中
        assert router.is_silenced("incident.created", {"env": "staging"}) is False


# ═══════════════ AlertRouter.route ═══════════════


class TestAlertRouterRoute:
    def test_no_rules_returns_all(self, isolated_webhook_db):
        """无规则时返回原订阅列表（向后兼容）"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}]
        routed = router.route("incident.created", {}, subs)
        assert routed == subs

    def test_rule_no_target_returns_all(self, isolated_webhook_db):
        """规则匹配但 target_subscription_ids 为空 → 保留全部"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(name="all", event_type_pattern="incident.*")
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}]
        routed = router.route("incident.created", {}, subs)
        assert len(routed) == 2

    def test_rule_target_filters(self, isolated_webhook_db):
        """规则指定 target → 只保留这些订阅"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(
            name="to-pd",
            event_type_pattern="incident.*",
            target_subscription_ids=["wh_a"],
            priority=10,
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}, {"id": "wh_c"}]
        routed = router.route("incident.created", {}, subs)
        assert [s["id"] for s in routed] == ["wh_a"]

    def test_rule_target_nonexistent_ignored(self, isolated_webhook_db):
        """target 中包含不存在的订阅 ID → 只保留候选集合内的"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(
            name="to-pd",
            event_type_pattern="incident.*",
            target_subscription_ids=["wh_a", "wh_zzzz"],  # wh_zzzz 不在候选
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}]
        routed = router.route("incident.created", {}, subs)
        assert [s["id"] for s in routed] == ["wh_a"]

    def test_severity_filter_match(self, isolated_webhook_db):
        """severity=critical 规则只匹配 payload.severity=critical"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(
            name="critical",
            event_type_pattern="incident.*",
            severity="critical",
            target_subscription_ids=["wh_a"],
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}]
        # severity=critical 命中 → 只投 wh_a
        routed = router.route("incident.created", {"severity": "critical"}, subs)
        assert [s["id"] for s in routed] == ["wh_a"]

    def test_severity_filter_no_match_returns_all(self, isolated_webhook_db):
        """规则 severity 不匹配 → 该规则不命中；无其他规则 → 返回全部（向后兼容）"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(
            name="critical",
            event_type_pattern="incident.*",
            severity="critical",
            target_subscription_ids=["wh_a"],
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}]
        # severity=warning 不命中 critical 规则 → 无规则命中 → 返回全部
        routed = router.route("incident.created", {"severity": "warning"}, subs)
        assert len(routed) == 2

    def test_severity_missing_in_payload(self, isolated_webhook_db):
        """payload 无 severity 字段，规则要求 severity → 规则不命中"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(
            name="critical",
            event_type_pattern="incident.*",
            severity="critical",
            target_subscription_ids=["wh_a"],
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}]
        # payload 无 severity → 规则不命中 → 返回全部
        routed = router.route("incident.created", {}, subs)
        assert len(routed) == 2

    def test_payload_matchers_filter(self, isolated_webhook_db):
        """payload_matchers 匹配才命中规则"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(
            name="prod-host",
            event_type_pattern="incident.*",
            payload_matchers=[{"field": "host", "op": "eq", "value": "prod-01"}],
            target_subscription_ids=["wh_a"],
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}]
        # host=prod-01 命中
        routed = router.route("incident.created", {"host": "prod-01"}, subs)
        assert [s["id"] for s in routed] == ["wh_a"]
        # host=prod-02 不命中 → 返回全部
        routed = router.route("incident.created", {"host": "prod-02"}, subs)
        assert len(routed) == 2

    def test_multiple_rules_union(self, isolated_webhook_db):
        """多条规则命中 → target 取并集"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(
            name="rule-a",
            event_type_pattern="incident.*",
            target_subscription_ids=["wh_a"],
            priority=10,
        )
        store.create_alert_rule(
            name="rule-b",
            event_type_pattern="incident.*",
            target_subscription_ids=["wh_b"],
            priority=20,
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}, {"id": "wh_c"}]
        routed = router.route("incident.created", {}, subs)
        ids = sorted(s["id"] for s in routed)
        assert ids == ["wh_a", "wh_b"]

    def test_priority_order_empty_target_breaks(self, isolated_webhook_db):
        """高优先级规则空 target（保留全部）应短路后续规则"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        # priority=10 空 target（保留全部）
        store.create_alert_rule(name="all", event_type_pattern="incident.*", priority=10)
        # priority=20 收窄到 wh_a（应被短路，不生效）
        store.create_alert_rule(
            name="narrow",
            event_type_pattern="incident.*",
            target_subscription_ids=["wh_a"],
            priority=20,
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}]
        routed = router.route("incident.created", {}, subs)
        assert len(routed) == 2  # 全部保留

    def test_event_type_pattern_mismatch(self, isolated_webhook_db):
        """事件类型不匹配规则模式 → 规则不命中"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(
            name="incident-rule",
            event_type_pattern="incident.*",
            target_subscription_ids=["wh_a"],
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}]
        # wiki.published 不匹配 incident.* → 规则不命中 → 返回全部
        routed = router.route("wiki.published", {}, subs)
        assert len(routed) == 2

    def test_disabled_rule_ignored(self, isolated_webhook_db):
        """禁用的规则不参与路由"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(
            name="disabled",
            event_type_pattern="incident.*",
            target_subscription_ids=["wh_a"],
            enabled=False,
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}]
        # 禁用规则不生效 → 无启用规则 → 返回全部
        routed = router.route("incident.created", {}, subs)
        assert len(routed) == 2

    def test_order_preserved(self, isolated_webhook_db):
        """路由结果保持原 all_subs 顺序"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(
            name="union",
            event_type_pattern="*",
            target_subscription_ids=["wh_c", "wh_a"],
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}, {"id": "wh_c"}]
        routed = router.route("any.event", {}, subs)
        # 保持原顺序 a, c（b 被过滤）
        assert [s["id"] for s in routed] == ["wh_a", "wh_c"]


# ═══════════════ AlertRouter.evaluate（dry-run） ═══════════════


class TestAlertRouterEvaluate:
    def test_evaluate_silenced(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        win = store.create_silence_window(
            name="maint",
            event_type_pattern="*",
            start_time=_iso_offset(-3600),
            end_time=_iso_offset(3600),
        )
        router = AlertRouter(store)
        result = router.evaluate("incident.created", {}, [{"id": "wh_a"}])
        assert result["silenced"] is True
        assert result["silenced_by"]["id"] == win["id"]
        assert result["routed_subscription_count"] == 0

    def test_evaluate_not_silenced_with_rules(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.alert_router import AlertRouter

        store = get_webhook_store()
        store.create_alert_rule(
            name="r",
            event_type_pattern="incident.*",
            target_subscription_ids=["wh_a"],
        )
        router = AlertRouter(store)
        subs = [{"id": "wh_a"}, {"id": "wh_b"}]
        result = router.evaluate("incident.created", {}, subs)
        assert result["silenced"] is False
        assert result["matched_subscription_count"] == 2
        assert result["routed_subscription_count"] == 1
        assert result["routed_subscription_ids"] == ["wh_a"]
        assert len(result["matched_rules"]) == 1


# ═══════════════ 集成测试：dispatch_event 经过路由引擎 ═══════════════


class TestDispatchIntegration:
    def test_dispatch_silenced_returns_zero(self, isolated_webhook_db):
        """静默窗口命中 → dispatch_event 返回 0，不创建 delivery"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.manager import get_webhook_manager

        store = get_webhook_store()
        sub = store.create_subscription(url="https://x.com", events=["*"])
        store.create_silence_window(
            name="maint",
            event_type_pattern="*",
            start_time=_iso_offset(-3600),
            end_time=_iso_offset(3600),
        )
        mgr = get_webhook_manager()
        n = mgr.dispatch_event("incident.created", {"severity": "critical"})
        assert n == 0
        # 不应有 delivery
        assert store.list_deliveries(sub["id"]) == []

    def test_dispatch_with_target_rule_filters(self, isolated_webhook_db):
        """路由规则收窄订阅 → 只给目标订阅创建 delivery"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.manager import get_webhook_manager

        store = get_webhook_store()
        sub_a = store.create_subscription(url="https://a.com", events=["incident.*"])
        sub_b = store.create_subscription(url="https://b.com", events=["incident.*"])
        store.create_alert_rule(
            name="to-a",
            event_type_pattern="incident.*",
            target_subscription_ids=[sub_a["id"]],
        )
        mgr = get_webhook_manager()
        n = mgr.dispatch_event("incident.created", {"x": 1})
        assert n == 1
        # 只有 sub_a 有 delivery
        assert len(store.list_deliveries(sub_a["id"])) == 1
        assert store.list_deliveries(sub_b["id"]) == []

    def test_dispatch_no_rules_backward_compat(self, isolated_webhook_db):
        """无规则时 dispatch 行为不变（所有匹配订阅都收到投递）"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.manager import get_webhook_manager

        store = get_webhook_store()
        _sub_a = store.create_subscription(url="https://a.com", events=["incident.*"])
        _sub_b = store.create_subscription(url="https://b.com", events=["incident.*"])
        mgr = get_webhook_manager()
        n = mgr.dispatch_event("incident.created", {"x": 1})
        assert n == 2

    def test_dispatch_severity_rule(self, isolated_webhook_db):
        """severity=critical 规则只对 critical 事件生效"""
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.manager import get_webhook_manager

        store = get_webhook_store()
        sub_pd = store.create_subscription(url="https://pd.com", events=["incident.*"])
        _sub_slack = store.create_subscription(url="https://slack.com", events=["incident.*"])
        # critical → PagerDuty
        store.create_alert_rule(
            name="critical-to-pd",
            event_type_pattern="incident.*",
            severity="critical",
            target_subscription_ids=[sub_pd["id"]],
            priority=10,
        )
        mgr = get_webhook_manager()
        # critical 事件 → 只投 pd
        n = mgr.dispatch_event("incident.created", {"severity": "critical"})
        assert n == 1
        # warning 事件 → 规则不命中 → 全部投递（向后兼容）
        n2 = mgr.dispatch_event("incident.created", {"severity": "warning"})
        assert n2 == 2


# ═══════════════ API 端点 ═══════════════


class TestAPIEndpoints:
    @pytest.fixture
    def client(self, isolated_webhook_db):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_rules_crud_via_api(self, client):
        # 创建
        resp = client.post(
            "/api/v1/webhooks/rules",
            json={
                "name": "test-rule",
                "event_type_pattern": "incident.*",
                "severity": "critical",
                "payload_matchers": [{"field": "host", "op": "eq", "value": "prod-01"}],
                "target_subscription_ids": ["wh_a"],
                "priority": 10,
            },
        )
        assert resp.status_code == 200, resp.text
        rule = resp.json()
        assert rule["name"] == "test-rule"
        rule_id = rule["id"]

        # 列表
        resp = client.get("/api/v1/webhooks/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        assert any(r["id"] == rule_id for r in data["rules"])

        # 获取
        resp = client.get(f"/api/v1/webhooks/rules/{rule_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == rule_id

        # 更新
        resp = client.put(
            f"/api/v1/webhooks/rules/{rule_id}",
            json={"severity": "warning", "priority": 5},
        )
        assert resp.status_code == 200
        assert resp.json()["severity"] == "warning"
        assert resp.json()["priority"] == 5

        # 删除
        resp = client.delete(f"/api/v1/webhooks/rules/{rule_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # 再获取 404
        resp = client.get(f"/api/v1/webhooks/rules/{rule_id}")
        assert resp.status_code == 404

    def test_rules_create_validation(self, client):
        # 缺 name
        resp = client.post("/api/v1/webhooks/rules", json={"event_type_pattern": "*"})
        assert resp.status_code == 400
        # 缺 pattern
        resp = client.post("/api/v1/webhooks/rules", json={"name": "x"})
        assert resp.status_code == 400

    def test_silence_crud_via_api(self, client):
        now = _now_iso()
        later = _iso_offset(3600)
        resp = client.post(
            "/api/v1/webhooks/silence",
            json={
                "name": "maint",
                "event_type_pattern": "*",
                "start_time": now,
                "end_time": later,
                "reason": "deploy",
            },
        )
        assert resp.status_code == 200, resp.text
        win = resp.json()
        win_id = win["id"]

        # 列表
        resp = client.get("/api/v1/webhooks/silence")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

        # 获取
        resp = client.get(f"/api/v1/webhooks/silence/{win_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == win_id

        # 更新
        resp = client.put(f"/api/v1/webhooks/silence/{win_id}", json={"reason": "updated"})
        assert resp.status_code == 200
        assert resp.json()["reason"] == "updated"

        # 删除
        resp = client.delete(f"/api/v1/webhooks/silence/{win_id}")
        assert resp.status_code == 200

    def test_rules_test_dry_run(self, client):
        # 先建订阅与规则
        client.post(
            "/webhooks",
            json={"url": "https://a.com", "events": ["incident.*"]},
        )
        client.post(
            "/api/v1/webhooks/rules",
            json={
                "name": "critical-pd",
                "event_type_pattern": "incident.*",
                "severity": "critical",
                "target_subscription_ids": [],
            },
        )
        # dry-run 测试
        resp = client.post(
            "/api/v1/webhooks/rules/test",
            json={
                "event_type": "incident.created",
                "payload": {"severity": "critical"},
            },
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["silenced"] is False
        assert result["matched_subscription_count"] >= 1
        assert result["routed_subscription_count"] >= 1
        assert len(result["matched_rules"]) >= 1

    def test_rules_test_silenced(self, client):
        # 建静默窗口
        client.post(
            "/api/v1/webhooks/silence",
            json={
                "name": "maint",
                "event_type_pattern": "*",
                "start_time": _iso_offset(-3600),
                "end_time": _iso_offset(3600),
            },
        )
        resp = client.post(
            "/api/v1/webhooks/rules/test",
            json={"event_type": "incident.created", "payload": {}},
        )
        assert resp.status_code == 200
        assert resp.json()["silenced"] is True
