"""Versions Router API 测试

使用 FastAPI TestClient 覆盖版本控制路由端点。
"""
from __future__ import annotations

import os

os.environ.setdefault("OPSKG_API_TOKEN", "")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestVersions:
    def test_list_versions_empty(self):
        r = client.get("/versions/nonexistent-key")
        assert r.status_code == 200
        data = r.json()
        assert data["doc_key"] == "nonexistent-key"
        assert "versions" in data
        assert "count" in data

    def test_get_version_not_found(self):
        r = client.get("/versions/nonexistent-key/1")
        assert r.status_code == 404

    def test_diff_versions(self):
        r = client.get("/versions/nonexistent-key/diff/1/2")
        assert r.status_code == 200
        data = r.json()
        # diff returns a result even for nonexistent
        assert isinstance(data, dict)

    def test_rollback_not_found(self):
        r = client.post("/versions/nonexistent-key/rollback/1")
        assert r.status_code == 404

    def test_save_version(self):
        r = client.post(
            "/versions/test-save-key/save",
            params={
                "title": "Test Title",
                "content": "# Test Content",
                "change_summary": "Initial save",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert "version" in data


class TestVersionsLifecycle:
    """Test a full version lifecycle: save → list → get → diff → rollback"""

    def test_full_lifecycle(self):
        doc_key = "lifecycle-test-key"

        # Save initial version
        r1 = client.post(
            f"/versions/{doc_key}/save",
            params={
                "title": "V1 Title",
                "content": "V1 content",
                "change_summary": "First version",
            },
        )
        assert r1.status_code == 200
        v1 = r1.json()["version"]

        # Save second version
        r2 = client.post(
            f"/versions/{doc_key}/save",
            params={
                "title": "V2 Title",
                "content": "V2 content modified",
                "change_summary": "Second version",
            },
        )
        assert r2.status_code == 200
        v2 = r2.json()["version"]
        assert v2 > v1

        # List versions
        r_list = client.get(f"/versions/{doc_key}")
        assert r_list.status_code == 200
        assert r_list.json()["count"] >= 2

        # Get specific version
        r_get = client.get(f"/versions/{doc_key}/{v1}")
        assert r_get.status_code == 200
        assert r_get.json()["title"] == "V1 Title"

        # Diff versions
        r_diff = client.get(f"/versions/{doc_key}/diff/{v1}/{v2}")
        assert r_diff.status_code == 200

        # Rollback to v1
        r_rollback = client.post(f"/versions/{doc_key}/rollback/{v1}")
        assert r_rollback.status_code == 200
