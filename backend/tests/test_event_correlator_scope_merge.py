"""跨窗口 scope 子集合并测试（P2-2.6）

验证 _merge_incidents 在子集/超集 scope 关系下的合并行为：
- 精确相等仍合并（回归保护）
- 子集合并：A.scope ⊂ B.scope → A 合并入 B
- 超集合并：A.scope ⊃ B.scope → B 合并入 A
- 无子集关系不合并
- 链式合并 A ⊂ B ⊂ C → 全合并到 C
- 合并后 scope 取并集、events 拼接、severity 取更高
- 不同 entity_type 不合并（避免误合并）

直接构造 Incident 调用 _merge_incidents，不依赖 DB。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════ 工具函数 ═══════════════


def _make_incident(
    incident_id: str,
    scope: dict,
    *,
    severity: str = "warning",
    started_at: str = "2026-01-01T00:00:00Z",
    ended_at: str = "2026-01-01T00:05:00Z",
    alert_ids: list[str] | None = None,
) -> "Incident":  # noqa: F821
    """构造测试用 Incident"""
    from app.aiops.event_correlator import Incident

    return Incident(
        incident_id=incident_id,
        started_at=started_at,
        ended_at=ended_at,
        severity=severity,
        scope=scope,
        suspected_root_cause="",
        runbook_hint="",
        alert_ids=list(alert_ids) if alert_ids else [incident_id],
        alerts=[{"id": aid, "message": f"alert-{aid}"} for aid in (alert_ids or [incident_id])],
    )


def _scope(hosts=None, services=None, components=None):
    """构造 scope dict（保持 sorted 列表）"""
    return {
        "hosts": sorted(hosts or []),
        "services": sorted(services or []),
        "components": sorted(components or []),
    }


@pytest.fixture
def correlator():
    from app.aiops.event_correlator import EventCorrelator

    return EventCorrelator()


# ═══════════════ 回归保护：精确相等仍合并 ═══════════════


class TestExactScopeMerge:
    def test_exact_equal_scope_merges(self, correlator):
        """精确相等的 scope 应合并为一个 incident（回归保护）"""
        a = _make_incident("inc-a", _scope(hosts=["h1"]), severity="warning",
                          alert_ids=["a1"])
        b = _make_incident("inc-b", _scope(hosts=["h1"]), severity="high",
                          alert_ids=["b1"])
        merged = correlator._merge_incidents([a, b])
        assert len(merged) == 1
        # events 拼接
        assert set(merged[0].alert_ids) == {"a1", "b1"}
        # severity 取更高
        assert merged[0].severity == "high"


# ═══════════════ 子集合并 ═══════════════


class TestSubsetMerge:
    def test_subset_merges_into_superset(self, correlator):
        """A.scope ⊂ B.scope → A 合并入 B"""
        a = _make_incident("inc-a", _scope(hosts=["h1"]), alert_ids=["a1"])
        b = _make_incident("inc-b", _scope(hosts=["h1", "h2"]), alert_ids=["b1"])
        merged = correlator._merge_incidents([a, b])
        assert len(merged) == 1
        # 合并后 scope 是并集（= 大 scope）
        assert set(merged[0].scope["hosts"]) == {"h1", "h2"}
        # 保留大 scope 的 incident_id
        assert merged[0].incident_id == "inc-b"
        # events 拼接
        assert set(merged[0].alert_ids) == {"a1", "b1"}

    def test_superset_absorbs_subset(self, correlator):
        """A.scope ⊃ B.scope → B 合并入 A"""
        a = _make_incident("inc-a", _scope(hosts=["h1", "h2"]), alert_ids=["a1"])
        b = _make_incident("inc-b", _scope(hosts=["h1"]), alert_ids=["b1"])
        merged = correlator._merge_incidents([a, b])
        assert len(merged) == 1
        # 合并后保留大 scope（A）
        assert merged[0].incident_id == "inc-a"
        assert set(merged[0].scope["hosts"]) == {"h1", "h2"}
        assert set(merged[0].alert_ids) == {"a1", "b1"}


# ═══════════════ 无子集关系不合并 ═══════════════


class TestNoSubsetNoMerge:
    def test_disjoint_scopes_not_merged(self, correlator):
        """无子集关系（不相交）的 scope 不合并"""
        a = _make_incident("inc-a", _scope(hosts=["h1"]), alert_ids=["a1"])
        b = _make_incident("inc-b", _scope(hosts=["h2"]), alert_ids=["b1"])
        merged = correlator._merge_incidents([a, b])
        assert len(merged) == 2

    def test_overlapping_but_not_subset_not_merged(self, correlator):
        """交叉但互非子集的 scope 不合并"""
        a = _make_incident("inc-a", _scope(hosts=["h1", "h2"]), alert_ids=["a1"])
        b = _make_incident("inc-b", _scope(hosts=["h2", "h3"]), alert_ids=["b1"])
        merged = correlator._merge_incidents([a, b])
        assert len(merged) == 2


# ═══════════════ 链式合并 ═══════════════


class TestChainMerge:
    def test_chain_subset_all_merge_to_largest(self, correlator):
        """A ⊂ B ⊂ C → 全合并到 C"""
        a = _make_incident("inc-a", _scope(hosts=["h1"]), alert_ids=["a1"])
        b = _make_incident("inc-b", _scope(hosts=["h1", "h2"]), alert_ids=["b1"])
        c = _make_incident("inc-c", _scope(hosts=["h1", "h2", "h3"]), alert_ids=["c1"])
        merged = correlator._merge_incidents([a, b, c])
        assert len(merged) == 1
        # 合并到最大的 C
        assert merged[0].incident_id == "inc-c"
        assert set(merged[0].scope["hosts"]) == {"h1", "h2", "h3"}
        # 三个 incident 的 events 全部拼接
        assert set(merged[0].alert_ids) == {"a1", "b1", "c1"}


# ═══════════════ 合并后字段正确性 ═══════════════


class TestMergedFields:
    def test_merge_takes_union_scope_concat_events_higher_severity(self, correlator):
        """合并后 scope 取并集、events 拼接、severity 取更高、time_range 取更宽"""
        a = _make_incident(
            "inc-a",
            _scope(hosts=["h1"], services=["svc1"]),
            severity="warning",
            started_at="2026-01-01T00:00:00Z",
            ended_at="2026-01-01T00:05:00Z",
            alert_ids=["a1", "a2"],
        )
        b = _make_incident(
            "inc-b",
            _scope(hosts=["h1", "h2"], services=["svc1", "svc2"]),
            severity="critical",
            started_at="2026-01-01T00:10:00Z",
            ended_at="2026-01-01T00:20:00Z",
            alert_ids=["b1"],
        )
        merged = correlator._merge_incidents([a, b])
        assert len(merged) == 1
        m = merged[0]
        # scope 取并集
        assert set(m.scope["hosts"]) == {"h1", "h2"}
        assert set(m.scope["services"]) == {"svc1", "svc2"}
        # events 拼接
        assert set(m.alert_ids) == {"a1", "a2", "b1"}
        # severity 取更高
        assert m.severity == "critical"
        # time_range 取更宽（started_at 更早，ended_at 更晚）
        assert m.started_at == "2026-01-01T00:00:00Z"
        assert m.ended_at == "2026-01-01T00:20:00Z"


# ═══════════════ entity_type 约束（避免误合并）═══════════════


class TestEntityTypeGuard:
    def test_different_entity_type_not_merged(self, correlator):
        """不同 entity_type（维度集合不同）即使子集-like 也不合并"""
        # A 仅 hosts 维度
        a = _make_incident("inc-a", _scope(hosts=["h1"]), alert_ids=["a1"])
        # B 仅 services 维度（即使服务名与 host 名相同也不合并）
        b = _make_incident("inc-b", _scope(services=["h1", "h2"]), alert_ids=["b1"])
        merged = correlator._merge_incidents([a, b])
        assert len(merged) == 2

    def test_should_merge_by_scope_helper(self, correlator):
        """_should_merge_by_scope 辅助方法判断正确"""
        from app.aiops.event_correlator import EventCorrelator

        # 子集关系 + 同类型 → True
        assert EventCorrelator._should_merge_by_scope(
            _scope(hosts=["h1"]), _scope(hosts=["h1", "h2"])
        ) is True
        # 相等 → False（已由精确合并处理）
        assert EventCorrelator._should_merge_by_scope(
            _scope(hosts=["h1"]), _scope(hosts=["h1"])
        ) is False
        # 无子集关系 → False
        assert EventCorrelator._should_merge_by_scope(
            _scope(hosts=["h1"]), _scope(hosts=["h2"])
        ) is False
        # 不同 entity_type → False
        assert EventCorrelator._should_merge_by_scope(
            _scope(hosts=["h1"]), _scope(services=["h1"])
        ) is False
