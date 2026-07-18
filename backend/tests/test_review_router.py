"""Review Router API 测试

使用 FastAPI TestClient 覆盖审查队列路由端点。
"""
from __future__ import annotations

import os

os.environ.setdefault("OPSKG_API_TOKEN", "")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestReviewQueue:
    def test_list_queue_empty(self):
        r = client.get("/review/queue")
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert "items" in data["data"]
        assert "stats" in data
        assert "total" in data["data"]

    def test_list_queue_with_status(self):
        r = client.get("/review/queue", params={"status": "pending"})
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert "items" in data["data"]

    def test_list_queue_with_limit_offset(self):
        r = client.get("/review/queue", params={"limit": 10, "offset": 0})
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert "items" in data["data"]

    def test_list_queue_empty_status(self):
        r = client.get("/review/queue", params={"status": ""})
        assert r.status_code == 200


class TestReviewStats:
    def test_review_stats(self):
        r = client.get("/review/stats")
        assert r.status_code == 200
        data = r.json()
        assert "pending" in data
        assert "approved" in data
        assert "rejected" in data
        assert "modified" in data


class TestReviewApprove:
    def test_approve_nonexistent(self):
        r = client.post("/review/99999/approve")
        assert r.status_code == 404

    def test_approve_with_note(self):
        r = client.post("/review/99999/approve", params={"note": "test note"})
        assert r.status_code == 404


class TestReviewReject:
    def test_reject_nonexistent(self):
        r = client.post("/review/99999/reject", json={"reason": "not needed"})
        assert r.status_code == 404

    def test_reject_empty_body(self):
        r = client.post("/review/99999/reject")
        assert r.status_code == 404


class TestReviewBatchApprove:
    def test_batch_approve_empty(self):
        r = client.post("/review/batch-approve", json={"ids": []})
        assert r.status_code == 200
        data = r.json()
        assert data["approved"] == 0

    def test_batch_approve_nonexistent(self):
        r = client.post("/review/batch-approve", json={"ids": [99999, 99998]})
        assert r.status_code == 200
        data = r.json()
        assert data["approved"] == 0
