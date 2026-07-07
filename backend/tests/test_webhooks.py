"""webhooks 模块单元测试（S11-4）

覆盖：
- 事件目录与重试策略常量
- _event_matches：精确 / `*` / 前缀通配 `incident.*`
- _sign：HMAC-SHA256 签名
- _compute_next_retry：重试时间计算（边界：超出次数返回空）
- WebhookStore：订阅 CRUD + 投递记录 + 待重试列表
- WebhookManager.dispatch_event：命中订阅数 + 无订阅返回 0

DB 隔离：每个测试通过 monkeypatch 将 webhooks.db 重定向到 tmp_path，并重置全局单例。
"""
from __future__ import annotations

import hashlib
import hmac
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
    monkeypatch.setattr(wstore, "_store", None) if hasattr(wstore, "_store") else None
    monkeypatch.setattr(wmanager, "_manager", None)
    yield db_file


# ═══════════════ 事件目录与重试策略 ═══════════════


class TestEventCatalog:
    def test_catalog_has_12_events(self):
        from app.webhooks.manager import EVENT_CATALOG

        assert len(EVENT_CATALOG) >= 12

    def test_catalog_contains_key_events(self):
        from app.webhooks.manager import EVENT_CATALOG

        for evt in ("document.created", "incident.created", "incident.status_changed", "webhook.test"):
            assert evt in EVENT_CATALOG

    def test_retry_intervals_monotonic_increasing(self):
        from app.webhooks.manager import RETRY_INTERVALS

        # 30 → 120 → 600，递增
        for i in range(1, len(RETRY_INTERVALS)):
            assert RETRY_INTERVALS[i] > RETRY_INTERVALS[i - 1]

    def test_default_max_attempts(self):
        from app.webhooks.manager import DEFAULT_MAX_ATTEMPTS, RETRY_INTERVALS

        # 1 次初试 + len(RETRY_INTERVALS) 次重试
        assert DEFAULT_MAX_ATTEMPTS == 1 + len(RETRY_INTERVALS)


# ═══════════════ _event_matches ═══════════════


class TestEventMatches:
    def test_exact_match(self):
        from app.webhooks.manager import _event_matches

        assert _event_matches(["incident.created"], "incident.created") is True

    def test_no_match_different_event(self):
        from app.webhooks.manager import _event_matches

        assert _event_matches(["incident.created"], "document.created") is False

    def test_wildcard_star_matches_all(self):
        from app.webhooks.manager import _event_matches

        assert _event_matches(["*"], "anything.at.all") is True
        assert _event_matches(["*"], "incident.created") is True

    def test_prefix_wildcard(self):
        from app.webhooks.manager import _event_matches

        assert _event_matches(["incident.*"], "incident.created") is True
        assert _event_matches(["incident.*"], "incident.status_changed") is True
        # 前缀不匹配其他
        assert _event_matches(["incident.*"], "document.created") is False

    def test_empty_subscription_no_match(self):
        from app.webhooks.manager import _event_matches

        assert _event_matches([], "incident.created") is False

    def test_multiple_patterns_any_match(self):
        from app.webhooks.manager import _event_matches

        assert _event_matches(["document.*", "incident.*"], "incident.created") is True
        assert _event_matches(["document.*", "incident.*"], "document.deleted") is True
        assert _event_matches(["document.*", "incident.*"], "wiki.published") is False

    def test_prefix_wildcard_requires_dot_separator(self):
        from app.webhooks.manager import _event_matches

        # incident.* 不应匹配 "incidents.created"（前缀需以 . 分隔）
        assert _event_matches(["incident.*"], "incidents.created") is False


# ═══════════════ _sign HMAC-SHA256 ═══════════════


class TestSign:
    def test_sign_format(self):
        from app.webhooks.manager import _sign

        sig = _sign(b'{"a":1}', "mysecret")
        assert sig.startswith("sha256=")
        assert len(sig) == len("sha256=") + 64  # hexdigest

    def test_sign_matches_hmac(self):
        from app.webhooks.manager import _sign

        payload = b'{"event":"test"}'
        secret = "abc"
        sig = _sign(payload, secret)
        expected = "sha256=" + hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        assert sig == expected

    def test_sign_differs_for_different_secret(self):
        from app.webhooks.manager import _sign

        payload = b"data"
        assert _sign(payload, "secret1") != _sign(payload, "secret2")

    def test_sign_differs_for_different_payload(self):
        from app.webhooks.manager import _sign

        assert _sign(b"data1", "s") != _sign(b"data2", "s")


# ═══════════════ _compute_next_retry ═══════════════


class TestComputeNextRetry:
    def test_first_retry_returns_iso(self):
        from app.webhooks.manager import _compute_next_retry

        result = _compute_next_retry(1)
        assert result != ""
        # 应是合法 ISO 时间
        datetime.fromisoformat(result)

    def test_zero_attempts_returns_empty(self):
        from app.webhooks.manager import _compute_next_retry

        # attempts=0 → idx=-1 越界 → 空字符串
        assert _compute_next_retry(0) == ""

    def test_exceeded_attempts_returns_empty(self):
        from app.webhooks.manager import RETRY_INTERVALS, _compute_next_retry

        # attempts 超过 RETRY_INTERVALS 长度 → 空字符串
        assert _compute_next_retry(len(RETRY_INTERVALS) + 1) == ""

    def test_retry_interval_matches(self):
        from app.webhooks.manager import RETRY_INTERVALS, _compute_next_retry

        # attempts=1 → idx=0 → RETRY_INTERVALS[0]
        before = datetime.now(timezone.utc)
        result = _compute_next_retry(1)
        after = before + timedelta(seconds=RETRY_INTERVALS[0] + 1)
        parsed = datetime.fromisoformat(result)
        # 落在 [before+interval, before+interval+1] 区间
        assert before + timedelta(seconds=RETRY_INTERVALS[0]) <= parsed <= after


# ═══════════════ WebhookStore 订阅 CRUD ═══════════════


class TestWebhookStoreSubscriptions:
    def test_create_subscription(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        sub = store.create_subscription(
            url="https://example.com/hook",
            events=["incident.created"],
            description="test",
        )
        assert sub["url"] == "https://example.com/hook"
        assert sub["events"] == ["incident.created"]
        assert sub["active"] is True
        assert "secret" in sub
        assert "id" in sub

    def test_list_subscriptions_active_only(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        store.create_subscription(url="https://a.com", events=["*"])
        store.create_subscription(url="https://b.com", events=["*"], active=False)
        active = store.list_subscriptions(active_only=True)
        assert len(active) == 1
        assert active[0]["url"] == "https://a.com"

    def test_get_subscription(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        created = store.create_subscription(url="https://x.com", events=["document.*"])
        fetched = store.get_subscription(created["id"])
        assert fetched is not None
        assert fetched["url"] == "https://x.com"

    def test_get_subscription_not_found(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        assert store.get_subscription("wh_nonexistent") is None

    def test_update_subscription(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        created = store.create_subscription(url="https://x.com", events=["*"])
        updated = store.update_subscription(created["id"], events=["incident.*"], active=False)
        assert updated["events"] == ["incident.*"]
        assert updated["active"] is False

    def test_delete_subscription(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        created = store.create_subscription(url="https://x.com", events=["*"])
        assert store.delete_subscription(created["id"]) is True
        assert store.get_subscription(created["id"]) is None
        # 再删返回 False
        assert store.delete_subscription(created["id"]) is False

    def test_get_subscription_secret(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        created = store.create_subscription(url="https://x.com", events=["*"])
        secret = store.get_subscription_secret(created["id"])
        assert secret == created["secret"]


# ═══════════════ WebhookStore 投递记录 ═══════════════


class TestWebhookStoreDeliveries:
    def test_create_delivery(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        sub = store.create_subscription(url="https://x.com", events=["*"])
        deliv = store.create_delivery(
            subscription_id=sub["id"],
            event_type="incident.created",
            payload={"data": 1},
            max_attempts=4,
        )
        assert deliv["status"] == "pending"
        assert deliv["attempts"] == 0
        assert deliv["max_attempts"] == 4

    def test_update_delivery_success(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        sub = store.create_subscription(url="https://x.com", events=["*"])
        deliv = store.create_delivery(
            subscription_id=sub["id"], event_type="incident.created", payload={}, max_attempts=4
        )
        store.update_delivery(
            deliv["id"], status="success", response_code=200, attempts=1, next_retry_at=None
        )
        # 验证更新（通过 list_pending_retries 应不返回 success）
        pending = store.list_pending_retries(datetime.now(timezone.utc).isoformat())
        assert all(p["id"] != deliv["id"] for p in pending)

    def test_list_pending_retries(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store

        store = get_webhook_store()
        sub = store.create_subscription(url="https://x.com", events=["*"])
        # 创建一个待重试，next_retry_at 设为过去
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        deliv = store.create_delivery(
            subscription_id=sub["id"], event_type="incident.created", payload={}, max_attempts=4
        )
        store.update_delivery(
            deliv["id"], status="retry", attempts=1, next_retry_at=past
        )
        pending = store.list_pending_retries(datetime.now(timezone.utc).isoformat())
        assert any(p["id"] == deliv["id"] for p in pending)


# ═══════════════ WebhookManager.dispatch_event ═══════════════


class TestWebhookManagerDispatch:
    def test_dispatch_no_subscriptions_returns_zero(self, isolated_webhook_db):
        from app.webhooks.manager import get_webhook_manager

        mgr = get_webhook_manager()
        count = mgr.dispatch_event("incident.created", {"key": "value"})
        assert count == 0

    def test_dispatch_matches_subscription(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.manager import get_webhook_manager

        store = get_webhook_store()
        store.create_subscription(url="https://example.com/hook", events=["incident.*"])
        store.create_subscription(url="https://example.com/hook2", events=["document.*"])
        mgr = get_webhook_manager()
        count = mgr.dispatch_event("incident.created", {"x": 1})
        assert count == 1  # 只有 incident.* 匹配

    def test_dispatch_wildcard_matches_all(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.manager import get_webhook_manager

        store = get_webhook_store()
        store.create_subscription(url="https://a.com", events=["*"])
        mgr = get_webhook_manager()
        assert mgr.dispatch_event("any.event", {}) == 1
        assert mgr.dispatch_event("another.event", {}) == 1

    def test_dispatch_inactive_subscription_not_matched(self, isolated_webhook_db):
        from app.storage.webhook_store import get_webhook_store
        from app.webhooks.manager import get_webhook_manager

        store = get_webhook_store()
        store.create_subscription(url="https://a.com", events=["*"], active=False)
        mgr = get_webhook_manager()
        # list_subscriptions(active_only=True) 不返回 inactive
        assert mgr.dispatch_event("incident.created", {}) == 0
