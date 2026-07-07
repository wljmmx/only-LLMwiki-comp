"""pytest 全局 fixture

session 级初始化：
- 确保所有业务 DB schema 已创建（CREATE TABLE IF NOT EXISTS，幂等）
  避免 /health 健康检查因 DB 文件不存在而降级（CI 干净环境首次运行）

注：FastAPI TestClient 模块级创建（client = TestClient(app)）不会触发 lifespan，
故需在 conftest 中显式初始化 DB schema。
"""
from __future__ import annotations

import os

# 确保测试期间关闭认证
os.environ.setdefault("OPSKG_API_TOKEN", "")

import pytest


@pytest.fixture(scope="session", autouse=True)
def _init_dbs() -> None:
    """session 级自动 fixture：首次启动初始化所有业务 DB schema"""
    from app.ha import ensure_all_dbs_initialized

    ensure_all_dbs_initialized()
