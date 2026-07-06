"""Sprint 10+ 可观测性（Prometheus 指标）验证脚本

验证项：
1. /metrics 端点可访问，返回 Prometheus 文本格式
2. HTTP 中间件自动采集：请求计数 + 延迟直方图 + in-progress gauge
3. 业务指标定义齐全（documents/wiki/incidents/webhook/llm/search）
4. record_business_metric API 正确更新 Gauge/Counter
5. 业务指标采集器 collector 能从存储层拉取数据
6. 中间件使用 path_template（如 /documents/{doc_id}）避免高基数
7. /metrics 端点本身不被计数（避免自引用）
8. METRICS_ENABLED=0 时禁用所有采集
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_obs_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

# 重定向各 db
import app.storage.document_store as ds_mod
import app.storage.version_control as vc_mod
import app.storage.webhook_store as wh_mod
import app.aiops.event_correlator as ec_mod

ds_mod.DB_PATH = TMP_DIR / "documents.db"
vc_mod.DB_PATH = TMP_DIR / "versions.db"
wh_mod.DB_PATH = TMP_DIR / "webhooks.db"
ec_mod.DB_PATH = TMP_DIR / "events.db"

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


def test_metrics_endpoint() -> None:
    print("\n[1] /metrics 端点可访问")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.get("/metrics")
    check(r.status_code == 200, f"GET /metrics 返回 200：实际 {r.status_code}")
    ct = r.headers.get("content-type", "")
    check(
        "text/plain" in ct and "version=1.0.0" in ct,
        f"Content-Type 是 Prometheus 格式：{ct}",
    )
    text = r.text
    check(
        "# HELP opskg_http_requests_total" in text,
        "响应包含 opskg_http_requests_total HELP",
    )
    check(
        "# TYPE opskg_http_requests_total counter" in text,
        "响应包含 opskg_http_requests_total TYPE counter",
    )


def test_http_middleware_collection() -> None:
    print("\n[2] HTTP 中间件自动采集")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    # 发起几个请求
    client.get("/health")
    client.get("/documents/stats")
    client.get("/search/stats")

    r = client.get("/metrics")
    text = r.text
    # 应包含 /health 路径的计数
    has_health = any(
        'path_template="/health"' in line and 'status="200"' in line
        for line in text.splitlines()
    )
    check(has_health, "metrics 包含 /health 200 的计数")

    # 延迟直方图
    has_duration = any(
        "opskg_http_request_duration_seconds_bucket" in line
        and 'path_template="/health"' in line
        for line in text.splitlines()
    )
    check(has_duration, "metrics 包含 /health 的延迟直方图")

    # in_progress gauge 应为 0（请求已结束）
    has_in_progress = "opskg_http_requests_in_progress" in text
    check(has_in_progress, "metrics 包含 in_progress gauge")


def test_path_template_low_cardinality() -> None:
    print("\n[3] 路径模板（低基数）")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    # 访问不存在的 doc_id，路径模板应是 /documents/{doc_id} 而非 /documents/some-id
    client.get("/documents/nonexistent-doc-id")
    client.get("/documents/another-doc-id")

    r = client.get("/metrics")
    text = r.text
    # 应该出现 path_template="/documents/{doc_id}" 而非具体 doc_id
    has_template = any(
        'path_template="/documents/{doc_id}"' in line for line in text.splitlines()
    )
    check(has_template, "metrics 使用 /documents/{doc_id} 模板（低基数）")
    # 不应出现具体 doc_id 作为 label
    no_concrete = not any(
        'path_template="/documents/nonexistent-doc-id"' in line
        for line in text.splitlines()
    )
    check(no_concrete, "metrics 不使用具体 doc_id 作为 label")


def test_business_metrics_definitions() -> None:
    print("\n[4] 业务指标定义齐全")
    from app.observability import business_metrics

    required = {
        "documents_total",
        "wiki_pages_total",
        "incidents_open",
        "incidents_total",
        "webhook_subscriptions",
        "webhook_deliveries_total",
        "llm_calls_total",
        "llm_call_duration",
        "search_queries_total",
        "documents_uploaded_total",
        "wiki_published_total",
        "incidents_created_total",
        "errors_total",
    }
    actual = set(business_metrics.keys())
    missing = required - actual
    check(
        not missing,
        f"必需业务指标齐全：缺 {missing}" if missing else "13 个业务指标全部定义",
    )


def test_record_business_metric() -> None:
    print("\n[5] record_business_metric API")
    from fastapi.testclient import TestClient

    from app.main import app
    from app.observability import record_business_metric

    # 重置后调用
    record_business_metric("errors_total", 2.0, type="test")
    record_business_metric("errors_total", 1.0, type="another")

    client = TestClient(app)
    r = client.get("/metrics")
    text = r.text
    has_test = any(
        'opskg_errors_total{type="test"}' in line and " 2.0" in line
        for line in text.splitlines()
    )
    check(has_test, "Counter 累加正确（type=test 累计 2.0）")
    has_another = any(
        'opskg_errors_total{type="another"}' in line and " 1.0" in line
        for line in text.splitlines()
    )
    check(has_another, "Counter 多标签正确（type=another 累计 1.0）")

    # 未知指标名应不抛异常
    try:
        record_business_metric("nonexistent_metric", 1.0)
        check(True, "未知指标名不抛异常")
    except Exception as e:
        check(False, f"未知指标名抛异常：{e}")


async def test_business_collector() -> None:
    print("\n[6] 业务指标采集器 collector")
    from app.observability.collector import collect_business_metrics
    from app.storage import get_document_store, get_webhook_store

    # 准备数据
    store = get_document_store()
    store.save("test1.md", b"# Test 1", "markdown")
    store.save("test2.txt", b"hello", "txt")

    wh_store = get_webhook_store()
    wh_store.create_subscription(
        url="https://example.com/h", events=["*"]
    )

    await collect_business_metrics()

    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.get("/metrics")
    text = r.text
    # documents_total 应 >= 2
    has_docs = any(
        "opskg_documents_total" in line and not line.startswith("#")
        and float(line.split()[-1]) >= 2.0
        for line in text.splitlines()
    )
    check(has_docs, "documents_total >= 2（采集器从存储层拉取）")
    # webhook_subscriptions{active="true"} 应 >= 1
    has_wh = any(
        'opskg_webhook_subscriptions_total{active="true"}' in line
        and float(line.split()[-1]) >= 1.0
        for line in text.splitlines()
    )
    check(has_wh, "webhook_subscriptions{active=true} >= 1")


def test_metrics_endpoint_not_counted() -> None:
    print("\n[7] /metrics 端点自身不被计数")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    # 多次访问 /metrics
    for _ in range(3):
        client.get("/metrics")
    r = client.get("/metrics")
    text = r.text
    # 不应出现 path_template="/metrics" 的计数
    has_metrics_count = any(
        'path_template="/metrics"' in line for line in text.splitlines()
        if line.startswith("opskg_http_requests_total")
    )
    check(not has_metrics_count, "/metrics 端点不被自身计数")


def test_metrics_disabled() -> None:
    print("\n[8] METRICS_ENABLED=0 禁用采集")
    # 通过设置环境变量后重新 import 验证（实际生产中应在启动前设置）
    import importlib

    os.environ["OPSKG_METRICS_ENABLED"] = "0"
    import app.observability.metrics as metrics_mod

    importlib.reload(metrics_mod)
    check(
        metrics_mod.METRICS_ENABLED is False,
        "OPSKG_METRICS_ENABLED=0 后 METRICS_ENABLED 为 False",
    )
    # 恢复
    os.environ["OPSKG_METRICS_ENABLED"] = "1"
    importlib.reload(metrics_mod)
    check(
        metrics_mod.METRICS_ENABLED is True,
        "恢复后 METRICS_ENABLED 为 True",
    )


async def main() -> None:
    print("=" * 60)
    print("Sprint 10+ 可观测性（Prometheus 指标）验证")
    print("=" * 60)
    test_metrics_endpoint()
    test_http_middleware_collection()
    test_path_template_low_cardinality()
    test_business_metrics_definitions()
    test_record_business_metric()
    await test_business_collector()
    test_metrics_endpoint_not_counted()
    test_metrics_disabled()

    print("\n" + "=" * 60)
    print(f"结果：✓ {PASS} 通过  ✗ {FAIL} 失败")
    print("=" * 60)
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
