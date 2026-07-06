"""S15-3 时序异常检测模块验证脚本

验证项：
1. 文件存在性
2. 表结构（metrics_timeseries + 索引）
3. TimeseriesStore CRUD（record / query / cleanup_old / get_latest_values）
4. Z-score 算法正确性（正常值 / 异常值 / 边界条件）
5. EWMA 算法正确性（正常值 / 异常值 / alpha 影响）
6. anomaly → event 桥接（detect_and_dispatch + severity 分级）
7. API 端点（record / query / cleanup / detect / detect/batch / detect-and-dispatch / algorithms）
8. 单元测试全通过
9. 全量后端测试不回归
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

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_s15_3_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)
# 关闭认证便于 API 测试
os.environ.setdefault("OPSKG_API_TOKEN", "")

# 重定向时序 DB 到 tmp
import app.aiops.timeseries_store as ts_mod

ts_mod.DB_PATH = TMP_DIR / "timeseries.db"

# 重置单例
import app.aiops.anomaly_detector as ad_mod

ad_mod._detector = None
ts_mod._store = None

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
    """切换到独立 DB 文件，避免数据残留"""
    ts_mod.DB_PATH = TMP_DIR / f"timeseries_{name}.db"
    ts_mod._store = None
    ad_mod._detector = None


def _iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _seed(store, metric_name, values, base_offset=-1000, labels=None):
    for i, v in enumerate(values):
        store.record(metric_name, v, labels=labels, timestamp=_iso(base_offset + i))


# ═══════════════ 1. 文件存在性 ═══════════════


def test_files_exist() -> None:
    print("\n[1] 文件存在性")
    files = [
        "backend/app/aiops/timeseries_store.py",
        "backend/app/aiops/anomaly_detector.py",
        "backend/app/routers/anomaly_router.py",
        "backend/tests/test_anomaly_detector.py",
    ]
    base = Path(__file__).parent.parent
    for f in files:
        check((base / f).exists(), f"{f} 存在")


# ═══════════════ 2. 表结构 ═══════════════


def test_schema() -> None:
    print("\n[2] 表结构 / 索引")
    from app.aiops.timeseries_store import _get_db

    conn = _get_db()
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        check("metrics_timeseries" in tables, "metrics_timeseries 表已创建")

        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(metrics_timeseries)").fetchall()
        }
        for col in ("id", "metric_name", "timestamp", "value", "labels", "created_at"):
            check(col in cols, f"metrics_timeseries 含列 {col}")

        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        check("idx_ts_metric_time" in idx, "索引 idx_ts_metric_time 存在")
        check("idx_ts_labels" in idx, "索引 idx_ts_labels 存在")
    finally:
        conn.close()


# ═══════════════ 3. TimeseriesStore CRUD ═══════════════


def test_store_crud() -> None:
    print("\n[3] TimeseriesStore CRUD")
    fresh_db("store_crud")
    from app.aiops.timeseries_store import TimeseriesStore

    store = TimeseriesStore()

    # record
    pt = store.record("cpu_usage", 75.5, labels={"host": "prod-01"})
    check(pt["id"] is not None, f"record 返回 id：{pt['id']}")
    check(pt["value"] == 75.5, "record value 正确")
    check(pt["labels"] == {"host": "prod-01"}, "record labels 正确")

    # record with explicit timestamp
    ts = "2026-01-01T00:00:00+00:00"
    pt2 = store.record("m", 1.0, timestamp=ts)
    check(pt2["timestamp"] == ts, "record 显式 timestamp 正确")

    # query
    _seed(store, "latency", [10.0, 20.0, 30.0])
    pts = store.query("latency")
    check(len(pts) == 3, f"query 返回 3 条：实际 {len(pts)}")
    check([p["value"] for p in pts] == [10.0, 20.0, 30.0], "query 按 timestamp 升序")

    # query by labels
    store.record("m", 1.0, labels={"host": "a"}, timestamp=_iso(-10))
    store.record("m", 2.0, labels={"host": "b"}, timestamp=_iso(-9))
    store.record("m", 3.0, labels={"host": "a"}, timestamp=_iso(-8))
    pts = store.query("m", labels={"host": "a"})
    check(len(pts) == 2, f"query labels 过滤返回 2 条：实际 {len(pts)}")

    # get_latest_values
    _seed(store, "latest", [1, 2, 3, 4, 5], base_offset=-100)
    vals = store.get_latest_values("latest", count=3)
    check(vals == [3.0, 4.0, 5.0], f"get_latest_values 最近 3 个（升序）：{vals}")

    # cleanup_old
    old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    store.record("old", 1.0, timestamp=old_ts)
    deleted = store.cleanup_old(days=30)
    check(deleted >= 1, f"cleanup_old 删除 >= 1：实际 {deleted}")
    check(store.query("old") == [], "cleanup_old 后旧数据已清空")


# ═══════════════ 4. Z-score 算法 ═══════════════


def test_zscore() -> None:
    print("\n[4] Z-score 算法正确性")
    fresh_db("zscore")
    from app.aiops.anomaly_detector import AnomalyDetector, get_anomaly_detector
    from app.aiops.timeseries_store import get_timeseries_store

    store = get_timeseries_store()
    detector = get_anomaly_detector()

    # 正常值：不告警
    _seed(store, "normal", [50, 51, 49, 50, 51, 50, 49, 50, 51, 50])
    r = detector.detect_zscore("normal", 50.5, window=10, threshold=3.0)
    check(r["is_anomaly"] is False, "正常值不告警")
    check(abs(r["score"]) < 3.0, f"正常值 |z| < 3：实际 {abs(r['score']):.2f}")
    check(r["window_size"] == 10, f"window_size = 10：实际 {r['window_size']}")

    # 异常值：告警
    _seed(store, "abnormal", [50, 50, 50, 50, 50, 50, 50, 50, 50, 50])
    r = detector.detect_zscore("abnormal", 100.0, window=10, threshold=3.0)
    check(r["is_anomaly"] is True, "异常值告警")
    check(r["score"] > 3.0, f"异常值 z > 3：实际 {r['score']:.2f}")
    check(r["mean"] == 50.0, f"mean = 50.0：实际 {r['mean']}")

    # 空数据
    r = detector.detect_zscore("empty", 100.0, window=10)
    check(r["is_anomaly"] is False, "空历史不告警")
    check(r["window_size"] == 0, "空历史 window_size = 0")

    # 单点数据
    store.record("single", 50.0, timestamp=_iso(-10))
    r = detector.detect_zscore("single", 100.0, window=10)
    check(r["is_anomaly"] is False, "单点历史不告警")
    check(r["window_size"] == 1, "单点历史 window_size = 1")

    # 极端值
    _seed(store, "extreme", [1, 2, 3, 2, 1, 2, 3, 2, 1, 2])
    r = detector.detect_zscore("extreme", 1e9, window=10, threshold=3.0)
    check(r["is_anomaly"] is True, "极端值告警")
    check(r["score"] > 5, f"极端值 z > 5（critical）：实际 {r['score']:.2f}")

    # 值与历史完全相同 → z=0
    _seed(store, "same", [50, 50, 50, 50, 50])
    r = detector.detect_zscore("same", 50.0, window=5)
    check(r["is_anomaly"] is False, "值与历史相同不告警")
    check(r["score"] == 0.0, "值与历史相同 z = 0")


# ═══════════════ 5. EWMA 算法 ═══════════════


def test_ewma() -> None:
    print("\n[5] EWMA 算法正确性")
    fresh_db("ewma")
    from app.aiops.anomaly_detector import get_anomaly_detector
    from app.aiops.timeseries_store import get_timeseries_store

    store = get_timeseries_store()
    detector = get_anomaly_detector()

    # 正常值
    _seed(store, "normal", [50, 51, 49, 50, 51, 50, 49, 50, 51, 50])
    r = detector.detect_ewma("normal", 50.5, alpha=0.3, window=10, threshold=3.0)
    check(r["is_anomaly"] is False, "EWMA 正常值不告警")
    check("ewma" in r, "EWMA 结果含 ewma 字段")

    # 异常值
    _seed(store, "abnormal", [50, 50, 50, 50, 50, 50, 50, 50, 50, 50])
    r = detector.detect_ewma("abnormal", 100.0, alpha=0.3, window=10, threshold=3.0)
    check(r["is_anomaly"] is True, "EWMA 异常值告警")
    check(r["score"] > 3.0, f"EWMA 异常值 z > 3：实际 {r['score']:.2f}")

    # alpha 影响
    _seed(store, "alpha", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    r_low = detector.detect_ewma("alpha", 8.0, alpha=0.1, window=10, threshold=3.0)
    r_high = detector.detect_ewma("alpha", 8.0, alpha=0.9, window=10, threshold=3.0)
    check(r_low["ewma"] != r_high["ewma"], "不同 alpha 下 ewma 值不同")
    check(1.0 <= r_low["ewma"] <= 10.0, f"低 alpha ewma 在历史范围内：{r_low['ewma']:.2f}")

    # 空数据
    r = detector.detect_ewma("empty", 100.0)
    check(r["is_anomaly"] is False, "EWMA 空历史不告警")
    check(r["window_size"] == 0, "EWMA 空历史 window_size = 0")


# ═══════════════ 6. anomaly → event 桥接 ═══════════════


def test_dispatch_bridge() -> None:
    print("\n[6] anomaly → event 桥接")
    fresh_db("dispatch")
    from app.aiops.anomaly_detector import get_anomaly_detector
    from app.aiops.timeseries_store import get_timeseries_store

    store = get_timeseries_store()
    detector = get_anomaly_detector()

    # mock dispatch_event
    import app.webhooks as wh_mod

    calls = []

    def fake_dispatch(evt, payload):
        calls.append((evt, payload))
        return 1

    orig = getattr(wh_mod, "dispatch_event", None)
    wh_mod.dispatch_event = fake_dispatch
    try:
        # 无异常不分发
        _seed(store, "normal", [50, 51, 49, 50, 51, 50, 49, 50, 51, 50])
        r = detector.detect_and_dispatch("normal", 50.5, algorithm="zscore")
        check(r["is_anomaly"] is False, "正常值不告警")
        check(r["dispatched"] == 0, "正常值不分发")
        check(len(calls) == 0, "正常值无 dispatch 调用")

        # 异常 → 分发
        calls.clear()
        _seed(store, "abnormal", [10, 10, 10, 10, 10])
        r = detector.detect_and_dispatch("abnormal", 100.0, algorithm="zscore")
        check(r["is_anomaly"] is True, "异常值告警")
        check(r["dispatched"] == 1, "异常值 dispatched = 1")
        check(len(calls) == 1, "异常值触发 1 次 dispatch")
        evt_type, payload = calls[0]
        check(evt_type == "event.ingested", f"事件类型 event.ingested：实际 {evt_type}")
        check(payload["metric_name"] == "abnormal", "payload 含 metric_name")
        check(payload["value"] == 100.0, "payload 含 value")
        check(payload["algorithm"] == "zscore", "payload 含 algorithm")
        check("severity" in payload, "payload 含 severity")
        check("timestamp" in payload, "payload 含 timestamp")

        # severity 分级：critical
        calls.clear()
        _seed(store, "critical", [10, 10, 10, 10, 10])
        r = detector.detect_and_dispatch("critical", 100.0, algorithm="zscore")
        check(r["severity"] == "critical", f"severity critical：实际 {r['severity']}")

        # labels 透传
        calls.clear()
        _seed(store, "labels", [10, 10, 10, 10, 10], labels={"host": "prod-01"})
        r = detector.detect_and_dispatch(
            "labels", 100.0, algorithm="zscore", labels={"host": "prod-01"}
        )
        check(calls[0][1]["labels"] == {"host": "prod-01"}, "payload labels 透传正确")

        # dispatch 异常不抛出
        calls.clear()
        def broken(*a, **k):
            raise RuntimeError("broken")
        wh_mod.dispatch_event = broken
        r = detector.detect_and_dispatch("abnormal", 100.0, algorithm="zscore")
        check(r["is_anomaly"] is True, "dispatch 异常时仍返回检测结果")
        check(r["dispatched"] == 0, "dispatch 异常时降级为 0")
    finally:
        if orig is not None:
            wh_mod.dispatch_event = orig


# ═══════════════ 7. API 端点 ═══════════════


def test_api_endpoints() -> None:
    print("\n[7] API 端点")
    fresh_db("api")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    # GET /anomaly/algorithms
    resp = client.get("/anomaly/algorithms")
    check(resp.status_code == 200, f"GET /anomaly/algorithms 200：实际 {resp.status_code}")
    data = resp.json()
    check(data["count"] >= 2, f"算法数 >= 2：实际 {data['count']}")
    names = [a["name"] for a in data["algorithms"]]
    check("zscore" in names, "含 zscore 算法")
    check("ewma" in names, "含 ewma 算法")

    # POST /anomaly/metrics/record
    resp = client.post(
        "/anomaly/metrics/record",
        json={"metric_name": "cpu", "value": 75.5, "labels": {"host": "prod-01"}},
    )
    check(resp.status_code == 200, f"POST /anomaly/metrics/record 200：实际 {resp.status_code}")
    check(resp.json()["value"] == 75.5, "record 返回 value 正确")

    # 批量记录用于后续检测
    for v in [50, 50, 50, 50, 50, 100]:
        client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": v})

    # GET /anomaly/metrics/{name}
    resp = client.get("/anomaly/metrics/cpu")
    check(resp.status_code == 200, f"GET /anomaly/metrics/cpu 200：实际 {resp.status_code}")
    check(resp.json()["count"] >= 1, "query 返回 >= 1")

    # GET /anomaly/metrics/{name} 空数据
    resp = client.get("/anomaly/metrics/nonexistent")
    check(resp.status_code == 200, "GET 不存在的 metric 返回 200")
    check(resp.json()["count"] == 0, "空 metric count = 0")

    # POST /anomaly/detect
    resp = client.post(
        "/anomaly/detect",
        json={"metric_name": "m", "value": 200.0, "algorithm": "zscore", "threshold": 3.0},
    )
    check(resp.status_code == 200, f"POST /anomaly/detect 200：实际 {resp.status_code}")
    check(resp.json()["is_anomaly"] is True, "detect 检测到异常")

    # POST /anomaly/detect 校验失败
    resp = client.post("/anomaly/detect", json={"value": 1.0})
    check(resp.status_code == 400, "缺 metric_name 返回 400")

    resp = client.post(
        "/anomaly/detect",
        json={"metric_name": "m", "value": 1.0, "algorithm": "unknown"},
    )
    check(resp.status_code == 400, "不支持算法返回 400")

    # POST /anomaly/detect/batch
    resp = client.post(
        "/anomaly/detect/batch",
        json={"metric_name": "m", "algorithm": "zscore", "window": 10, "threshold": 3.0},
    )
    check(resp.status_code == 200, f"POST /anomaly/detect/batch 200：实际 {resp.status_code}")
    bdata = resp.json()
    check(bdata["total"] == 6, f"batch total = 6：实际 {bdata['total']}")
    check(bdata["anomalies"] >= 1, f"batch anomalies >= 1：实际 {bdata['anomalies']}")

    # POST /anomaly/detect-and-dispatch
    # 先 mock dispatch 避免真实投递
    import app.webhooks as wh_mod

    wh_mod.dispatch_event = lambda evt, payload: 0
    resp = client.post(
        "/anomaly/detect-and-dispatch",
        json={"metric_name": "m", "value": 200.0, "algorithm": "zscore"},
    )
    check(resp.status_code == 200, f"POST /anomaly/detect-and-dispatch 200：实际 {resp.status_code}")
    ddata = resp.json()
    check(ddata["is_anomaly"] is True, "detect-and-dispatch 检测到异常")
    check("dispatched" in ddata, "detect-and-dispatch 含 dispatched")
    check("severity" in ddata, "detect-and-dispatch 含 severity")

    # DELETE /anomaly/metrics/cleanup
    resp = client.delete("/anomaly/metrics/cleanup", params={"days": 30})
    check(resp.status_code == 200, f"DELETE /anomaly/metrics/cleanup 200：实际 {resp.status_code}")
    check("deleted" in resp.json(), "cleanup 返回 deleted 字段")

    # 401 测试（开启认证）
    from app.auth import token_auth

    orig_get_settings = token_auth.get_settings

    class FakeSettings:
        api_token = "secret-token"

    token_auth.get_settings = lambda: FakeSettings()
    try:
        resp = client.post("/anomaly/metrics/record", json={"metric_name": "m", "value": 1.0})
        check(resp.status_code == 401, f"POST 认证关闭时返回 401：实际 {resp.status_code}")

        resp = client.delete("/anomaly/metrics/cleanup")
        check(resp.status_code == 401, f"DELETE 认证关闭时返回 401：实际 {resp.status_code}")

        # GET 仍可访问
        resp = client.get("/anomaly/algorithms")
        check(resp.status_code == 200, "GET /anomaly/algorithms 开启认证时仍 200")
    finally:
        token_auth.get_settings = orig_get_settings


# ═══════════════ 8. 单元测试 ═══════════════


def test_unit_tests() -> None:
    print("\n[8] 单元测试 tests/test_anomaly_detector.py")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_anomaly_detector.py", "-q", "--tb=short"],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
        env=env,
    )
    tail = result.stdout.strip().splitlines()[-3:] if result.stdout else []
    for line in tail:
        print(f"    {line}")
    check(result.returncode == 0, f"pytest 退出码 0：实际 {result.returncode}")
    if result.returncode != 0:
        for line in result.stdout.splitlines()[-20:]:
            print(f"    {line}")


# ═══════════════ 9. 全量回归 ═══════════════


def test_full_regression() -> None:
    print("\n[9] 全量后端测试不回归")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "tests/", "-q",
            "-k", "not test_word_parse and not test_excel_parse",
        ],
        cwd=str(BACKEND_DIR),
        capture_output=True,
        text=True,
        env=env,
    )
    tail = result.stdout.strip().splitlines()[-5:] if result.stdout else []
    for line in tail:
        print(f"    {line}")
    check(result.returncode == 0, f"全量回归退出码 0：实际 {result.returncode}")
    if result.returncode != 0:
        for line in result.stdout.splitlines()[-30:]:
            print(f"    {line}")


def main() -> None:
    print("=" * 60)
    print("S15-3 时序异常检测模块验证")
    print("=" * 60)
    test_files_exist()
    test_schema()
    test_store_crud()
    test_zscore()
    test_ewma()
    test_dispatch_bridge()
    test_api_endpoints()
    test_unit_tests()
    test_full_regression()

    print("\n" + "=" * 60)
    print(f"结果：✓ {PASS} 通过  ✗ {FAIL} 失败")
    print("=" * 60)
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
