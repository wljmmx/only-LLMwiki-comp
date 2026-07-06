"""HA 高可用支持（Sprint 10+ P3-4）

提供：
- 实例 ID 标识（OPSKG_INSTANCE_ID 或 hostname+pid）
- /health 增强：返回 instance_id + 关键依赖状态
- /ready readiness 探针：检查 DB 连接、关键路径可访问性
- 实例元数据暴露给 Prometheus 指标

设计原则：
- 单实例默认行为不变（向后兼容）
- 多实例需 OPSKG_DEPLOYMENT_MODE=replicated + 共享存储
- 所有 DB 操作容错，单 DB 不可达不影响其他
"""

from __future__ import annotations

import os
import socket
import sqlite3
from datetime import datetime, timezone
from typing import Any

import structlog

from app.config import get_settings

logger = structlog.get_logger()


def get_instance_id() -> str:
    """获取实例 ID

    优先级：
    1. OPSKG_INSTANCE_ID 环境变量
    2. settings.instance_id
    3. hostname + pid 自动生成
    """
    env_id = os.getenv("OPSKG_INSTANCE_ID", "").strip()
    if env_id:
        return env_id
    settings = get_settings()
    if settings.instance_id:
        return settings.instance_id
    try:
        host = socket.gethostname() or "unknown"
    except Exception:  # noqa: BLE001
        host = "unknown"
    return f"{host}-{os.getpid()}"


def get_startup_time() -> str:
    """进程启动时间（ISO8601 UTC），用于计算 uptime"""
    return _STARTUP_TIME


_STARTUP_TIME = datetime.now(timezone.utc).isoformat()


def check_db_connection(db_path: str) -> dict[str, Any]:
    """检查 SQLite 数据库可访问性

    Returns:
        {"ok": bool, "path": str, "error": str | None, "table_count": int}
    """
    import os as _os

    if not _os.path.exists(db_path):
        return {
            "ok": False,
            "path": db_path,
            "error": "file not found",
            "table_count": 0,
        }
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        cur = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        )
        n = cur.fetchone()[0]
        conn.close()
        return {"ok": True, "path": db_path, "error": None, "table_count": n}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": db_path, "error": str(e), "table_count": 0}


def collect_health() -> dict[str, Any]:
    """收集健康检查信息

    Returns:
        {
            "status": "ok" | "degraded" | "down",
            "instance_id": str,
            "deployment_mode": str,
            "uptime_seconds": float,
            "started_at": str,
            "dbs": {name: {ok, path, error, table_count}},
            "background_workers": {webhook_worker},
        }
    """
    from app.aiops.event_correlator import DB_PATH as EV_DB
    from app.search.search_engine import DB_PATH as SEARCH_DB
    from app.storage.document_store import DB_PATH as DOC_DB
    from app.storage.version_control import DB_PATH as VC_DB
    from app.storage.webhook_store import DB_PATH as WH_DB
    from app.templates.manager import DB_PATH as TPL_DB

    settings = get_settings()
    dbs = {
        "documents": check_db_connection(str(DOC_DB)),
        "versions": check_db_connection(str(VC_DB)),
        "webhooks": check_db_connection(str(WH_DB)),
        "events": check_db_connection(str(EV_DB)),
        "search": check_db_connection(str(SEARCH_DB)),
        "templates": check_db_connection(str(TPL_DB)),
    }
    all_ok = all(d["ok"] for d in dbs.values())
    any_ok = any(d["ok"] for d in dbs.values())

    # 后台 worker 状态
    bg = {}
    try:
        from app.webhooks import get_webhook_manager

        mgr = get_webhook_manager()
        bg["webhook_worker"] = "running" if mgr._bg_task is not None else "stopped"
    except Exception:  # noqa: BLE001
        bg["webhook_worker"] = "unknown"

    # 计算 uptime
    try:
        started = datetime.fromisoformat(_STARTUP_TIME)
        uptime = (datetime.now(timezone.utc) - started).total_seconds()
    except Exception:  # noqa: BLE001
        uptime = 0.0

    status = "ok" if all_ok else ("degraded" if any_ok else "down")
    return {
        "status": status,
        "instance_id": get_instance_id(),
        "deployment_mode": settings.deployment_mode,
        "uptime_seconds": round(uptime, 2),
        "started_at": _STARTUP_TIME,
        "dbs": dbs,
        "background_workers": bg,
    }


def collect_readiness() -> dict[str, Any]:
    """readiness 探针：检查服务是否准备好接收流量

    比 liveness 更严格：所有 DB 必须 ok，关键 worker 必须运行
    """
    health = collect_health()
    ready = (
        health["status"] == "ok"
        and health["background_workers"].get("webhook_worker") == "running"
    )
    return {
        "ready": ready,
        "instance_id": health["instance_id"],
        "status": health["status"],
        "checks": {
            "all_dbs_ok": health["status"] == "ok",
            "webhook_worker_running": health["background_workers"].get(
                "webhook_worker"
            )
            == "running",
        },
    }


__all__ = [
    "check_db_connection",
    "collect_health",
    "collect_readiness",
    "get_instance_id",
    "get_startup_time",
]
