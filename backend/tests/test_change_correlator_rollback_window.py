"""ChangeCorrelator P2-3.4 / P2-3.5 测试

P2-3.4: 利用 rollback_of 字段识别回滚变更
- suggest_rollback 排除 rollback_of 非空的变更
- suggest_rollback 优先返回被 rollback_of 指向的变更
- suggest_rollback 结果含 is_rolled_back 字段
- _compute_link 对 rollback_of 非空变更降权
- _compute_link 对被回滚目标变更加权
- _find_rollback_targets 正确返回目标集合

P2-3.5: deployment 2h 长尾 + 按 change_type 区分窗口
- 默认 deployment 窗口 120 分钟
- 默认 config_change 窗口 60 分钟
- 默认 default 窗口 30 分钟
- 自定义 change_type_windows 覆盖默认
- deployment 2h 外的变更不关联（回归保护）
- time_window_minutes 参数仍能覆盖 default 窗口（向后兼容）
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


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


def _make_incident_row(
    scope: dict,
    started_at: str,
    incident_id: str = "inc-test",
) -> sqlite3.Row:
    """构造一个 sqlite3.Row 模拟 incident 行，供 _compute_link 单测使用"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE incidents (
            incident_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            scope TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO incidents (incident_id, started_at, scope) VALUES (?, ?, ?)",
        (incident_id, started_at, json.dumps(scope)),
    )
    return conn.execute(
        "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
    ).fetchone()


# ═══════════════ P2-3.4: rollback_of 识别 ═══════════════


class TestFindRollbackTargets:
    """_find_rollback_targets 辅助方法"""

    def test_returns_empty_for_no_rollback(self):
        from app.aiops.change_correlator import Change, ChangeCorrelator

        changes = [
            Change(id="c1", change_type="deployment", timestamp="2026-01-01T00:00:00Z"),
            Change(id="c2", change_type="restart", timestamp="2026-01-01T00:01:00Z"),
        ]
        assert ChangeCorrelator._find_rollback_targets(changes) == set()

    def test_returns_target_ids(self):
        from app.aiops.change_correlator import Change, ChangeCorrelator

        changes = [
            Change(id="c1", change_type="deployment", timestamp="2026-01-01T00:00:00Z"),
            Change(
                id="c2",
                change_type="rollback",
                timestamp="2026-01-01T00:01:00Z",
                rollback_of="c1",
            ),
            Change(
                id="c3",
                change_type="rollback",
                timestamp="2026-01-01T00:02:00Z",
                rollback_of="c9",
            ),
        ]
        # c1 和 c9 被指向；c9 虽然不在列表中也应被收录（集合只看 rollback_of 值）
        assert ChangeCorrelator._find_rollback_targets(changes) == {"c1", "c9"}

    def test_ignores_empty_rollback_of(self):
        from app.aiops.change_correlator import Change, ChangeCorrelator

        changes = [
            Change(id="c1", change_type="deployment", timestamp="t", rollback_of=""),
        ]
        assert ChangeCorrelator._find_rollback_targets(changes) == set()


class TestComputeLinkRollbackWeighting:
    """_compute_link 对 rollback_of 的降权/加权（直接单测）"""

    def test_rollback_of_action_downweighted(self):
        """rollback_of 非空的变更（回滚动作）应被降权 0.3x"""
        from app.aiops.change_correlator import Change, ChangeCorrelator

        cc = ChangeCorrelator()
        now = datetime.now(timezone.utc)
        inc_row = _make_incident_row(
            scope={"hosts": ["h1"], "services": ["svc1"], "components": ["cmp1"]},
            started_at=_iso(now),
        )
        window = timedelta(minutes=30)

        base_kwargs = dict(
            change_type="deployment",
            timestamp=_iso(now - timedelta(seconds=60)),
            host="h1",
            service="svc1",
            component="cmp1",
        )
        normal = Change(id="c-normal", **base_kwargs)
        rollback_action = Change(id="c-rollback", rollback_of="c-other", **base_kwargs)

        link_normal = cc._compute_link(normal, inc_row, window)
        link_rollback = cc._compute_link(rollback_action, inc_row, window)

        assert link_normal is not None
        assert link_rollback is not None
        # 基线封顶 1.0；回滚动作降权 0.3 → 0.3
        assert link_normal.correlation_score == 1.0
        assert link_rollback.correlation_score < link_normal.correlation_score
        assert link_rollback.correlation_score == pytest.approx(0.3, abs=0.01)
        # reasoning 应包含降权标注
        assert "rollback 动作" in link_rollback.reasoning

    def test_rollback_target_upweighted(self):
        """被 rollback_of 指向的变更（原始变更）应被加权 1.5x"""
        from app.aiops.change_correlator import Change, ChangeCorrelator

        cc = ChangeCorrelator()
        now = datetime.now(timezone.utc)
        # 单 overlap + 低权重类型，确保 baseline * 1.5 < 1.0 不被封顶
        inc_row = _make_incident_row(
            scope={"hosts": ["h1"]},
            started_at=_iso(now),
        )
        window = timedelta(minutes=30)
        # change_type=other (weight 0.3) → type 贡献 0.075
        target = Change(
            id="c-target",
            change_type="other",
            timestamp=_iso(now - timedelta(seconds=60)),
            host="h1",
        )

        link_base = cc._compute_link(
            target, inc_row, window, rollback_targets=set()
        )
        link_up = cc._compute_link(
            target, inc_row, window, rollback_targets={"c-target"}
        )

        assert link_base is not None
        assert link_up is not None
        # baseline: scope 0.2 + time 0.3 + type 0.075 = 0.575
        assert link_base.correlation_score == pytest.approx(0.575, abs=0.01)
        # 加权: 0.575 * 1.5 = 0.8625
        assert link_up.correlation_score == pytest.approx(0.8625, abs=0.01)
        assert link_up.correlation_score > link_base.correlation_score
        # reasoning 应包含加权标注
        assert "被回滚指向" in link_up.reasoning

    def test_rollback_of_takes_precedence_over_target_check(self):
        """若 change 自身 rollback_of 非空，则只降权，不再加权（即使被指向）"""
        from app.aiops.change_correlator import Change, ChangeCorrelator

        cc = ChangeCorrelator()
        now = datetime.now(timezone.utc)
        inc_row = _make_incident_row(
            scope={"hosts": ["h1"]},
            started_at=_iso(now),
        )
        window = timedelta(minutes=30)
        # 该变更既是回滚动作（rollback_of 非空）又恰好被另一个回滚指向
        ambig = Change(
            id="c-ambig",
            change_type="other",
            timestamp=_iso(now - timedelta(seconds=60)),
            host="h1",
            rollback_of="c-something",
        )
        link = cc._compute_link(
            ambig, inc_row, window, rollback_targets={"c-ambig"}
        )
        assert link is not None
        # 降权优先：0.575 * 0.3 = 0.1725
        assert link.correlation_score == pytest.approx(0.1725, abs=0.01)
        assert "rollback 动作" in link.reasoning


# ═══════════════ P2-3.4: suggest_rollback 集成测试 ═══════════════


class TestSuggestRollbackRollbackOf:
    """suggest_rollback 利用 rollback_of 字段的集成测试"""

    def _setup_incident_with_changes(
        self,
        isolated_events_db,
        changes: list[dict],
        incident_offset_seconds: int = 60,
    ):
        """构造 incident + changes，并执行 correlate，返回 (cc, incident_id)"""
        from app.aiops.change_correlator import get_change_correlator
        from app.aiops.event_correlator import get_event_correlator

        ec = get_event_correlator()
        cc = get_change_correlator()
        now = datetime.now(timezone.utc)
        # 用第一条 change 的 host/service 构造告警，确保 scope 重合
        host = changes[0].get("host", "h1")
        service = changes[0].get("service", "svc1")
        ec.ingest([
            {
                "id": "e1",
                "timestamp": _iso(now - timedelta(seconds=incident_offset_seconds)),
                "host": host,
                "service": service,
                "severity": "high",
                "message": "down",
            }
        ])
        ec.correlate(since_minutes=120)
        cc.ingest(changes)
        cc.correlate(since_hours=2, time_window_minutes=30)
        incidents = ec.list_incidents(status="all")
        return cc, incidents[0]["incident_id"]

    def test_suggest_rollback_excludes_rollback_of_changes(self, isolated_events_db):
        """suggest_rollback 排除 rollback_of 非空的变更（即使其关联分被人工调高）"""
        from app.aiops.change_correlator import _get_db, get_change_correlator
        from app.aiops.event_correlator import get_event_correlator

        ec = get_event_correlator()
        cc = get_change_correlator()
        now = datetime.now(timezone.utc)
        # 告警 → incident
        ec.ingest([{
            "id": "e1",
            "timestamp": _iso(now - timedelta(seconds=60)),
            "host": "h1",
            "service": "svc1",
            "severity": "high",
            "message": "down",
        }])
        ec.correlate(since_minutes=120)
        inc_id = ec.list_incidents(status="all")[0]["incident_id"]

        # 2 个变更：普通 deployment + 回滚动作（rollback_of 非空）
        cc.ingest([
            {
                "id": "ch-normal",
                "change_type": "deployment",
                "timestamp": _iso(now - timedelta(seconds=120)),
                "host": "h1",
                "service": "svc1",
            },
            {
                "id": "ch-rollback-action",
                "change_type": "deployment",
                "timestamp": _iso(now - timedelta(seconds=120)),
                "host": "h1",
                "service": "svc1",
                "rollback_of": "ch-other",
            },
        ])
        # 人工写入两条关联，回滚动作分数更高（0.95 > 0.8）
        # 模拟"若没有 rollback_of 过滤，回滚动作本会被选中"的场景
        conn = _get_db()
        now_iso = now.isoformat()
        conn.executemany(
            """INSERT OR REPLACE INTO change_incident_links
               (change_id, incident_id, correlation_score, scope_overlap,
                time_lag_seconds, reasoning, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                ("ch-normal", inc_id, 0.8, 2, 60, "normal change", now_iso),
                ("ch-rollback-action", inc_id, 0.95, 2, 60, "rollback action", now_iso),
            ],
        )
        conn.commit()

        suggestion = cc.suggest_rollback(inc_id)
        assert suggestion["suggested"] is True
        # 即使回滚动作分数更高（0.95 > 0.8），也应排除它，返回普通变更
        assert suggestion["change_id"] == "ch-normal"
        assert suggestion["is_rolled_back"] is False

    def test_suggest_rollback_prefers_rolled_back_target(self, isolated_events_db):
        """suggest_rollback 优先返回被 rollback_of 指向的变更（即使分数更低）"""
        from app.aiops.change_correlator import get_change_correlator
        from app.aiops.event_correlator import get_event_correlator

        ec = get_event_correlator()
        cc = get_change_correlator()
        now = datetime.now(timezone.utc)
        # 告警：host=h1, service=svc1, component=cmp1
        # component 用于让回滚动作 3-overlap 基线达 1.0 → 降权后 0.3 仍能持久化
        # 这样 get_incident_changes 才能读到回滚动作的 rollback_of=ch-target
        ec.ingest([{
            "id": "e1",
            "timestamp": _iso(now - timedelta(seconds=60)),
            "host": "h1",
            "service": "svc1",
            "component": "cmp1",
            "severity": "high",
            "message": "down",
        }])
        ec.correlate(since_minutes=120)
        # 三个变更：
        # ch-target：被回滚指向（rollback_of 为空，会被加权），单 overlap → 分数较低
        # ch-other：普通 deployment，2 overlap → 分数较高
        # ch-rollback-action：回滚动作，3 overlap，rollback_of=ch-target（降权后 0.3 持久化）
        cc.ingest([
            {
                "id": "ch-target",
                "change_type": "other",  # 低权重，确保加权后仍低于 ch-other
                "timestamp": _iso(now - timedelta(seconds=120)),
                "host": "h1",
            },
            {
                "id": "ch-other",
                "change_type": "deployment",
                "timestamp": _iso(now - timedelta(seconds=120)),
                "host": "h1",
                "service": "svc1",
            },
            {
                "id": "ch-rollback-action",
                "change_type": "rollback",
                "timestamp": _iso(now - timedelta(seconds=120)),
                "host": "h1",
                "service": "svc1",
                "component": "cmp1",
                "rollback_of": "ch-target",
            },
        ])
        cc.correlate(since_hours=2, time_window_minutes=30)
        incidents = ec.list_incidents(status="all")
        inc_id = incidents[0]["incident_id"]

        suggestion = cc.suggest_rollback(inc_id)
        assert suggestion["suggested"] is True
        # ch-target 被回滚指向 → 优先返回（即使 ch-other 分数更高）
        assert suggestion["change_id"] == "ch-target"
        assert suggestion["is_rolled_back"] is True

    def test_suggest_rollback_includes_is_rolled_back_field(self, isolated_events_db):
        """suggest_rollback 结果含 is_rolled_back 字段（True/False）"""
        now = datetime.now(timezone.utc)
        cc, inc_id = self._setup_incident_with_changes(
            isolated_events_db,
            changes=[
                {
                    "id": "ch-normal",
                    "change_type": "deployment",
                    "timestamp": _iso(now - timedelta(seconds=60)),
                    "host": "h1",
                    "service": "svc1",
                }
            ],
        )
        suggestion = cc.suggest_rollback(inc_id)
        assert suggestion["suggested"] is True
        # 字段存在且为 bool 类型
        assert "is_rolled_back" in suggestion
        assert isinstance(suggestion["is_rolled_back"], bool)
        # 普通变更未被回滚
        assert suggestion["is_rolled_back"] is False


# ═══════════════ P2-3.5: 按 change_type 区分窗口 ═══════════════


class TestChangeTypeWindows:
    """按 change_type 区分关联时间窗口"""

    def test_default_deployment_window_120(self):
        """默认 deployment 窗口 120 分钟"""
        from app.aiops.change_correlator import ChangeCorrelator

        cc = ChangeCorrelator()
        assert cc.change_type_windows["deployment"] == 120

    def test_default_config_change_window_60(self):
        """默认 config_change 窗口 60 分钟"""
        from app.aiops.change_correlator import ChangeCorrelator

        cc = ChangeCorrelator()
        assert cc.change_type_windows["config_change"] == 60

    def test_default_default_window_30(self):
        """默认 default 窗口 30 分钟"""
        from app.aiops.change_correlator import ChangeCorrelator

        cc = ChangeCorrelator()
        assert cc.change_type_windows["default"] == 30

    def test_custom_change_type_windows_override(self):
        """自定义 change_type_windows 覆盖默认"""
        from app.aiops.change_correlator import ChangeCorrelator

        cc = ChangeCorrelator(change_type_windows={"deployment": 240, "default": 45})
        assert cc.change_type_windows["deployment"] == 240
        assert cc.change_type_windows["config_change"] == 60  # 未覆盖的保留默认
        # time_window_minutes(默认 30) 始终覆盖 default（向后兼容）
        assert cc.change_type_windows["default"] == 30

    def test_custom_change_type_windows_with_time_window_minutes(self):
        """time_window_minutes 覆盖 default 键，自定义窗口保留"""
        from app.aiops.change_correlator import ChangeCorrelator

        cc = ChangeCorrelator(
            time_window_minutes=90,
            change_type_windows={"deployment": 200},
        )
        assert cc.change_type_windows["deployment"] == 200
        assert cc.change_type_windows["config_change"] == 60
        # time_window_minutes 覆盖 default
        assert cc.change_type_windows["default"] == 90

    def test_window_for_change_helper(self):
        """_window_for_change 按 change_type 取窗口"""
        from app.aiops.change_correlator import ChangeCorrelator

        cc = ChangeCorrelator()
        from datetime import timedelta as td

        assert cc._window_for_change("deployment") == td(minutes=120)
        assert cc._window_for_change("config_change") == td(minutes=60)
        # 未知 change_type 用 default
        assert cc._window_for_change("unknown_type") == td(minutes=30)
        # default_override 覆盖 default
        assert cc._window_for_change("unknown_type", default_override=99) == td(minutes=99)
        # 已知 change_type 不受 default_override 影响
        assert cc._window_for_change("deployment", default_override=99) == td(minutes=120)


class TestCorrelateChangeTypeWindows:
    """correlate 中按 change_type 区分窗口的端到端验证"""

    def _setup_change_and_incident(
        self,
        isolated_events_db,
        change_type: str,
        lag_minutes: int,
    ):
        """构造 1 个 change + 1 个 incident，incident 在 change 后 lag_minutes 分钟

        scope 重合（host + service + component 三重叠），确保时间窗口是唯一变量。
        """
        from app.aiops.change_correlator import get_change_correlator
        from app.aiops.event_correlator import get_event_correlator

        ec = get_event_correlator()
        cc = get_change_correlator()
        now = datetime.now(timezone.utc)
        change_ts = now - timedelta(minutes=lag_minutes)
        # 变更
        cc.ingest([
            {
                "id": "ch1",
                "change_type": change_type,
                "timestamp": _iso(change_ts),
                "host": "h1",
                "service": "svc1",
                "component": "cmp1",
            }
        ])
        # 告警事件（在 change 之后 lag_minutes 分钟）
        ec.ingest([
            {
                "id": "e1",
                "timestamp": _iso(now),
                "host": "h1",
                "service": "svc1",
                "component": "cmp1",
                "severity": "high",
                "message": "down",
            }
        ])
        ec.correlate(since_minutes=240)
        return cc

    def test_deployment_within_120min_associated(self, isolated_events_db):
        """deployment 在 120 分钟窗口内（100min）应关联"""
        cc = self._setup_change_and_incident(isolated_events_db, "deployment", 100)
        result = cc.correlate(since_hours=4, time_window_minutes=30)
        # 用 time_window_minutes=30 仅为向后兼容参数；deployment 仍用 120min 窗口
        assert result["stats"]["links"] >= 1
        assert result["links"][0]["change_id"] == "ch1"

    def test_deployment_beyond_120min_not_associated(self, isolated_events_db):
        """deployment 2h（120min）外的变更不关联（回归保护）

        lag=130min > 120min 窗口 → _compute_link 时间过滤返回 None。
        """
        cc = self._setup_change_and_incident(isolated_events_db, "deployment", 130)
        result = cc.correlate(since_hours=4, time_window_minutes=30)
        assert result["stats"]["links"] == 0

    def test_config_change_within_60min_associated(self, isolated_events_db):
        """config_change 在 60 分钟窗口内（50min）应关联"""
        cc = self._setup_change_and_incident(
            isolated_events_db, "config_change", 50
        )
        result = cc.correlate(since_hours=4, time_window_minutes=30)
        assert result["stats"]["links"] >= 1

    def test_config_change_beyond_60min_not_associated(self, isolated_events_db):
        """config_change 60min 外（70min）不关联，但仍在 deployment 120min 内

        证明 config_change 用 60min 而非 120min 窗口。
        """
        cc = self._setup_change_and_incident(
            isolated_events_db, "config_change", 70
        )
        result = cc.correlate(since_hours=4, time_window_minutes=30)
        assert result["stats"]["links"] == 0

    def test_default_type_within_30min_associated(self, isolated_events_db):
        """未知 change_type 用 default 30min 窗口，20min 内应关联"""
        cc = self._setup_change_and_incident(isolated_events_db, "restart", 20)
        result = cc.correlate(since_hours=4, time_window_minutes=30)
        assert result["stats"]["links"] >= 1

    def test_default_type_beyond_30min_not_associated(self, isolated_events_db):
        """未知 change_type 用 default 30min 窗口，40min 外不关联"""
        cc = self._setup_change_and_incident(isolated_events_db, "restart", 40)
        result = cc.correlate(since_hours=4, time_window_minutes=30)
        assert result["stats"]["links"] == 0

    def test_time_window_minutes_overrides_default_only(self, isolated_events_db):
        """time_window_minutes 参数覆盖 default 窗口，不影响 deployment/config_change

        time_window_minutes=15 → default=15min，但 deployment 仍 120min，config_change 仍 60min。
        用 restart（default 窗口）验证：20min 在 30min 内但超出 15min → 不应关联。
        """
        from app.aiops.change_correlator import get_change_correlator
        from app.aiops.event_correlator import get_event_correlator

        ec = get_event_correlator()
        cc = get_change_correlator()
        now = datetime.now(timezone.utc)
        cc.ingest([
            {
                "id": "ch1",
                "change_type": "restart",
                "timestamp": _iso(now - timedelta(minutes=20)),
                "host": "h1",
                "service": "svc1",
                "component": "cmp1",
            }
        ])
        ec.ingest([
            {
                "id": "e1",
                "timestamp": _iso(now),
                "host": "h1",
                "service": "svc1",
                "component": "cmp1",
                "severity": "high",
                "message": "down",
            }
        ])
        ec.correlate(since_minutes=60)
        # time_window_minutes=15 覆盖 default=15，restart 用 default → 20min > 15min 不关联
        result = cc.correlate(since_hours=2, time_window_minutes=15)
        assert result["stats"]["links"] == 0

    def test_time_window_minutes_backward_compat_default(self, isolated_events_db):
        """time_window_minutes 参数仍能覆盖 default 窗口（向后兼容）

        time_window_minutes=45 → default=45min，restart（用 default）在 40min 内应关联。
        原内置 default=30min 时 40min 不关联，证明 time_window_minutes 确实覆盖了 default。
        """
        from app.aiops.change_correlator import get_change_correlator
        from app.aiops.event_correlator import get_event_correlator

        ec = get_event_correlator()
        cc = get_change_correlator()
        now = datetime.now(timezone.utc)
        cc.ingest([
            {
                "id": "ch1",
                "change_type": "restart",
                "timestamp": _iso(now - timedelta(minutes=40)),
                "host": "h1",
                "service": "svc1",
                "component": "cmp1",
            }
        ])
        ec.ingest([
            {
                "id": "e1",
                "timestamp": _iso(now),
                "host": "h1",
                "service": "svc1",
                "component": "cmp1",
                "severity": "high",
                "message": "down",
            }
        ])
        ec.correlate(since_minutes=120)
        # time_window_minutes=45 覆盖 default=45，restart 在 40min 内 → 关联
        result = cc.correlate(since_hours=2, time_window_minutes=45)
        assert result["stats"]["links"] >= 1

    def test_custom_change_type_windows_affect_correlate(self, isolated_events_db):
        """自定义 change_type_windows 在 correlate 中生效（覆盖默认窗口）"""
        from app.aiops.change_correlator import ChangeCorrelator
        from app.aiops.event_correlator import get_event_correlator

        # 自定义 deployment=20min（远小于默认 120min）
        cc = ChangeCorrelator(change_type_windows={"deployment": 20})
        ec = get_event_correlator()
        now = datetime.now(timezone.utc)
        cc.ingest([
            {
                "id": "ch1",
                "change_type": "deployment",
                "timestamp": _iso(now - timedelta(minutes=40)),
                "host": "h1",
                "service": "svc1",
                "component": "cmp1",
            }
        ])
        ec.ingest([
            {
                "id": "e1",
                "timestamp": _iso(now),
                "host": "h1",
                "service": "svc1",
                "component": "cmp1",
                "severity": "high",
                "message": "down",
            }
        ])
        ec.correlate(since_minutes=120)
        # deployment 自定义窗口 20min，40min 超出 → 不关联
        result = cc.correlate(since_hours=2)
        assert result["stats"]["links"] == 0
