"""安全响应头中间件测试"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security.headers import SecurityHeadersMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/")
    async def root() -> dict:
        return {"ok": True}

    @app.get("/api/v1/test")
    async def test() -> dict:
        return {"data": "test"}

    return app


class TestSecurityHeaders:
    """安全响应头中间件测试"""

    def test_csp_header_present(self) -> None:
        """验证 CSP 头存在"""
        client = TestClient(_make_app())
        resp = client.get("/")
        assert "Content-Security-Policy" in resp.headers
        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "script-src" in csp
        assert "style-src" in csp

    def test_x_content_type_options(self) -> None:
        """验证 X-Content-Type-Options: nosniff"""
        client = TestClient(_make_app())
        resp = client.get("/")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"

    def test_x_frame_options(self) -> None:
        """验证 X-Frame-Options: DENY"""
        client = TestClient(_make_app())
        resp = client.get("/")
        assert resp.headers["X-Frame-Options"] == "DENY"

    def test_x_xss_protection(self) -> None:
        """验证 X-XSS-Protection"""
        client = TestClient(_make_app())
        resp = client.get("/")
        assert "X-XSS-Protection" in resp.headers

    def test_referrer_policy(self) -> None:
        """验证 Referrer-Policy"""
        client = TestClient(_make_app())
        resp = client.get("/")
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_permissions_policy(self) -> None:
        """验证 Permissions-Policy"""
        client = TestClient(_make_app())
        resp = client.get("/")
        assert "Permissions-Policy" in resp.headers
        pp = resp.headers["Permissions-Policy"]
        assert "camera=()" in pp
        assert "microphone=()" in pp

    def test_all_endpoints_have_security_headers(self) -> None:
        """验证所有端点都有安全头"""
        client = TestClient(_make_app())
        for path in ["/", "/api/v1/test"]:
            resp = client.get(path)
            assert "Content-Security-Policy" in resp.headers
            assert "X-Content-Type-Options" in resp.headers
            assert "X-Frame-Options" in resp.headers

    def test_does_not_overwrite_existing_headers(self) -> None:
        """验证不覆盖已有的安全头"""
        app = FastAPI()

        @app.get("/custom")
        async def custom() -> dict:
            from fastapi.responses import Response
            return Response(
                content=b'{"ok":true}',
                headers={"X-Frame-Options": "SAMEORIGIN"},
                media_type="application/json",
            )

        app.add_middleware(SecurityHeadersMiddleware)
        client = TestClient(app)
        resp = client.get("/custom")
        # 已有值不被覆盖
        assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
        # 但其他头仍被添加
        assert "Content-Security-Policy" in resp.headers
