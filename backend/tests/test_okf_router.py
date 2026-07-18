"""OKF Router API 测试

使用 FastAPI TestClient 覆盖 OKF 路由端点。
"""
from __future__ import annotations

import io
import os

os.environ.setdefault("OPSKG_API_TOKEN", "")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestOKFVersion:
    def test_get_okf_version(self):
        r = client.get("/okf/version")
        assert r.status_code == 200
        data = r.json()
        assert data["okf_version"] == "0.1"
        assert data["adapter"] == "opskg-okf-adapter"
        assert "supported_constraints" in data

    def test_version_has_constraints(self):
        r = client.get("/okf/version")
        data = r.json()
        constraints = data["supported_constraints"]
        assert "concept_file_has_yaml_frontmatter" in constraints
        assert "concept_file_has_nonempty_type" in constraints
        assert "reserved_files_follow_roles" in constraints


class TestOKFPreview:
    def test_preview_returns_summary(self):
        r = client.get("/okf/preview")
        assert r.status_code == 200
        data = r.json()
        assert "total" in data
        assert "by_type" in data
        assert "has_index" in data
        assert "has_log" in data


class TestOKFExport:
    def test_export_returns_tarball(self):
        r = client.get("/okf/export")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/gzip"
        assert "attachment" in r.headers["content-disposition"]

    def test_export_has_response_headers(self):
        r = client.get("/okf/export")
        assert "X-OKF-Pages-Exported" in r.headers
        assert "X-OKF-Errors" in r.headers


class TestOKFValidate:
    def test_validate_empty_tarball_rejected(self):
        # Empty file should fail validation
        r = client.post(
            "/okf/validate",
            files={"file": ("empty.tar.gz", io.BytesIO(b""), "application/gzip")},
        )
        # Should fail with 400 since tarball is empty
        assert r.status_code in (400, 422, 500)

    def test_validate_wrong_extension(self):
        r = client.post(
            "/okf/validate",
            files={"file": ("test.zip", io.BytesIO(b"data"), "application/zip")},
        )
        assert r.status_code == 400


class TestOKFImport:
    def test_import_wrong_extension(self):
        r = client.post(
            "/okf/import",
            files={"file": ("test.zip", io.BytesIO(b"data"), "application/zip")},
        )
        assert r.status_code == 400

    def test_import_empty_tarball(self):
        r = client.post(
            "/okf/import",
            files={"file": ("empty.tar.gz", io.BytesIO(b""), "application/gzip")},
        )
        # Should return error
        assert r.status_code in (400, 422, 500)


class TestOKFImportDir:
    def test_missing_path(self):
        r = client.post("/okf/import/dir", json={})
        assert r.status_code == 400

    def test_nonexistent_path(self):
        r = client.post("/okf/import/dir", json={"path": "/nonexistent/path"})
        assert r.status_code == 404


class TestOKFValidateWiki:
    def test_validate_wiki_returns_result(self):
        r = client.get("/okf/validate/wiki")
        assert r.status_code == 200
        data = r.json()
        assert "okf_version" in data
        assert "valid" in data
        assert "errors" in data
        assert "warnings" in data
        assert "findings" in data
