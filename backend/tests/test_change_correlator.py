"""ChangeCorrelator 回归测试（P2-3.7 回归保护）

验证 suggest_rollback 方法在 P2-3.7 回滚执行链路改造后行为未改变：
- 仍返回建议 dict（不触发实际回滚）
- 无关联变更时返回 suggested=False
- 相关度不足时返回 suggested=False
- 高风险关联时返回 suggested=True + 回滚建议字段

这是 P2-3.7 的回归保护测试，确保新增 execute_rollback 不影响 suggest_rollback。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════ 公共 fixture ═══════════════


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture
def isolated_events_db(tmp_path, monkeypatch):
    """将 events 数据库重定向到 tmp_path，并重置 correlator 单例"""
    import app.aiops.change_correlator as ch
    import app.aiops.event_correlator as ev

    db_file = tmp_path / "events.db"
    monkeypatch.setattr(ev, "DB_PATH", db_file)
    monkeypatch.setattr(ch, "DB_PATH", db_file)
    monkeypatch.setattr(ev, "_correlator", None)
    monkeypatch.setattr(ch, "_correlator", None)
    yield db_file


# ═══════════════ suggest_rollback 回归测试 ═══════════════


class TestSuggestRollbackRegression:
    """验证 suggest_rollback 行为在 P2-3.7 改造后未改变"""

    def _setup_high_risk(self, isolated_events_db, time_lag_seconds=60):
        """构造 1 个高风险变更 + 1 个 incident"""
        from app.aiops.change_correlator import get_change_correlator
        from app.aiops.event_correlator import get_event_correlator

        ec = get_event_correlator()
        cc = get_change_correlator()
        now = datetime.now(timezone.utc)
        cc.ingest(
            [
                {
                    "id": "ch1",
                    "change_type": "deployment",
                    "timestamp": _iso(now - timedelta(seconds=time_lag_seconds)),
                    "host": "h1",
                    "service": "svc",
                    "author": "alice",
                }
            ]
        )
        ec.ingest(
            [
                {
                    "id": "e1",
                    "timestamp": _iso(now),
                    "host": "h1",
                    "service": "svc",
                    "severity": "high",
                    "message": "down",
                }
            ]
        )
        ec.correlate(since_minutes=60)
        cc.correlate(since_hours=1, time_window_minutes=30)
        incidents = ec.list_incidents(status="all")
        return cc, incidents[0]["incident_id"]

    def test_suggest_rollback_returns_dict_not_triggers_rollback(
        self, isolated_events_db
    ):
        """suggest_rollback 返回建议 dict，不触发实际回滚"""
        cc, inc_id = self._setup_high_risk(isolated_events_db)
        suggestion = cc.suggest_rollback(inc_id)

        # 必须返回 dict（不是执行结果）
        assert isinstance(suggestion, dict)
        # suggested=True 表示有回滚建议
        assert suggestion["suggested"] is True
        # 返回的是建议字段，不是执行结果（无 success/action_id 字段）
        assert "change_id" in suggestion
        assert "change_type" in suggestion
        assert "correlation_score" in suggestion
        # 不应包含执行结果字段
        assert "success" not in suggestion
        assert "action_id" not in suggestion
        assert "provider" not in suggestion

    def test_suggest_rollback_no_changes(self, isolated_events_db):
        """无关联变更时返回 suggested=False"""
        from app.aiops.change_correlator import get_change_correlator

        cc = get_change_correlator()
        suggestion = cc.suggest_rollback("inc-nonexistent")
        assert suggestion["suggested"] is False
        assert "无关联变更" in suggestion["reason"]

    def test_suggest_rollback_low_correlation(self, isolated_events_db):
        """相关度不足时返回 suggested=False"""
        from app.aiops.change_correlator import get_change_correlator
        from app.aiops.event_correlator import get_event_correlator

        ec = get_event_correlator()
        cc = get_change_correlator()
        now = datetime.now(timezone.utc)
        # 变更和 incident 在不同 host/service（scope 不重合，相关度低）
        cc.ingest(
            [
                {
                    "id": "ch1",
                    "change_type": "deployment",
                    "timestamp": _iso(now - timedelta(seconds=60)),
                    "host": "hA",
                    "service": "svcA",
                }
            ]
        )
        ec.ingest(
            [
                {
                    "id": "e1",
                    "timestamp": _iso(now),
                    "host": "hB",
                    "service": "svcB",
                    "severity": "high",
                    "message": "x",
                }
            ]
        )
        ec.correlate(since_minutes=60)
        cc.correlate(since_hours=1, time_window_minutes=30)
        incidents = ec.list_incidents(status="all")
        inc_id = incidents[0]["incident_id"]
        suggestion = cc.suggest_rollback(inc_id)
        assert suggestion["suggested"] is False
