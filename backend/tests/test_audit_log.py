"""审计日志中间件测试

覆盖：
- POST 触发审计（含 who/what/when/status/duration）
- GET 不触发审计
- /health 不触发审计
- 敏感字段脱敏（password 字段值不出现在日志中）
- 请求体截断（>200 字符截断）
- request_id 透传（X-Request-ID header）
- 列表 API /api/audit/logs 返回记录（admin 权限）
- audit_log_enabled=False 时不记录

测试隔离：每个测试将 audit DB 重定向到 tmp_path，并重置 AuditStore 单例。
templates DB 用唯一 slug 避免冲突，测试后清理。
"""
from __future__ import annotations

import os
import uuid

# 确保测试期间关闭认证（dev 模式）
os.environ.setdefault("OPSKG_API_TOKEN", "")

import pytest
from fastapi.testclient import TestClient


def _unique_slug(prefix: str = "audit") -> str:
    """生成唯一 slug，避免 templates DB 的 UNIQUE 约束冲突"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def isolated_audit_db(tmp_path, monkeypatch):
    """将审计 DB 重定向到 tmp_path，并重置单例"""
    import app.storage.audit_store as audit_store

    db_file = tmp_path / "audit.db"
    monkeypatch.setattr(audit_store, "DB_PATH", db_file)
    monkeypatch.setattr(audit_store, "_store", None)
    yield audit_store.get_audit_store


@pytest.fixture
def client(isolated_audit_db):
    """TestClient（复用全局 app，审计 DB 已隔离）"""
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


# ────────── 审计日志触发 ──────────


class TestAuditLog:
    def test_post_triggers_audit(self, client, isolated_audit_db):
        """POST 触发审计（含 who/what/when/status/duration）"""
        # 用 /templates 创建模板（POST 写操作），用唯一 slug 避免冲突
        slug = _unique_slug("audit-test")
        r = client.post(
            "/templates",
            params={"slug": slug, "name": "审计测试", "content": "# T"},
        )
        assert r.status_code == 200, f"POST /templates 失败: {r.status_code} {r.text}"
        # 查询审计日志
        store = isolated_audit_db()
        logs = store.list_audit_logs()
        # 至少有一条 POST /templates 记录
        post_logs = [l for l in logs if l["method"] == "POST" and "templates" in l["path"]]
        assert len(post_logs) > 0, "POST /templates 应产生审计记录"
        log = post_logs[0]
        # 验证字段完整性
        assert log["user"] == "anonymous"  # dev 模式
        assert log["method"] == "POST"
        assert "templates" in log["path"]
        assert log["status"] == 200
        assert log["duration_ms"] >= 0
        assert "timestamp" in log
        assert log["timestamp"]  # 非空

    def test_get_not_audited(self, client, isolated_audit_db):
        """GET 不触发审计"""
        r = client.get("/documents")
        assert r.status_code == 200
        store = isolated_audit_db()
        logs = store.list_audit_logs()
        # 不应有 GET 记录
        get_logs = [l for l in logs if l["method"] == "GET"]
        assert len(get_logs) == 0, "GET 请求不应产生审计记录"

    def test_health_not_audited(self, client, isolated_audit_db):
        """/health 不触发审计（豁免路径）"""
        r = client.get("/health")
        assert r.status_code == 200
        store = isolated_audit_db()
        logs = store.list_audit_logs()
        health_logs = [l for l in logs if "health" in l["path"]]
        assert len(health_logs) == 0, "/health 不应产生审计记录"

    def test_sensitive_fields_masked(self, client, isolated_audit_db):
        """敏感字段脱敏（password 字段值不出现在日志中）"""
        # 用 /auth/login 发送含 password 的请求
        client.post(
            "/auth/login",
            json={"username": "testuser", "password": "super_secret_123"},
        )
        # 登录失败也无所谓，关键是审计记录
        store = isolated_audit_db()
        logs = store.list_audit_logs(method="POST")
        login_logs = [l for l in logs if "auth/login" in l["path"]]
        assert len(login_logs) > 0, "POST /auth/login 应产生审计记录"
        payload = login_logs[0]["payload_summary"]
        # 密码明文不应出现在摘要中
        assert "super_secret_123" not in payload, "密码明文泄露到审计日志"
        # 脱敏标记应出现
        assert "***" in payload, "敏感字段应被替换为 ***"

    def test_payload_truncated(self, client, isolated_audit_db):
        """请求体截断（>200 字符截断）"""
        # 构造超长请求体，用唯一 slug
        slug = _unique_slug("trunc")
        long_content = "A" * 500
        r = client.post(
            "/templates",
            params={"slug": slug, "name": "截断测试", "content": long_content},
        )
        assert r.status_code == 200, f"POST /templates 失败: {r.status_code} {r.text}"
        store = isolated_audit_db()
        logs = store.list_audit_logs(method="POST")
        tpl_logs = [l for l in logs if "templates" in l["path"]]
        assert len(tpl_logs) > 0
        summary = tpl_logs[0]["payload_summary"]
        # 摘要不应超过 200 字符
        assert len(summary) <= 200, f"摘要应截断到 200 字符，实际 {len(summary)}"

    def test_request_id_passthrough(self, client, isolated_audit_db):
        """request_id 透传（X-Request-ID header）"""
        custom_id = "test-req-id-abc-123"
        slug = _unique_slug("rid")
        r = client.post(
            "/templates",
            params={"slug": slug, "name": "RID测试", "content": "# T"},
            headers={"X-Request-ID": custom_id},
        )
        assert r.status_code == 200, f"POST /templates 失败: {r.status_code} {r.text}"
        store = isolated_audit_db()
        logs = store.list_audit_logs(method="POST")
        rid_logs = [l for l in logs if l["request_id"] == custom_id]
        assert len(rid_logs) > 0, "应通过 X-Request-ID 关联审计记录"

    def test_list_audit_logs_endpoint(self, client, isolated_audit_db):
        """列表 API /api/audit/logs 返回记录（admin 权限）"""
        # 先产生一条审计记录
        slug = _unique_slug("list-test")
        client.post(
            "/templates",
            params={"slug": slug, "name": "列表测试", "content": "# T"},
        )
        # 查询审计日志端点（dev 模式 anonymous 放行）
        r = client.get("/api/audit/logs")
        assert r.status_code == 200
        data = r.json()
        assert "logs" in data
        assert "count" in data
        assert data["count"] > 0, "应返回至少一条审计记录"
        # 验证记录结构
        log = data["logs"][0]
        for field in ("id", "timestamp", "user", "method", "path", "status"):
            assert field in log, f"审计记录应含 {field} 字段"

    def test_disabled_audit_log_not_recording(
        self, client, isolated_audit_db, monkeypatch
    ):
        """audit_log_enabled=False 时不记录"""
        # 关闭审计日志
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "audit_log_enabled", False)
        # 执行写操作
        slug = _unique_slug("disabled")
        r = client.post(
            "/templates",
            params={"slug": slug, "name": "禁用测试", "content": "# T"},
        )
        assert r.status_code == 200, f"POST /templates 失败: {r.status_code} {r.text}"
        # 审计 DB 应无记录
        store = isolated_audit_db()
        logs = store.list_audit_logs()
        assert len(logs) == 0, "audit_log_enabled=False 时不应记录审计日志"
