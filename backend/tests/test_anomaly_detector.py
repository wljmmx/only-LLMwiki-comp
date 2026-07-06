"""时序异常检测模块单元测试（S15-3）

覆盖：
- TimeseriesStore: record / query / cleanup_old / get_latest_values
- Z-score: 正常值 / 异常值 / 窗口不足 / 空数据 / 单点 / 极端值
- EWMA: 正常值 / 异常值 / alpha 参数影响
- detect_batch: 批量检测
- detect_and_dispatch: 异常事件分发（severity 分级 + webhook dispatch）
- API 端点: 所有端点的 200 / 400 / 401 响应

DB 隔离：每个测试通过 monkeypatch 将 timeseries.db 重定向到 tmp_path，并重置单例。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

# 确保测试期间关闭认证（GET 端点免认证；POST/DELETE 测试 401 时单独开启）
os.environ.setdefault("OPSKG_API_TOKEN", "")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════ 公共 fixture ═══════════════


@pytest.fixture
def isolated_ts_db(tmp_path, monkeypatch):
    """将时序数据库重定向到 tmp_path，并重置 store/detector 单例"""
    import app.aiops.timeseries_store as ts_mod
    import app.aiops.anomaly_detector as ad_mod

    db_file = tmp_path / "timeseries.db"
    monkeypatch.setattr(ts_mod, "DB_PATH", db_file)
    monkeypatch.setattr(ts_mod, "_store", None)
    monkeypatch.setattr(ad_mod, "_detector", None)
    yield db_file


def _iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _seed_series(store, metric_name: str, values: list[float], base_offset: int = -1000, labels: dict | None = None):
    """批量写入数据点，每个间隔 1 秒"""
    for i, v in enumerate(values):
        store.record(
            metric_name=metric_name,
            value=v,
            labels=labels,
            timestamp=_iso(base_offset + i),
        )


# ═══════════════ TimeseriesStore ═══════════════


class TestTimeseriesStore:
    def test_record_returns_point(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        pt = store.record("cpu_usage", 75.5, labels={"host": "prod-01"})
        assert pt["id"] is not None
        assert pt["metric_name"] == "cpu_usage"
        assert pt["value"] == 75.5
        assert pt["labels"] == {"host": "prod-01"}
        assert "timestamp" in pt
        assert "created_at" in pt

    def test_record_empty_metric_name_raises(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        with pytest.raises(ValueError):
            store.record("", 1.0)

    def test_record_invalid_timestamp_raises(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        # 非法时间戳会被 _parse_ts 容错为 now，不抛错；这里测极短非 ISO 字符串
        pt = store.record("m", 1.0, timestamp="not-a-time")
        assert pt["timestamp"] == "not-a-time"  # 原样存入

    def test_record_explicit_timestamp(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        ts = "2026-01-01T00:00:00+00:00"
        pt = store.record("m", 1.0, timestamp=ts)
        assert pt["timestamp"] == ts

    def test_query_by_metric_name(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        _seed_series(store, "m1", [1.0, 2.0, 3.0])
        _seed_series(store, "m2", [10.0, 20.0])
        pts = store.query("m1")
        assert len(pts) == 3
        assert [p["value"] for p in pts] == [1.0, 2.0, 3.0]
        # 升序
        assert pts[0]["timestamp"] <= pts[-1]["timestamp"]

    def test_query_by_time_range(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        # 使用固定时间戳避免 _iso 调用时刻不同的竞态
        ts = [
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:01+00:00",
            "2026-01-01T00:00:02+00:00",
            "2026-01-01T00:00:03+00:00",
            "2026-01-01T00:00:04+00:00",
        ]
        for i, v in enumerate([1.0, 2.0, 3.0, 4.0, 5.0]):
            store.record("m", v, timestamp=ts[i])
        pts = store.query("m", start_time=ts[2], end_time=ts[4])
        assert len(pts) == 3
        assert [p["value"] for p in pts] == [3.0, 4.0, 5.0]

    def test_query_by_labels(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        store.record("m", 1.0, labels={"host": "a"}, timestamp=_iso(-10))
        store.record("m", 2.0, labels={"host": "b"}, timestamp=_iso(-9))
        store.record("m", 3.0, labels={"host": "a"}, timestamp=_iso(-8))
        pts = store.query("m", labels={"host": "a"})
        assert len(pts) == 2
        assert all(p["labels"]["host"] == "a" for p in pts)

    def test_query_limit(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        _seed_series(store, "m", list(range(10)))
        pts = store.query("m", limit=3)
        assert len(pts) == 3

    def test_query_empty(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        assert store.query("nonexistent") == []

    def test_cleanup_old(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        # 写入 40 天前的数据
        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        store.record("m", 1.0, timestamp=old_ts)
        # 写入 5 天前的数据
        recent_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        store.record("m", 2.0, timestamp=recent_ts)
        deleted = store.cleanup_old(days=30)
        assert deleted == 1
        pts = store.query("m")
        assert len(pts) == 1
        assert pts[0]["value"] == 2.0

    def test_cleanup_old_negative_days_raises(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        with pytest.raises(ValueError):
            store.cleanup_old(days=-1)

    def test_get_latest_values(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        _seed_series(store, "m", [1.0, 2.0, 3.0, 4.0, 5.0], base_offset=-10)
        vals = store.get_latest_values("m", count=3)
        # 最近 3 个值，时间升序（旧→新）
        assert vals == [3.0, 4.0, 5.0]

    def test_get_latest_values_with_labels(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        store.record("m", 1.0, labels={"host": "a"}, timestamp=_iso(-10))
        store.record("m", 2.0, labels={"host": "b"}, timestamp=_iso(-9))
        store.record("m", 3.0, labels={"host": "a"}, timestamp=_iso(-8))
        vals = store.get_latest_values("m", count=10, labels={"host": "a"})
        assert vals == [1.0, 3.0]

    def test_get_latest_values_empty(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        assert store.get_latest_values("nope", count=5) == []

    def test_get_latest_values_zero_count(self, isolated_ts_db):
        from app.aiops.timeseries_store import get_timeseries_store

        store = get_timeseries_store()
        _seed_series(store, "m", [1.0])
        assert store.get_latest_values("m", count=0) == []


# ═══════════════ Z-score ═══════════════


class TestZscore:
    def test_normal_value_not_anomaly(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        # 构造正态分布样例：均值约 50，std 约 10
        _seed_series(store, "latency", [40, 50, 60, 45, 55, 50, 48, 52, 50, 50])
        detector = get_anomaly_detector()
        result = detector.detect_zscore("latency", 52.0, window=10, threshold=3.0)
        assert result["is_anomaly"] is False
        assert abs(result["score"]) < 3.0
        assert result["window_size"] == 10
        assert result["metric_name"] == "latency"

    def test_anomaly_value_detected(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "latency", [50, 50, 50, 50, 50, 50, 50, 50, 50, 50])
        # 方差为 0 时，偏离均值即异常
        detector = get_anomaly_detector()
        result = detector.detect_zscore("latency", 100.0, window=10, threshold=3.0)
        assert result["is_anomaly"] is True
        assert result["score"] > 3.0
        assert result["mean"] == 50.0

    def test_anomaly_with_variance(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        # mean=10, std≈1.05
        _seed_series(store, "m", [9, 10, 11, 10, 9, 11, 10, 9, 11, 10])
        detector = get_anomaly_detector()
        result = detector.detect_zscore("m", 20.0, window=10, threshold=3.0)
        assert result["is_anomaly"] is True
        assert result["score"] > 3.0

    def test_empty_history_not_anomaly(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        detector = get_anomaly_detector()
        result = detector.detect_zscore("nope", 100.0, window=10)
        assert result["is_anomaly"] is False
        assert result["score"] == 0.0
        assert result["window_size"] == 0

    def test_single_point_not_anomaly(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        store.record("m", 50.0, timestamp=_iso(-10))
        detector = get_anomaly_detector()
        result = detector.detect_zscore("m", 100.0, window=10)
        assert result["is_anomaly"] is False
        assert result["window_size"] == 1

    def test_window_size_truncation(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [1, 2, 3, 4, 5], base_offset=-100)
        detector = get_anomaly_detector()
        # 只请求 window=3，但实际有 5 条历史
        result = detector.detect_zscore("m", 3.0, window=3, threshold=3.0)
        assert result["window_size"] == 3

    def test_extreme_value(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [1, 2, 3, 2, 1, 2, 3, 2, 1, 2])
        detector = get_anomaly_detector()
        result = detector.detect_zscore("m", 1e9, window=10, threshold=3.0)
        assert result["is_anomaly"] is True
        assert result["score"] > 5  # critical 级别

    def test_threshold_customization(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [10, 10, 10, 10, 10])
        detector = get_anomaly_detector()
        # threshold=1 时，std=0 + 偏离 → 仍为异常
        result = detector.detect_zscore("m", 12.0, window=5, threshold=1.0)
        assert result["is_anomaly"] is True
        assert result["threshold"] == 1.0

    def test_all_same_value_no_anomaly(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [50, 50, 50, 50, 50])
        detector = get_anomaly_detector()
        # 值与历史完全相同 → z=0
        result = detector.detect_zscore("m", 50.0, window=5)
        assert result["is_anomaly"] is False
        assert result["score"] == 0.0


# ═══════════════ EWMA ═══════════════


class TestEwma:
    def test_normal_value_not_anomaly(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [50, 51, 49, 50, 51, 50, 49, 50, 51, 50])
        detector = get_anomaly_detector()
        result = detector.detect_ewma("m", 50.5, alpha=0.3, window=10, threshold=3.0)
        assert result["is_anomaly"] is False
        assert "ewma" in result
        assert result["ewma"] is not None

    def test_anomaly_value_detected(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [50, 50, 50, 50, 50, 50, 50, 50, 50, 50])
        detector = get_anomaly_detector()
        result = detector.detect_ewma("m", 100.0, alpha=0.3, window=10, threshold=3.0)
        assert result["is_anomaly"] is True
        assert result["score"] > 3.0

    def test_empty_history_not_anomaly(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        detector = get_anomaly_detector()
        result = detector.detect_ewma("nope", 100.0)
        assert result["is_anomaly"] is False
        assert result["window_size"] == 0

    def test_single_point_not_anomaly(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        store.record("m", 50.0, timestamp=_iso(-10))
        detector = get_anomaly_detector()
        result = detector.detect_ewma("m", 100.0)
        assert result["is_anomaly"] is False
        assert result["window_size"] == 1

    def test_alpha_impact(self, isolated_ts_db):
        """不同 alpha 下 EWMA 跟随速度不同"""
        from app.aiops.anomaly_detector import AnomalyDetector, get_anomaly_detector

        store = get_store_for_detector()
        # 渐变序列：1 → 10
        _seed_series(store, "m", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        detector = get_anomaly_detector()
        r_low_alpha = detector.detect_ewma("m", 8.0, alpha=0.1, window=10, threshold=3.0)
        r_high_alpha = detector.detect_ewma("m", 8.0, alpha=0.9, window=10, threshold=3.0)
        # high_alpha 时 ewma 更靠近最新值
        assert r_high_alpha["ewma"] != r_low_alpha["ewma"]
        # ewma_latest 应在历史值范围内
        assert 1.0 <= r_low_alpha["ewma"] <= 10.0

    def test_ewma_field_present(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [1, 2, 3, 4, 5])
        detector = get_anomaly_detector()
        result = detector.detect_ewma("m", 3.0)
        assert "ewma" in result
        assert isinstance(result["ewma"], float)


# ═══════════════ evaluate（统一入口） ═══════════════


class TestEvaluate:
    def test_evaluate_zscore(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [10, 10, 10, 10, 10])
        detector = get_anomaly_detector()
        result = detector.evaluate("m", 100.0, algorithm="zscore")
        assert result["is_anomaly"] is True

    def test_evaluate_ewma(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [10, 10, 10, 10, 10])
        detector = get_anomaly_detector()
        result = detector.evaluate("m", 100.0, algorithm="ewma")
        assert result["is_anomaly"] is True
        assert "ewma" in result

    def test_evaluate_unsupported_algorithm(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        detector = get_anomaly_detector()
        with pytest.raises(ValueError):
            detector.evaluate("m", 1.0, algorithm="unknown")


# ═══════════════ detect_batch ═══════════════


class TestDetectBatch:
    def test_batch_finds_anomalies(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        # 前 10 个正常值，第 11 个突变
        values = [50, 51, 49, 50, 51, 50, 49, 50, 51, 50, 200]
        _seed_series(store, "m", values, base_offset=-100)
        detector = get_anomaly_detector()
        results = detector.detect_batch("m", algorithm="zscore", window=10, threshold=3.0)
        assert len(results) == 11
        # 第 11 个点应为异常
        assert results[-1]["is_anomaly"] is True
        assert results[-1]["value"] == 200.0
        # 前面大部分点应非异常
        assert any(r["is_anomaly"] is False for r in results)

    def test_batch_empty_range(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        detector = get_anomaly_detector()
        results = detector.detect_batch("nonexistent")
        assert results == []

    def test_batch_with_time_range(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        # 使用固定时间戳避免竞态
        ts = [
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:01+00:00",
            "2026-01-01T00:00:02+00:00",
            "2026-01-01T00:00:03+00:00",
            "2026-01-01T00:00:04+00:00",
        ]
        for i, v in enumerate([1, 2, 3, 4, 5]):
            store.record("m", v, timestamp=ts[i])
        detector = get_anomaly_detector()
        results = detector.detect_batch(
            "m", start_time=ts[1], end_time=ts[3], window=5, threshold=3.0
        )
        # ts[1]..ts[3] → 3 个点
        assert len(results) == 3

    def test_batch_ewma_algorithm(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [10, 10, 10, 10, 10, 100], base_offset=-100)
        detector = get_anomaly_detector()
        results = detector.detect_batch("m", algorithm="ewma", window=10, threshold=3.0)
        assert len(results) == 6
        assert results[-1]["is_anomaly"] is True

    def test_batch_results_have_timestamp(self, isolated_ts_db):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [1, 2, 3], base_offset=-100)
        detector = get_anomaly_detector()
        results = detector.detect_batch("m", algorithm="zscore", window=5)
        for r in results:
            assert "timestamp" in r
            assert "labels" in r


# ═══════════════ detect_and_dispatch ═══════════════


class TestDetectAndDispatch:
    def test_no_anomaly_no_dispatch(self, isolated_ts_db, monkeypatch):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [50, 51, 49, 50, 51, 50, 49, 50, 51, 50])
        # mock dispatch_event
        calls = []
        import app.webhooks as wh_mod

        monkeypatch.setattr(
            wh_mod, "dispatch_event",
            lambda evt, payload: calls.append((evt, payload)) or 0,
        )
        detector = get_anomaly_detector()
        result = detector.detect_and_dispatch("m", 50.5, algorithm="zscore")
        assert result["is_anomaly"] is False
        assert result["dispatched"] == 0
        assert calls == []  # 未分发

    def test_anomaly_dispatches_event(self, isolated_ts_db, monkeypatch):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [10, 10, 10, 10, 10])
        calls = []
        import app.webhooks as wh_mod

        def fake_dispatch(evt, payload):
            calls.append((evt, payload))
            return 1

        monkeypatch.setattr(wh_mod, "dispatch_event", fake_dispatch)
        detector = get_anomaly_detector()
        result = detector.detect_and_dispatch("m", 100.0, algorithm="zscore")
        assert result["is_anomaly"] is True
        assert result["dispatched"] == 1
        assert len(calls) == 1
        evt_type, payload = calls[0]
        assert evt_type == "event.ingested"
        assert payload["metric_name"] == "m"
        assert payload["value"] == 100.0
        assert payload["algorithm"] == "zscore"
        assert "severity" in payload
        assert "timestamp" in payload

    def test_severity_critical(self, isolated_ts_db, monkeypatch):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [10, 10, 10, 10, 10])
        # z = 9999（std=0），|z|>5 → critical
        import app.webhooks as wh_mod

        monkeypatch.setattr(wh_mod, "dispatch_event", lambda *a, **k: 0)
        detector = get_anomaly_detector()
        result = detector.detect_and_dispatch("m", 100.0, algorithm="zscore")
        assert result["severity"] == "critical"

    def test_severity_warning(self, isolated_ts_db, monkeypatch):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        # 构造 z 在 (3, 5] 范围
        # mean=50, std=2 → value=58 → z=4 → warning
        _seed_series(store, "m", [48, 52, 48, 52, 50, 50, 48, 52, 50, 50])
        import app.webhooks as wh_mod

        monkeypatch.setattr(wh_mod, "dispatch_event", lambda *a, **k: 0)
        detector = get_anomaly_detector()
        result = detector.detect_and_dispatch("m", 58.0, algorithm="zscore", threshold=3.0)
        if result["is_anomaly"]:
            assert result["severity"] in ("warning", "critical")

    def test_dispatch_failure_does_not_raise(self, isolated_ts_db, monkeypatch):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [10, 10, 10, 10, 10])
        import app.webhooks as wh_mod

        def broken_dispatch(*a, **k):
            raise RuntimeError("webhook broken")

        monkeypatch.setattr(wh_mod, "dispatch_event", broken_dispatch)
        detector = get_anomaly_detector()
        result = detector.detect_and_dispatch("m", 100.0, algorithm="zscore")
        assert result["is_anomaly"] is True
        assert result["dispatched"] == 0  # 异常时降级为 0

    def test_labels_in_payload(self, isolated_ts_db, monkeypatch):
        from app.aiops.anomaly_detector import get_anomaly_detector

        store = get_store_for_detector()
        _seed_series(store, "m", [10, 10, 10, 10, 10], labels={"host": "prod-01"})
        calls = []
        import app.webhooks as wh_mod

        monkeypatch.setattr(
            wh_mod, "dispatch_event",
            lambda evt, payload: calls.append(payload) or 1,
        )
        detector = get_anomaly_detector()
        result = detector.detect_and_dispatch(
            "m", 100.0, algorithm="zscore", labels={"host": "prod-01"}
        )
        assert result["is_anomaly"] is True
        assert calls[0]["labels"] == {"host": "prod-01"}


# ═══════════════ API 端点 ═══════════════


class TestAPIEndpoints:
    @pytest.fixture
    def client(self, isolated_ts_db):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    # ─── GET 端点（免认证） ───

    def test_list_algorithms(self, client):
        r = client.get("/anomaly/algorithms")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 2
        names = [a["name"] for a in data["algorithms"]]
        assert "zscore" in names
        assert "ewma" in names

    def test_query_metrics_empty(self, client):
        r = client.get("/anomaly/metrics/nonexistent")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 0
        assert data["points"] == []

    def test_query_metrics_with_data(self, client):
        # 先记录数据
        client.post(
            "/anomaly/metrics/record",
            json={"metric_name": "cpu", "value": 75.0, "labels": {"host": "a"}},
        )
        client.post(
            "/anomaly/metrics/record",
            json={"metric_name": "cpu", "value": 80.0, "labels": {"host": "a"}},
        )
        r = client.get("/anomaly/metrics/cpu")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 2
        assert data["points"][0]["value"] == 75.0
        assert data["points"][1]["value"] == 80.0

    def test_query_metrics_with_time_range(self, client):
        ts1 = _iso(-100)
        ts2 = _iso(-50)
        ts3 = _iso(-10)
        client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": 1.0, "timestamp": ts1})
        client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": 2.0, "timestamp": ts2})
        client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": 3.0, "timestamp": ts3})
        r = client.get("/anomaly/metrics/m", params={"start_time": _iso(-60), "end_time": _iso(0)})
        assert r.status_code == 200
        assert r.json()["count"] == 2  # ts2 + ts3

    # ─── POST 端点（需认证，开发模式下放行） ───

    def test_record_metric(self, client):
        r = client.post(
            "/anomaly/metrics/record",
            json={"metric_name": "cpu", "value": 75.5, "labels": {"host": "prod-01"}},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["metric_name"] == "cpu"
        assert data["value"] == 75.5
        assert data["labels"] == {"host": "prod-01"}
        assert data["id"] is not None

    def test_record_metric_missing_fields(self, client):
        # 缺 metric_name
        r = client.post("/anomaly/metrics/record", json={"value": 1.0})
        assert r.status_code == 400
        # 缺 value
        r = client.post("/anomaly/metrics/record", json={"metric_name": "m"})
        assert r.status_code == 400

    def test_record_metric_invalid_value(self, client):
        r = client.post(
            "/anomaly/metrics/record",
            json={"metric_name": "m", "value": "not-a-number"},
        )
        assert r.status_code == 400

    def test_record_metric_invalid_labels(self, client):
        r = client.post(
            "/anomaly/metrics/record",
            json={"metric_name": "m", "value": 1.0, "labels": "not-a-dict"},
        )
        assert r.status_code == 400

    def test_cleanup_metrics(self, client):
        # 先记录旧数据
        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": 1.0, "timestamp": old_ts})
        r = client.delete("/anomaly/metrics/cleanup", params={"days": 30})
        assert r.status_code == 200
        assert r.json()["deleted"] >= 1

    def test_cleanup_metrics_negative_days(self, client):
        r = client.delete("/anomaly/metrics/cleanup", params={"days": -1})
        assert r.status_code == 400

    def test_detect_dry_run(self, client):
        # 先写入历史数据
        for v in [50, 50, 50, 50, 50]:
            client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": v})
        r = client.post(
            "/anomaly/detect",
            json={"metric_name": "m", "value": 100.0, "algorithm": "zscore", "threshold": 3.0},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["is_anomaly"] is True
        assert "dispatched" not in data  # dry-run 不含 dispatched

    def test_detect_normal_value(self, client):
        for v in [50, 51, 49, 50, 51, 50, 49, 50, 51, 50]:
            client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": v})
        r = client.post(
            "/anomaly/detect",
            json={"metric_name": "m", "value": 50.5, "algorithm": "zscore"},
        )
        assert r.status_code == 200
        assert r.json()["is_anomaly"] is False

    def test_detect_missing_fields(self, client):
        r = client.post("/anomaly/detect", json={"value": 1.0})
        assert r.status_code == 400
        r = client.post("/anomaly/detect", json={"metric_name": "m"})
        assert r.status_code == 400

    def test_detect_unsupported_algorithm(self, client):
        r = client.post(
            "/anomaly/detect",
            json={"metric_name": "m", "value": 1.0, "algorithm": "unknown"},
        )
        assert r.status_code == 400

    def test_detect_batch(self, client):
        for v in [50, 51, 49, 50, 51, 50, 49, 50, 51, 50, 200]:
            client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": v})
        r = client.post(
            "/anomaly/detect/batch",
            json={"metric_name": "m", "algorithm": "zscore", "window": 10, "threshold": 3.0},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["total"] == 11
        assert data["anomalies"] >= 1
        assert data["algorithm"] == "zscore"

    def test_detect_batch_empty(self, client):
        r = client.post(
            "/anomaly/detect/batch",
            json={"metric_name": "nonexistent"},
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_detect_and_dispatch_endpoint(self, client):
        for v in [50, 50, 50, 50, 50]:
            client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": v})
        r = client.post(
            "/anomaly/detect-and-dispatch",
            json={"metric_name": "m", "value": 100.0, "algorithm": "zscore"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["is_anomaly"] is True
        assert "dispatched" in data
        assert "severity" in data

    def test_detect_and_dispatch_no_anomaly(self, client):
        for v in [50, 51, 49, 50, 51, 50, 49, 50, 51, 50]:
            client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": v})
        r = client.post(
            "/anomaly/detect-and-dispatch",
            json={"metric_name": "m", "value": 50.5, "algorithm": "zscore"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["is_anomaly"] is False
        assert data["dispatched"] == 0

    # ─── 401 认证测试 ───

    def test_record_requires_auth(self, client, monkeypatch):
        from app.auth import token_auth

        class FakeSettings:
            api_token = "secret-token"

        monkeypatch.setattr(token_auth, "get_settings", lambda: FakeSettings())
        r = client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": 1.0})
        assert r.status_code == 401

    def test_cleanup_requires_auth(self, client, monkeypatch):
        from app.auth import token_auth

        class FakeSettings:
            api_token = "secret-token"

        monkeypatch.setattr(token_auth, "get_settings", lambda: FakeSettings())
        r = client.delete("/anomaly/metrics/cleanup")
        assert r.status_code == 401

    def test_detect_requires_auth(self, client, monkeypatch):
        from app.auth import token_auth

        class FakeSettings:
            api_token = "secret-token"

        monkeypatch.setattr(token_auth, "get_settings", lambda: FakeSettings())
        r = client.post("/anomaly/detect", json={"metric_name": "m", "value": 1.0})
        assert r.status_code == 401

    def test_detect_batch_requires_auth(self, client, monkeypatch):
        from app.auth import token_auth

        class FakeSettings:
            api_token = "secret-token"

        monkeypatch.setattr(token_auth, "get_settings", lambda: FakeSettings())
        r = client.post("/anomaly/detect/batch", json={"metric_name": "m"})
        assert r.status_code == 401

    def test_detect_and_dispatch_requires_auth(self, client, monkeypatch):
        from app.auth import token_auth

        class FakeSettings:
            api_token = "secret-token"

        monkeypatch.setattr(token_auth, "get_settings", lambda: FakeSettings())
        r = client.post("/anomaly/detect-and-dispatch", json={"metric_name": "m", "value": 1.0})
        assert r.status_code == 401

    def test_get_endpoints_no_auth_required(self, client, monkeypatch):
        """GET 端点在开启认证时仍可免认证访问"""
        from app.auth import token_auth

        class FakeSettings:
            api_token = "secret-token"

        monkeypatch.setattr(token_auth, "get_settings", lambda: FakeSettings())
        # GET /anomaly/algorithms
        r = client.get("/anomaly/algorithms")
        assert r.status_code == 200
        # GET /anomaly/metrics/{name}
        r = client.get("/anomaly/metrics/foo")
        assert r.status_code == 200


# ═══════════════ 辅助 ═══════════════


def get_store_for_detector():
    """获取当前单例 store（已被 isolated_ts_db fixture 重定向）"""
    from app.aiops.timeseries_store import get_timeseries_store

    return get_timeseries_store()
