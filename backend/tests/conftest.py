"""pytest 全局 fixture

session 级初始化：
- 确保所有业务 DB schema 已创建（CREATE TABLE IF NOT EXISTS，幂等）
  避免 /health 健康检查因 DB 文件不存在而降级（CI 干净环境首次运行）

注：FastAPI TestClient 模块级创建（client = TestClient(app)）不会触发 lifespan，
故需在 conftest 中显式初始化 DB schema。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

# 确保测试期间关闭认证
os.environ.setdefault("OPSKG_API_TOKEN", "")
# 测试期间默认关闭限流（避免全量回归时跨测试计数串扰）
# test_rate_limit.py 的 fixture 会按需重新启用
os.environ.setdefault("OPSKG_RATE_LIMIT_ENABLED", "false")

import pytest


@pytest.fixture(scope="session", autouse=True)
def _init_dbs() -> None:
    """session 级自动 fixture：首次启动初始化所有业务 DB schema"""
    from app.ha import ensure_all_dbs_initialized

    ensure_all_dbs_initialized()


# ────────── DocumentStore / VersionControl 共享内存 DB fixtures ──────────


def _make_memory_db() -> sqlite3.Connection:
    """创建内存 SQLite 连接，启用 WAL 和 row_factory

    注意：不使用 cache=shared，避免跨测试数据泄漏。
    每个测试的 fixture 独立创建连接，通过 monkeypatch _get_db 返回同一连接。
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@pytest.fixture
def temp_db_document_store(monkeypatch, tmp_path) -> sqlite3.Connection:
    """为 DocumentStore 测试创建临时 SQLite 内存数据库 + 文件存储目录

    将 document_store 模块的 _get_db 替换为返回共享内存连接，
    将 UPLOADS_DIR 替换为临时目录，_ensure_dirs 替换为 no-op。
    """
    conn = _make_memory_db()
    from app.storage import document_store as ds_module

    ds_module._init_schema(conn)

    def _patched_get_db() -> sqlite3.Connection:
        return conn

    monkeypatch.setattr(ds_module, "_get_db", _patched_get_db)
    monkeypatch.setattr(ds_module, "_ensure_dirs", lambda: None)
    monkeypatch.setattr(ds_module, "UPLOADS_DIR", tmp_path)

    return conn


@pytest.fixture
def temp_db_version_control(monkeypatch) -> sqlite3.Connection:
    """为 VersionControl 测试创建临时 SQLite 内存数据库

    将 version_control 模块的 _get_db 替换为返回共享内存连接。
    """
    conn = _make_memory_db()
    from app.storage import version_control as vc_module

    vc_module._init_schema(conn)

    def _patched_get_db() -> sqlite3.Connection:
        return conn

    monkeypatch.setattr(vc_module, "_get_db", _patched_get_db)

    return conn


@pytest.fixture
def sample_document() -> dict[str, Any]:
    """创建示例文档数据"""
    return {
        "filename": "test_nginx_guide.md",
        "content": b"# Nginx Deployment Guide\n\nThis guide covers Nginx deployment.\n\n## Configuration\n\n```nginx\nserver {\n    listen 80;\n    server_name example.com;\n}\n```\n\n## Troubleshooting\n\nCommon issues:\n- 502 Bad Gateway\n- 504 Gateway Timeout\n",
        "fmt": "markdown",
    }
