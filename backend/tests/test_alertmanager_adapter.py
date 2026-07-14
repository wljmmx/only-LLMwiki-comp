"""Alertmanager webhook adapter 单元测试（P2-2.5）

覆盖：
- alertmanager_to_events: 标准 v4 payload / 单 alert 容错 / 多 alert / 空 alerts / 非法 payload
- _map_severity: 词表映射 + 兜底
- _strip_port: host:port / IPv6 / 无端口 / 空值
- _build_message: summary+description / 仅 summary / 缺失回退 alertname
- should_resolve_incident: firing/resolved 判定
- POST /events/ingest/alertmanager 端点：dev 模式放行 / Bearer / query param / 401 / 400 / resolved 自动迁移
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.aiops.alertmanager_adapter import (
    _build_message,
    _map_severity,
    _strip_port,
    alertmanager_to_events,
    should_resolve_incident,
)

# ═══════════════ 测试 fixture ═══════════════


def _make_alert(
    *,
    status: str = "firing",
    alertname: str = "HighCPU",
    instance: str = "prod-web-01:9100",
    job: str = "node-exporter",
    severity: str = "warning",
    summary: str = "CPU > 90%",
    description: str = "CPU=95%",
    starts_at: str = "2026-07-13T08:23:45.123Z",
    ends_at: str | None = "2026-07-13T08:28:45.123Z",
    generator_url: str = "http://prom:9090/graph",
    fingerprint: str = "abc123def456",
    extra_labels: dict | None = None,
    extra_annotations: dict | None = None,
) -> dict:
    labels = {"alertname": alertname, "instance": instance, "job": job, "severity": severity}
    if extra_labels:
        labels.update(extra_labels)
    annotations = {"summary": summary, "description": description}
    if extra_annotations:
        annotations.update(extra_annotations)
    alert: dict = {
        "status": status,
        "labels": labels,
        "annotations": annotations,
        "startsAt": starts_at,
        "generatorURL": generator_url,
        "fingerprint": fingerprint,
    }
    if ends_at:
        alert["endsAt"] = ends_at
    return alert


def _make_payload(alerts: list[dict], **overrides) -> dict:
    payload = {
        "version": "4",
        "groupKey": "{}:{alertname=\"HighCPU\"}",
        "status": "firing",
        "receiver": "opskg-webhook",
        "groupLabels": {"alertname": "HighCPU"},
        "commonLabels": {"alertname": "HighCPU", "severity": "warning"},
        "commonAnnotations": {"summary": "CPU > 90%"},
        "externalURL": "http://alertmanager:9093",
        "alerts": alerts,
    }
    payload.update(overrides)
    return payload


# ═══════════════ _map_severity ═══════════════


class TestMapSeverity:
    def test_standard_am_severities(self):
        assert _map_severity("info") == "info"
        assert _map_severity("warning") == "warning"
        assert _map_severity("critical") == "critical"

    def test_synonyms(self):
        assert _map_severity("warn") == "warning"
        assert _map_severity("error") == "high"
        assert _map_severity("severe") == "critical"
        assert _map_severity("emergency") == "fatal"
        assert _map_severity("minor") == "low"

    def test_case_insensitive(self):
        assert _map_severity("WARNING") == "warning"
        assert _map_severity("Critical") == "critical"

    def test_none_falls_back_to_warning(self):
        assert _map_severity(None) == "warning"

    def test_empty_falls_back_to_warning(self):
        assert _map_severity("") == "warning"

    def test_unknown_falls_back_to_warning(self):
        assert _map_severity("unknown_level") == "warning"


# ═══════════════ _strip_port ═══════════════


class TestStripPort:
    def test_host_port(self):
        assert _strip_port("prod-web-01:9100") == "prod-web-01"

    def test_no_port(self):
        assert _strip_port("prod-web-01") == "prod-web-01"

    def test_empty(self):
        assert _strip_port("") == ""

    def test_none(self):
        assert _strip_port(None) == ""

    def test_ipv6_bracketed(self):
        # [::1]:9100 → ::1
        assert _strip_port("[::1]:9100") == "::1"

    def test_ipv6_bracketed_no_port(self):
        assert _strip_port("[::1]") == "::1"

    def test_multiple_colons_treated_as_ipv6(self):
        # 含多个 : 视为 IPv6 不剥离
        assert _strip_port("fe80::1") == "fe80::1"


# ═══════════════ _build_message ═══════════════


class TestBuildMessage:
    def test_summary_and_description(self):
        msg = _build_message({"summary": "S", "description": "D"}, {})
        assert msg == "S — D"

    def test_summary_only(self):
        msg = _build_message({"summary": "S"}, {})
        assert msg == "S"

    def test_description_only(self):
        msg = _build_message({"description": "D"}, {})
        assert msg == "D"

    def test_empty_falls_back_to_alertname(self):
        msg = _build_message({}, {"alertname": "HighCPU"})
        assert msg == "HighCPU"

    def test_completely_empty(self):
        msg = _build_message({}, {})
        assert msg == "(no message)"

    def test_whitespace_only_summary_ignored(self):
        msg = _build_message({"summary": "  ", "description": "D"}, {})
        assert msg == "D"


# ═══════════════ alertmanager_to_events ═══════════════


class TestAlertmanagerToEvents:
    def test_standard_v4_payload(self):
        payload = _make_payload([_make_alert()])
        events = alertmanager_to_events(payload)
        assert len(events) == 1
        ev = events[0]
        assert ev["host"] == "prod-web-01"
        assert ev["service"] == "node-exporter"
        assert ev["component"] == "HighCPU"
        assert ev["severity"] == "warning"
        assert ev["message"] == "CPU > 90% — CPU=95%"
        assert ev["source"] == "alertmanager"
        assert ev["timestamp"] == "2026-07-13T08:23:45.123Z"
        # attributes 完整性
        attrs = ev["attributes"]
        assert attrs["alertname"] == "HighCPU"
        assert attrs["status"] == "firing"
        assert attrs["fingerprint"] == "abc123def456"
        assert attrs["generatorURL"] == "http://prom:9090/graph"
        assert attrs["endsAt"] == "2026-07-13T08:28:45.123Z"
        assert attrs["annotations"]["summary"] == "CPU > 90%"

    def test_multiple_alerts(self):
        payload = _make_payload([
            _make_alert(alertname="HighCPU", instance="web-01:9100"),
            _make_alert(alertname="DiskFull", instance="db-01:9100", severity="critical"),
            _make_alert(alertname="MemHigh", instance="cache-01:9100", severity="info"),
        ])
        events = alertmanager_to_events(payload)
        assert len(events) == 3
        assert {e["component"] for e in events} == {"HighCPU", "DiskFull", "MemHigh"}
        assert {e["host"] for e in events} == {"web-01", "db-01", "cache-01"}
        severities = {e["component"]: e["severity"] for e in events}
        assert severities["DiskFull"] == "critical"
        assert severities["MemHigh"] == "info"

    def test_empty_alerts(self):
        payload = _make_payload([])
        events = alertmanager_to_events(payload)
        assert events == []

    def test_single_alert_object_no_alerts_array(self):
        """容错：直接发单 alert 对象不包 alerts 数组"""
        alert = _make_alert()
        events = alertmanager_to_events(alert)
        assert len(events) == 1
        assert events[0]["component"] == "HighCPU"

    def test_extra_labels_go_to_tags(self):
        alert = _make_alert(extra_labels={"env": "prod", "cluster": "us-east-1"})
        payload = _make_payload([alert])
        events = alertmanager_to_events(payload)
        ev = events[0]
        # 保留字段不在 tags
        assert "alertname" not in ev["tags"]
        assert "instance" not in ev["tags"]
        assert "job" not in ev["tags"]
        assert "severity" not in ev["tags"]
        # 额外 label 在 tags
        assert ev["tags"]["env"] == "prod"
        assert ev["tags"]["cluster"] == "us-east-1"

    def test_missing_severity_label(self):
        alert = _make_alert(severity="")
        # severity="" 会被 _map_severity 视为 None → warning
        payload = _make_payload([alert])
        events = alertmanager_to_events(payload)
        assert events[0]["severity"] == "warning"

    def test_no_severity_label_at_all(self):
        alert = _make_alert()
        del alert["labels"]["severity"]
        payload = _make_payload([alert])
        events = alertmanager_to_events(payload)
        assert events[0]["severity"] == "warning"

    def test_resolved_status_preserved(self):
        alert = _make_alert(status="resolved")
        payload = _make_payload([alert])
        events = alertmanager_to_events(payload)
        assert events[0]["attributes"]["status"] == "resolved"

    def test_no_ends_at(self):
        alert = _make_alert(ends_at=None)
        payload = _make_payload([alert])
        events = alertmanager_to_events(payload)
        # endsAt 缺失时不应在 attributes 中
        assert "endsAt" not in events[0]["attributes"]

    def test_no_annotations(self):
        alert = _make_alert()
        del alert["annotations"]
        payload = _make_payload([alert])
        events = alertmanager_to_events(payload)
        # 回退到 alertname
        assert events[0]["message"] == "HighCPU"

    def test_invalid_payload_type(self):
        with pytest.raises(ValueError, match="dict"):
            alertmanager_to_events(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_missing_alerts_field(self):
        with pytest.raises(ValueError, match="alerts"):
            alertmanager_to_events({"version": "4"})

    def test_alerts_not_list(self):
        with pytest.raises(ValueError, match="list"):
            alertmanager_to_events({"alerts": "notalist"})

    def test_skips_non_dict_alerts(self):
        payload = _make_payload([
            _make_alert(),
            "not a dict",  # type: ignore[list-item]
            None,  # type: ignore[list-item]
        ])
        events = alertmanager_to_events(payload)
        assert len(events) == 1  # 仅 1 个有效 alert


# ═══════════════ should_resolve_incident ═══════════════


class TestShouldResolveIncident:
    def test_firing_event(self):
        ev = {"attributes": {"status": "firing"}}
        assert should_resolve_incident(ev) is False

    def test_resolved_event(self):
        ev = {"attributes": {"status": "resolved"}}
        assert should_resolve_incident(ev) is True

    def test_missing_attributes(self):
        ev = {}
        assert should_resolve_incident(ev) is False

    def test_missing_status(self):
        ev = {"attributes": {}}
        assert should_resolve_incident(ev) is False

    def test_real_resolved_alert_from_payload(self):
        alert = _make_alert(status="resolved")
        payload = _make_payload([alert])
        events = alertmanager_to_events(payload)
        assert should_resolve_incident(events[0]) is True


# ═══════════════ API 端点：POST /events/ingest/alertmanager ═══════════════


@pytest.fixture
def isolated_events_db(tmp_path, monkeypatch):
    """将 events 数据库重定向到 tmp_path，并重置 correlator 单例（与 test_aiops.py 同模式）"""
    import app.aiops.change_correlator as ch
    import app.aiops.event_correlator as ev

    db_file = tmp_path / "events.db"
    monkeypatch.setattr(ev, "DB_PATH", db_file)
    monkeypatch.setattr(ch, "DB_PATH", db_file)
    monkeypatch.setattr(ev, "_correlator", None)
    monkeypatch.setattr(ch, "_correlator", None)
    yield db_file


class _FakeSettings:
    """可配置 token 的 fake settings（参照 test_auth.py 模式）"""

    def __init__(self, *, api_token: str = "", alertmanager_ingest_token: str = ""):
        self.api_token = api_token
        self.alertmanager_ingest_token = alertmanager_ingest_token


def _patch_settings(monkeypatch, *, api_token: str = "", am_token: str = ""):
    """同步 patch events_router 与 token_auth 两处的 get_settings

    events_router 用 alertmanager_ingest_token + api_token 做认证
    token_auth.verify_token_string 用 api_token 验证 session/legacy token
    """
    fake = _FakeSettings(api_token=api_token, alertmanager_ingest_token=am_token)
    from app.auth import token_auth
    from app.routers import events_router

    monkeypatch.setattr(events_router, "get_settings", lambda: fake)
    monkeypatch.setattr(token_auth, "get_settings", lambda: fake)


@pytest.fixture
def client(isolated_events_db):
    """TestClient（DB 已隔离，settings 在各测试中按需 patch）"""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


class TestAlertmanagerEndpoint:
    """POST /events/ingest/alertmanager 端点集成测试"""

    def test_dev_mode_no_auth_required(self, client, monkeypatch):
        """dev 模式（alertmanager_ingest_token + api_token 均空）→ 200"""
        _patch_settings(monkeypatch, api_token="", am_token="")
        payload = _make_payload([_make_alert()])
        resp = client.post("/events/ingest/alertmanager", json=payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ingested"] == 1
        assert data["source"] == "alertmanager"
        assert data["alerts_total"] == 1

    def test_bearer_header_auth(self, client, monkeypatch):
        """配置 alertmanager_ingest_token 后，Bearer header 通过"""
        _patch_settings(monkeypatch, api_token="", am_token="secret-am-token")
        payload = _make_payload([_make_alert()])
        resp = client.post(
            "/events/ingest/alertmanager",
            json=payload,
            headers={"Authorization": "Bearer secret-am-token"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ingested"] == 1

    def test_query_param_auth(self, client, monkeypatch):
        """Alertmanager 通过 ?token= 携带 token"""
        _patch_settings(monkeypatch, api_token="", am_token="query-token-xyz")
        payload = _make_payload([_make_alert()])
        resp = client.post(
            "/events/ingest/alertmanager?token=query-token-xyz",
            json=payload,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ingested"] == 1

    def test_falls_back_to_api_token(self, client, monkeypatch):
        """alertmanager_ingest_token 未配置时，回退到 api_token"""
        _patch_settings(monkeypatch, api_token="shared-legacy-token", am_token="")
        payload = _make_payload([_make_alert()])
        resp = client.post(
            "/events/ingest/alertmanager",
            json=payload,
            headers={"Authorization": "Bearer shared-legacy-token"},
        )
        assert resp.status_code == 200, resp.text

    def test_401_when_token_required_but_missing(self, client, monkeypatch):
        """配置 token 后无凭证 → 401"""
        _patch_settings(monkeypatch, api_token="", am_token="required-token")
        payload = _make_payload([_make_alert()])
        resp = client.post("/events/ingest/alertmanager", json=payload)
        assert resp.status_code == 401

    def test_401_with_wrong_token(self, client, monkeypatch):
        """配置 token 后凭证错误 → 401"""
        _patch_settings(monkeypatch, api_token="", am_token="correct-token")
        payload = _make_payload([_make_alert()])
        resp = client.post(
            "/events/ingest/alertmanager?token=wrong-token",
            json=payload,
        )
        assert resp.status_code == 401

    def test_400_invalid_payload_missing_alerts(self, client, monkeypatch):
        """payload 缺 alerts 字段 → 400"""
        _patch_settings(monkeypatch, api_token="", am_token="")
        resp = client.post(
            "/events/ingest/alertmanager",
            json={"version": "4"},  # 缺 alerts
        )
        assert resp.status_code == 400
        assert "alerts" in resp.json()["detail"].lower()

    def test_400_alerts_not_list(self, client, monkeypatch):
        """alerts 非 list → 400"""
        _patch_settings(monkeypatch, api_token="", am_token="")
        resp = client.post(
            "/events/ingest/alertmanager",
            json={"alerts": "notalist"},
        )
        assert resp.status_code == 400

    def test_empty_alerts_returns_zero(self, client, monkeypatch):
        """空 alerts 数组 → 200 + ingested=0"""
        _patch_settings(monkeypatch, api_token="", am_token="")
        payload = _make_payload([])
        resp = client.post("/events/ingest/alertmanager", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] == 0
        assert data["alerts_total"] == 0

    def test_multiple_alerts_ingested(self, client, monkeypatch):
        """3 个 alert → ingested=3"""
        _patch_settings(monkeypatch, api_token="", am_token="")
        payload = _make_payload([
            _make_alert(alertname="HighCPU", instance="web-01:9100"),
            _make_alert(alertname="DiskFull", instance="db-01:9100", severity="critical"),
            _make_alert(alertname="MemHigh", instance="cache-01:9100", severity="info"),
        ])
        resp = client.post("/events/ingest/alertmanager", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] == 3
        assert data["alerts_total"] == 3
        assert data["skipped_duplicates"] == 0

    def test_resolved_alert_triggers_incident_auto_resolve(
        self, client, monkeypatch
    ):
        """firing → correlate → 创建 incident；resolved 同 alertname → 自动迁移"""
        _patch_settings(monkeypatch, api_token="", am_token="")

        # 用当前时间附近的时间戳，确保落在 correlate 的 60min 窗口内
        from datetime import datetime, timedelta, timezone

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        minute_ago_iso = (
            datetime.now(timezone.utc) - timedelta(seconds=30)
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        # 1. 发送 firing alert
        firing_payload = _make_payload([
            _make_alert(
                alertname="DiskFull",
                instance="db-01:9100",
                severity="critical",
                fingerprint="fp-disk-001",
                status="firing",
                starts_at=minute_ago_iso,
            )
        ])
        resp = client.post("/events/ingest/alertmanager", json=firing_payload)
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 1

        # 2. 触发关联创建 incident
        # 注：/events/correlate 走 verify_token 依赖；dev 模式 fake settings api_token="" → 放行
        corr = client.post("/events/correlate", json={"since_minutes": 60})
        assert corr.status_code == 200, corr.text
        incidents = corr.json().get("incidents", [])
        assert len(incidents) >= 1, "应至少创建 1 个 incident"

        # 3. 发送 resolved alert 同 alertname
        resolved_payload = _make_payload([
            _make_alert(
                alertname="DiskFull",
                instance="db-01:9100",
                severity="critical",
                fingerprint="fp-disk-001",
                status="resolved",
                starts_at=now_iso,
            )
        ])
        resp = client.post("/events/ingest/alertmanager", json=resolved_payload)
        assert resp.status_code == 200
        data = resp.json()
        # resolved 自动迁移计数 ≥ 1
        assert data["resolved"] >= 1, f"应自动迁移 ≥1 个 incident，实际 {data['resolved']}"

    def test_event_persisted_with_alertmanager_source(self, client, monkeypatch):
        """ingest 后事件持久化 source='alertmanager'"""
        _patch_settings(monkeypatch, api_token="", am_token="")
        payload = _make_payload([_make_alert()])
        resp = client.post("/events/ingest/alertmanager", json=payload)
        assert resp.status_code == 200

        from app.aiops.event_correlator import _get_db

        conn = _get_db()
        rows = conn.execute(
            "SELECT source, host, service, component FROM events WHERE source = ?",
            ("alertmanager",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "prod-web-01"
        assert rows[0][2] == "node-exporter"
        assert rows[0][3] == "HighCPU"
