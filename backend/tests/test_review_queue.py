"""ReviewQueue 单元测试

覆盖：
- add_entity / add_relation / batch_add
- list_pending / list_by_status / count_by_status
- get_by_id / approve / reject / modify / batch_approve
- get_stats
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.knowledge import review_queue as rq_module


@pytest.fixture
def temp_db():
    """使用临时 DB 文件，避免污染开发环境"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = Path(f.name)
    original = rq_module.DB_PATH
    rq_module.DB_PATH = tmp_path
    # Reset singleton
    rq_module._queue = None
    try:
        yield tmp_path
    finally:
        rq_module.DB_PATH = original
        rq_module._queue = None
        if tmp_path.exists():
            tmp_path.unlink()


@pytest.fixture
def queue(temp_db):
    """创建新的 ReviewQueue 实例"""
    q = rq_module.ReviewQueue()
    return q


# ═══════════════ add_entity / add_relation ═══════════════


class TestAddEntity:
    def test_add_entity_returns_id(self, queue):
        eid = queue.add_entity(
            entity_type="Service",
            name="nginx",
            properties={"port": 80},
            confidence=0.9,
            evidence="Nginx runs on port 80",
            source_doc_id="doc-001",
        )
        assert isinstance(eid, int)
        assert eid > 0

    def test_add_entity_stored_correctly(self, queue):
        eid = queue.add_entity(
            entity_type="Host",
            name="web-01",
            properties={"ip": "10.0.0.1"},
            confidence=0.85,
            evidence="Host web-01 at 10.0.0.1",
            source_doc_id="doc-002",
        )
        item = queue.get_by_id(eid)
        assert item is not None
        assert item["item_type"] == "entity"
        assert item["entity_type"] == "Host"
        assert item["name"] == "web-01"
        assert item["confidence"] == 0.85
        assert item["status"] == "pending"

    def test_add_entity_empty_properties(self, queue):
        eid = queue.add_entity(
            entity_type="Concept",
            name="load-balancing",
            properties={},
            confidence=0.5,
            evidence="",
            source_doc_id="",
        )
        assert isinstance(eid, int)
        assert eid > 0


class TestAddRelation:
    def test_add_relation_returns_id(self, queue):
        rid = queue.add_relation(
            relation_type="DEPENDS_ON",
            from_entity="nginx",
            to_entity="postgres",
            properties={"critical": True},
            confidence=0.8,
            evidence="Nginx depends on postgres",
            source_doc_id="doc-003",
        )
        assert isinstance(rid, int)
        assert rid > 0

    def test_add_relation_stored_correctly(self, queue):
        rid = queue.add_relation(
            relation_type="RUNS_ON",
            from_entity="nginx",
            to_entity="web-01",
            properties={},
            confidence=0.9,
            evidence="Nginx runs on web-01",
            source_doc_id="doc-004",
        )
        item = queue.get_by_id(rid)
        assert item is not None
        assert item["item_type"] == "relation"
        assert item["relation_type"] == "RUNS_ON"
        assert item["from_entity"] == "nginx"
        assert item["to_entity"] == "web-01"
        assert item["confidence"] == 0.9


# ═══════════════ batch_add ═══════════════


class TestBatchAdd:
    def test_batch_add_entities_and_relations(self, queue):
        entities = [
            {"entity_type": "Service", "name": "svc-a", "properties": {}, "confidence": 0.8},
            {"entity_type": "Service", "name": "svc-b", "properties": {}, "confidence": 0.7},
        ]
        relations = [
            {"relation_type": "DEPENDS_ON", "from_entity": "svc-a", "to_entity": "svc-b",
             "properties": {}, "confidence": 0.9},
        ]
        result = queue.batch_add(entities, relations)
        assert result["entities_added"] == 2
        assert result["relations_added"] == 1

    def test_batch_add_empty(self, queue):
        result = queue.batch_add([], [])
        assert result["entities_added"] == 0
        assert result["relations_added"] == 0


# ═══════════════ list_pending / list_by_status / count_by_status ═══════════════


class TestListPending:
    def test_empty_queue(self, queue):
        items = queue.list_pending()
        assert items == []

    def test_with_items(self, queue):
        queue.add_entity("Service", "svc", {}, 0.5, "", "")
        items = queue.list_pending()
        assert len(items) == 1
        assert items[0]["name"] == "svc"

    def test_limit_and_offset(self, queue):
        for i in range(3):
            queue.add_entity("Service", f"svc-{i}", {}, 0.5, "", "")
        items = queue.list_pending(limit=2, offset=0)
        assert len(items) == 2
        items2 = queue.list_pending(limit=2, offset=2)
        assert len(items2) == 1


class TestListByStatus:
    def test_filter_by_status(self, queue):
        queue.add_entity("Service", "svc-pending", {}, 0.5, "", "")
        eid = queue.add_entity("Service", "svc-approved", {}, 0.6, "", "")
        queue.approve(eid)

        pending = queue.list_by_status("pending")
        approved = queue.list_by_status("approved")
        assert len(pending) == 1
        assert pending[0]["name"] == "svc-pending"
        assert len(approved) == 1
        assert approved[0]["name"] == "svc-approved"

    def test_list_all_statuses(self, queue):
        queue.add_entity("Service", "a", {}, 0.5, "", "")
        queue.add_entity("Service", "b", {}, 0.5, "", "")
        items = queue.list_by_status(None)
        assert len(items) == 2


class TestCountByStatus:
    def test_count_pending(self, queue):
        for _ in range(3):
            queue.add_entity("Service", "svc", {}, 0.5, "", "")
        assert queue.count_by_status("pending") == 3

    def test_count_all(self, queue):
        queue.add_entity("Service", "a", {}, 0.5, "", "")
        queue.add_entity("Service", "b", {}, 0.5, "", "")
        assert queue.count_by_status(None) == 2

    def test_count_empty(self, queue):
        assert queue.count_by_status("pending") == 0


# ═══════════════ get_by_id ═══════════════


class TestGetById:
    def test_get_existing(self, queue):
        eid = queue.add_entity("Service", "test", {}, 0.5, "", "")
        item = queue.get_by_id(eid)
        assert item is not None
        assert item["id"] == eid

    def test_get_nonexistent(self, queue):
        assert queue.get_by_id(99999) is None


# ═══════════════ approve / reject / modify ═══════════════


class TestApprove:
    def test_approve_changes_status(self, queue):
        eid = queue.add_entity("Service", "svc", {}, 0.5, "", "")
        ok = queue.approve(eid)
        assert ok is True
        item = queue.get_by_id(eid)
        assert item["status"] == "approved"

    def test_approve_nonexistent(self, queue):
        assert queue.approve(99999) is False

    def test_approve_with_note(self, queue):
        eid = queue.add_entity("Service", "svc", {}, 0.5, "", "")
        queue.approve(eid, note="Approved by admin")
        item = queue.get_by_id(eid)
        assert item["reviewer_note"] == "Approved by admin"


class TestReject:
    def test_reject_changes_status(self, queue):
        eid = queue.add_entity("Service", "svc", {}, 0.5, "", "")
        ok = queue.reject(eid, "Not needed")
        assert ok is True
        item = queue.get_by_id(eid)
        assert item["status"] == "rejected"
        assert item["reviewer_note"] == "Not needed"

    def test_reject_nonexistent(self, queue):
        assert queue.reject(99999) is False


class TestModify:
    def test_modify_changes_status(self, queue):
        eid = queue.add_entity("Service", "svc", {"desc": "old"}, 0.5, "", "")
        ok = queue.modify(eid, {"name": "svc-modified"}, "Updated name")
        assert ok is True
        item = queue.get_by_id(eid)
        assert item["status"] == "modified"

    def test_modify_nonexistent(self, queue):
        assert queue.modify(99999, {"name": "x"}) is False


# ═══════════════ batch_approve ═══════════════


class TestBatchApprove:
    def test_batch_approve_multiple(self, queue):
        ids = []
        for i in range(3):
            ids.append(queue.add_entity("Service", f"svc-{i}", {}, 0.5, "", ""))
        count = queue.batch_approve(ids)
        assert count == 3
        for eid in ids:
            assert queue.get_by_id(eid)["status"] == "approved"

    def test_batch_approve_empty(self, queue):
        assert queue.batch_approve([]) == 0

    def test_batch_approve_some_nonexistent(self, queue):
        eid = queue.add_entity("Service", "svc", {}, 0.5, "", "")
        count = queue.batch_approve([eid, 99999])
        assert count == 1


# ═══════════════ get_stats ═══════════════


class TestGetStats:
    def test_empty_stats(self, queue):
        stats = queue.get_stats()
        assert stats["pending"] == 0
        assert stats["approved"] == 0
        assert stats["rejected"] == 0
        assert stats["modified"] == 0

    def test_mixed_stats(self, queue):
        # Add and approve some
        e1 = queue.add_entity("Service", "a", {}, 0.5, "", "")
        e2 = queue.add_entity("Service", "b", {}, 0.5, "", "")
        e3 = queue.add_entity("Service", "c", {}, 0.5, "", "")
        queue.approve(e1)
        queue.reject(e2, "no")
        queue.modify(e3, {"name": "new"})

        stats = queue.get_stats()
        assert stats["pending"] == 0  # e3 was modified, not pending
        assert stats["approved"] == 1
        assert stats["rejected"] == 1
        assert stats["modified"] == 1


# ═══════════════ get_review_queue singleton ═══════════════


class TestGetReviewQueue:
    def test_returns_review_queue(self, temp_db):
        # Reset singleton
        rq_module._queue = None
        q = rq_module.get_review_queue()
        assert isinstance(q, rq_module.ReviewQueue)

    def test_singleton_reuse(self, temp_db):
        rq_module._queue = None
        q1 = rq_module.get_review_queue()
        q2 = rq_module.get_review_queue()
        assert q1 is q2
