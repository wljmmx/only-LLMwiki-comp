"""API 限流中间件测试（slowapi）

覆盖：
- 全局限流：连续 61 次请求，第 61 次返回 429
- /health 不受限流
- /setup/status 不受限流
- 429 响应含 retry_after 字段
- 不同 IP 独立计数（用 X-Forwarded-For 模拟）
- rate_limit_enabled=False 时全开放

测试隔离：每个测试前重置 limiter 的内存存储，避免计数串扰。
"""
from __future__ import annotations

import os

# 确保测试期间关闭认证（dev 模式）
os.environ.setdefault("OPSKG_API_TOKEN", "")

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def reset_limiter():
    """每个测试前重置 limiter 存储，确保计数从 0 开始

    全局 conftest 设置 OPSKG_RATE_LIMIT_ENABLED=false，故 limiter 默认禁用。
    rate_limit 测试需要启用，测试后恢复禁用以免影响其他测试。

    注意：必须先触发 app.main 导入（configure_rate_limit 在此时运行），
    之后再设 limiter.enabled = True，否则 configure_rate_limit 会覆盖。
    """
    # 触发 app.main 导入（如果尚未导入），使 configure_rate_limit 执行完毕
    from app.main import app  # noqa: F401
    from app.middleware.rate_limit import limiter

    limiter.reset()
    # 启用限流（在 configure_rate_limit 之后设置，不会被覆盖）
    limiter.enabled = True
    yield
    limiter.reset()
    # 恢复禁用状态（与 conftest 的 OPSKG_RATE_LIMIT_ENABLED=false 一致）
    limiter.enabled = False


@pytest.fixture
def client():
    """TestClient（复用全局 app）"""
    from app.main import app

    return TestClient(app)


# ────────── 全局限流 ──────────


class TestRateLimit:
    def test_global_limit_blocks_61st_request(self, client):
        """全局限流：连续 61 次请求，第 61 次返回 429

        默认 60 req/min/IP。前 60 次正常，第 61 次触发 429。
        用 /documents（读端点）触发，避免写副作用。
        """
        # 前 60 次应正常（200）
        for i in range(60):
            r = client.get("/documents")
            assert r.status_code == 200, f"第 {i + 1} 次请求意外失败: {r.status_code}"
        # 第 61 次应被限流（429）
        r = client.get("/documents")
        assert r.status_code == 429, f"第 61 次应返回 429，实际 {r.status_code}"

    def test_health_not_rate_limited(self, client):
        """健康检查端点不受限流影响"""
        # 连续请求远超 60 次也应全部 200
        for _ in range(80):
            r = client.get("/health")
            assert r.status_code == 200

    def test_setup_status_not_rate_limited(self, client):
        """/setup/status 不受限流（引导流程需可访问）"""
        for _ in range(80):
            r = client.get("/setup/status")
            # setup/status 返回 200（即使配置未完成也是 200）
            assert r.status_code == 200

    def test_429_response_has_retry_after(self, client):
        """429 响应含 retry_after 字段（JSON body + header）"""
        # 触发限流
        for _ in range(60):
            client.get("/documents")
        r = client.get("/documents")
        assert r.status_code == 429
        body = r.json()
        assert "retry_after" in body, "429 响应应含 retry_after 字段"
        assert isinstance(body["retry_after"], int)
        assert body["retry_after"] > 0
        assert body["error"] == "rate_limit_exceeded"
        # 响应头也应含 Retry-After
        assert "retry-after" in {k.lower() for k in r.headers.keys()}

    def test_different_ips_independent_count(self, client):
        """不同 IP 独立计数（用 X-Forwarded-For 模拟）"""
        # IP A 用满 60 次
        for _ in range(60):
            r = client.get("/documents", headers={"X-Forwarded-For": "10.0.0.1"})
            assert r.status_code == 200
        # IP A 第 61 次应 429
        r = client.get("/documents", headers={"X-Forwarded-For": "10.0.0.1"})
        assert r.status_code == 429
        # IP B 第 1 次应正常（独立计数）
        r = client.get("/documents", headers={"X-Forwarded-For": "10.0.0.2"})
        assert r.status_code == 200

    def test_disabled_rate_limit_allows_all(self, client, monkeypatch):
        """rate_limit_enabled=False 时全开放（不限流）"""
        from app.middleware.rate_limit import limiter

        limiter.enabled = False
        # 远超 60 次也应全部 200
        for _ in range(100):
            r = client.get("/documents")
            assert r.status_code == 200
