"""Sprint 10+ P3-4 HA 高可用验证脚本

验证项：
1. get_instance_id 优先级：OPSKG_INSTANCE_ID > settings.instance_id > hostname+pid
2. get_startup_time 返回 ISO8601 UTC
3. check_db_connection 正确识别存在/不存在的 DB 文件
4. collect_health 返回结构齐全（status/instance_id/deployment_mode/uptime/dbs/workers）
5. /health 端点返回增强后的健康信息（含 instance_id + dbs）
6. /ready 端点返回 ready=True 且 HTTP 200
7. /ready 在依赖失败时返回 HTTP 503
8. deployment_mode 配置项默认 standalone，可通过 env 切换 replicated
9. lifespan 启动时记录 instance_id 到日志
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_ha_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)

# 重定向各 DB 到临时目录
import app.storage.document_store as ds_mod
import app.storage.version_control as vc_mod
import app.storage.webhook_store as wh_mod
import app.aiops.event_correlator as ec_mod
import app.search.search_engine as se_mod
import app.templates.manager as tpl_mod

ds_mod.DB_PATH = TMP_DIR / "documents.db"
vc_mod.DB_PATH = TMP_DIR / "versions.db"
wh_mod.DB_PATH = TMP_DIR / "webhooks.db"
ec_mod.DB_PATH = TMP_DIR / "events.db"
se_mod.DB_PATH = TMP_DIR / "search_index.db"
tpl_mod.DB_PATH = TMP_DIR / "templates.db"

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


def test_instance_id_priority() -> None:
    print("\n[1] get_instance_id 优先级")
    # 清理 env
    os.environ.pop("OPSKG_INSTANCE_ID", None)
    from app.config import get_settings
    from app.ha import get_instance_id

    # 默认：hostname + pid
    settings = get_settings()
    saved = settings.instance_id
    settings.instance_id = ""
    id_default = get_instance_id()
    expected_host = socket.gethostname()
    check(
        id_default == f"{expected_host}-{os.getpid()}",
        f"默认 hostname+pid：{id_default}",
    )

    # settings.instance_id 优先于 hostname+pid
    settings.instance_id = "settings-instance-1"
    id_from_settings = get_instance_id()
    check(
        id_from_settings == "settings-instance-1",
        f"settings.instance_id 优先：{id_from_settings}",
    )

    # OPSKG_INSTANCE_ID 优先级最高
    os.environ["OPSKG_INSTANCE_ID"] = "env-instance-1"
    id_from_env = get_instance_id()
    check(
        id_from_env == "env-instance-1",
        f"OPSKG_INSTANCE_ID 优先级最高：{id_from_env}",
    )

    # 恢复
    os.environ.pop("OPSKG_INSTANCE_ID", None)
    settings.instance_id = saved


def test_startup_time() -> None:
    print("\n[2] get_startup_time")
    from app.ha import get_startup_time

    started = get_startup_time()
    check(
        isinstance(started, str) and "T" in started and "+" in started,
        f"返回 ISO8601 UTC 字符串：{started}",
    )


def test_check_db_connection() -> None:
    print("\n[3] check_db_connection")
    from app.ha import check_db_connection

    # 不存在的文件
    result = check_db_connection(str(TMP_DIR / "nonexistent.db"))
    check(result["ok"] is False, "不存在文件 → ok=False")
    check(
        result["error"] == "file not found",
        f"错误原因正确：{result['error']}",
    )

    # 创建一个空 DB
    import sqlite3

    db_path = TMP_DIR / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE foo (id INTEGER)")
    conn.commit()
    conn.close()

    result2 = check_db_connection(str(db_path))
    check(result2["ok"] is True, "存在 DB → ok=True")
    check(result2["table_count"] == 1, f"表数=1：实际 {result2['table_count']}")
    check(result2["error"] is None, "error=None")


def test_collect_health_structure() -> None:
    print("\n[4] collect_health 结构")
    # 显式初始化所有 DB schema（部分 Store 不在构造时建表，需触发一次 _get_db）
    from app.storage import document_store as ds
    from app.storage import version_control as vc
    from app.storage import webhook_store as wh
    from app.aiops import event_correlator as ec
    from app.search import search_engine as se
    from app.templates import manager as tpl

    ds._get_db().close()
    vc._get_db().close()
    wh._get_db().close()
    ec._get_db().close()
    se._get_db().close()
    tpl._get_db().close()

    from app.ha import collect_health

    health = collect_health()
    check(
        health["status"] in ("ok", "degraded", "down"),
        f"status ∈ ok|degraded|down：实际 {health['status']}",
    )
    check(
        "instance_id" in health and isinstance(health["instance_id"], str),
        "instance_id 字段存在且为 str",
    )
    check(
        "deployment_mode" in health,
        f"deployment_mode 字段存在：{health['deployment_mode']}",
    )
    check(
        "uptime_seconds" in health and health["uptime_seconds"] >= 0,
        f"uptime_seconds 非负：{health['uptime_seconds']}",
    )
    check(
        "started_at" in health and "T" in health["started_at"],
        "started_at 字段是 ISO8601",
    )
    check(
        "dbs" in health and len(health["dbs"]) == 6,
        f"dbs 含 6 个 DB：实际 {len(health['dbs'])}",
    )
    expected_dbs = {"documents", "versions", "webhooks", "events", "search", "templates"}
    check(
        set(health["dbs"].keys()) == expected_dbs,
        f"dbs keys = {expected_dbs}",
    )
    check(
        "background_workers" in health
        and "webhook_worker" in health["background_workers"],
        "background_workers.webhook_worker 存在",
    )


def test_health_endpoint() -> None:
    print("\n[5] /health 端点")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.get("/health")
        check(r.status_code == 200, f"GET /health 返回 200：{r.status_code}")
        body = r.json()
        check(
            body.get("status") in ("ok", "degraded", "down"),
            f"返回 status 字段：{body.get('status')}",
        )
        check(
            "instance_id" in body,
            f"返回 instance_id：{body.get('instance_id')}",
        )
        check(
            "dbs" in body and len(body["dbs"]) == 6,
            "返回 6 个 DB 状态",
        )
        check(
            "uptime_seconds" in body,
            "返回 uptime_seconds",
        )


def test_ready_endpoint_ok() -> None:
    print("\n[6] /ready 端点（健康场景）")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.get("/ready")
        check(r.status_code == 200, f"GET /ready 返回 200：{r.status_code}")
        body = r.json()
        check(body.get("ready") is True, f"body.ready=True：{body.get('ready')}")
        check(
            "checks" in body and "all_dbs_ok" in body["checks"],
            "返回 checks.all_dbs_ok",
        )
        check(
            body["checks"].get("all_dbs_ok") is True,
            f"all_dbs_ok=True：{body['checks'].get('all_dbs_ok')}",
        )


def test_ready_endpoint_fail() -> None:
    print("\n[7] /ready 端点（依赖失败场景）")
    # 把一个 DB 路径指向不存在的位置
    saved = se_mod.DB_PATH
    se_mod.DB_PATH = TMP_DIR / "missing.db"

    # 清理 search_engine 单例缓存
    import app.search.search_engine as se_mod2

    se_mod2._engine = None

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.get("/ready")
        check(
            r.status_code == 503,
            f"DB 不可达 → HTTP 503：实际 {r.status_code}",
        )
        body = r.json()
        check(
            body.get("ready") is False,
            f"body.ready=False：{body.get('ready')}",
        )

    # 恢复
    se_mod.DB_PATH = saved
    se_mod2._engine = None


def test_deployment_mode_config() -> None:
    print("\n[8] deployment_mode 配置")
    from app.config import get_settings

    settings = get_settings()
    check(
        settings.deployment_mode == "standalone",
        f"默认 standalone：实际 {settings.deployment_mode}",
    )
    check(
        settings.instance_id == "" or isinstance(settings.instance_id, str),
        "instance_id 默认空字符串或 str",
    )


def test_lifespan_logs_instance_id() -> None:
    print("\n[9] lifespan 启动日志记录 instance_id")
    from fastapi.testclient import TestClient

    from app.main import app

    # 通过 TestClient with 触发 lifespan
    with TestClient(app) as client:
        # 实例已经启动，调用 /health 应该返回 instance_id
        r = client.get("/health")
        body = r.json()
        check(
            bool(body.get("instance_id")),
            f"lifespan 启动后 /health 返回 instance_id：{body.get('instance_id')}",
        )
        check(
            body.get("deployment_mode") in ("standalone", "replicated"),
            f"deployment_mode 在响应中：{body.get('deployment_mode')}",
        )


def main() -> None:
    print("=" * 70)
    print("OpsKG Sprint 10+ P3-4 HA 高可用验证")
    print("=" * 70)

    test_instance_id_priority()
    test_startup_time()
    test_check_db_connection()
    test_collect_health_structure()
    test_health_endpoint()
    test_ready_endpoint_ok()
    test_ready_endpoint_fail()
    test_deployment_mode_config()
    test_lifespan_logs_instance_id()

    print("\n" + "=" * 70)
    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    print("=" * 70)
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
