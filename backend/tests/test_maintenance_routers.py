"""Wiki / Graph 维护端点测试

验证内容：
A) Graph 维护端点（用 monkeypatch 替换 get_graph_store 为 fake store）
1. GET /graph/maintenance/overview — 一站式总览
2. GET /graph/maintenance/orphan-entities — 查询孤立实体
3. GET /graph/maintenance/low-confidence — 查询低置信度实体
4. GET /graph/maintenance/duplicates — 查询重复实体
5. GET /graph/maintenance/by-source/{doc_id} — 按源文档查询
6. POST /graph/maintenance/bulk-delete — 批量删除
7. POST /graph/maintenance/cleanup-low-confidence — 清理低置信度（dry_run + 实际）
8. POST /graph/maintenance/cleanup-orphans — 清理孤立实体（dry_run + 实际）
9. DELETE /graph/by-source/{doc_id} — 按源文档删除
10. Neo4j 不可用时返回错误响应（不抛 500）

B) Wiki 维护端点（用 monkeypatch 替换相关函数）
11. GET /llm-wiki/maintenance/overview — 维护总览
12. POST /llm-wiki/maintenance/bulk-delete — 批量删除
13. POST /llm-wiki/maintenance/bulk-delete — 空 slugs 返回 400
14. POST /llm-wiki/maintenance/cleanup-orphans — 清理孤岛（dry_run）
15. POST /llm-wiki/maintenance/fix-deadlinks — 修复死链（remove_link / create_stub）
16. POST /llm-wiki/maintenance/fix-deadlinks — 无效 mode 返回 400
"""
from __future__ import annotations

import os

# 确保测试期间关闭认证
os.environ.setdefault("OPSKG_API_TOKEN", "")

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ────────── Fake GraphStore ──────────


class _FakeGraphStore:
    """模拟 GraphStore，记录调用参数并返回预设结果"""

    def __init__(self):
        self.orphan_entities = [
            {"name": "orphan-1", "type": "Service", "confidence": 0.3,
             "source_doc_id": "doc-A"},
        ]
        self.low_confidence_entities = [
            {"name": "low-1", "type": "Service", "confidence": 0.2,
             "source_doc_id": "doc-A"},
        ]
        self.duplicate_entities = [
            {"name": "nginx", "cnt": 2, "sources": ["doc-A", "doc-B"]},
        ]
        self.entities_by_source = [
            {"name": "ent-1", "type": "Service", "confidence": 0.9,
             "source_doc_id": "doc-A"},
        ]
        self.stats = {"total_entities": 10, "total_relations": 5}
        self.calls: list[tuple] = []

    def get_stats(self):
        return self.stats

    def query_orphan_entities(self, limit=200):
        self.calls.append(("query_orphan_entities", limit))
        return self.orphan_entities[:limit]

    def query_low_confidence_entities(self, threshold=0.5, limit=200):
        self.calls.append(("query_low_confidence_entities", threshold, limit))
        return self.low_confidence_entities[:limit]

    def query_entities_by_source(self, source_doc_id, limit=500):
        self.calls.append(("query_entities_by_source", source_doc_id, limit))
        return self.entities_by_source

    def query_duplicate_entities(self, limit=100):
        self.calls.append(("query_duplicate_entities", limit))
        return self.duplicate_entities

    def batch_delete_entities(self, names):
        self.calls.append(("batch_delete_entities", names))
        return {
            "deleted_count": len(names),
            "failed_count": 0,
            "deleted": names,
            "failed": [],
        }

    def cleanup_low_confidence(self, threshold=0.5, limit=500):
        self.calls.append(("cleanup_low_confidence", threshold, limit))
        return {
            "deleted_count": len(self.low_confidence_entities),
            "failed_count": 0,
            "deleted": [e["name"] for e in self.low_confidence_entities],
            "failed": [],
        }

    def cleanup_orphan_entities(self, limit=500):
        self.calls.append(("cleanup_orphan_entities", limit))
        return {
            "deleted_count": len(self.orphan_entities),
            "failed_count": 0,
            "deleted": [e["name"] for e in self.orphan_entities],
            "failed": [],
        }

    def delete_relations_by_source(self, source_doc_id):
        self.calls.append(("delete_relations_by_source", source_doc_id))
        return {"source_doc_id": source_doc_id, "relations_removed": 3}

    def delete_by_source(self, source_doc_id):
        self.calls.append(("delete_by_source", source_doc_id))
        return {
            "source_doc_id": source_doc_id,
            "relations_removed": 3,
            "entities_deleted": 1,
            "entities_failed": 0,
            "failed": [],
        }


@pytest.fixture
def fake_graph_store(monkeypatch):
    """注入 fake GraphStore 替换全局单例"""
    fake = _FakeGraphStore()
    # graph_router 通过 from app.knowledge import get_graph_store 取得
    import app.routers.graph_router as gr_module
    monkeypatch.setattr(gr_module, "get_graph_store", lambda: fake)
    return fake


# ────────── A. Graph 维护端点 ──────────


class TestGraphMaintenanceOverview:
    def test_overview_returns_aggregated_metrics(self, fake_graph_store):
        """总览返回聚合指标"""
        r = client.get("/graph/maintenance/overview")
        assert r.status_code == 200
        data = r.json()
        assert data["stats"] == fake_graph_store.stats
        assert data["orphan_count"] == 1
        assert data["low_confidence_count"] == 1
        assert data["duplicate_count"] == 1
        assert data["low_confidence_threshold"] == 0.5
        assert len(data["sample_orphans"]) == 1
        assert len(data["sample_low_confidence"]) == 1
        assert len(data["sample_duplicates"]) == 1

    def test_overview_with_custom_threshold(self, fake_graph_store):
        """支持自定义低置信度阈值"""
        r = client.get("/graph/maintenance/overview", params={"low_confidence_threshold": 0.3})
        assert r.status_code == 200
        assert r.json()["low_confidence_threshold"] == 0.3


class TestGraphMaintenanceQueries:
    def test_orphan_entities(self, fake_graph_store):
        """查询孤立实体"""
        r = client.get("/graph/maintenance/orphan-entities", params={"limit": 100})
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["entities"][0]["name"] == "orphan-1"

    def test_low_confidence(self, fake_graph_store):
        """查询低置信度实体"""
        r = client.get(
            "/graph/maintenance/low-confidence",
            params={"threshold": 0.5, "limit": 100},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["threshold"] == 0.5
        assert data["count"] == 1

    def test_by_source(self, fake_graph_store):
        """按源文档查询"""
        r = client.get("/graph/maintenance/by-source/doc-A")
        assert r.status_code == 200
        data = r.json()
        assert data["doc_id"] == "doc-A"
        assert data["count"] == 1

    def test_duplicates(self, fake_graph_store):
        """查询重复实体"""
        r = client.get("/graph/maintenance/duplicates", params={"limit": 50})
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["duplicates"][0]["name"] == "nginx"


class TestGraphMaintenanceBulkDelete:
    def test_bulk_delete_returns_counts(self, fake_graph_store):
        """批量删除返回计数"""
        r = client.post(
            "/graph/maintenance/bulk-delete",
            json={"names": ["ent-1", "ent-2"]},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["deleted_count"] == 2
        assert data["failed_count"] == 0

    def test_bulk_delete_empty_names_returns_400(self, fake_graph_store):
        """空 names 返回 400"""
        r = client.post("/graph/maintenance/bulk-delete", json={"names": []})
        assert r.status_code == 400


class TestGraphMaintenanceCleanupLowConfidence:
    def test_dry_run_returns_preview(self, fake_graph_store):
        """dry_run=True 仅返回预览，不实际删除"""
        r = client.post(
            "/graph/maintenance/cleanup-low-confidence",
            json={"threshold": 0.5, "limit": 500, "dry_run": True},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        assert data["count"] == 1
        assert "low-1" in data["would_delete"]

    def test_actual_delete(self, fake_graph_store):
        """实际删除低置信度实体"""
        r = client.post(
            "/graph/maintenance/cleanup-low-confidence",
            json={"threshold": 0.5, "limit": 500, "dry_run": False},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["deleted_count"] == 1
        assert data["threshold"] == 0.5


class TestGraphMaintenanceCleanupOrphans:
    def test_dry_run_returns_preview(self, fake_graph_store):
        """dry_run=True 仅返回预览"""
        r = client.post(
            "/graph/maintenance/cleanup-orphans",
            json={"limit": 500, "dry_run": True},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        assert data["count"] == 1
        assert "orphan-1" in data["would_delete"]

    def test_actual_delete(self, fake_graph_store):
        """实际删除孤立实体"""
        r = client.post(
            "/graph/maintenance/cleanup-orphans",
            json={"limit": 500, "dry_run": False},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["deleted_count"] == 1


class TestGraphMaintenanceDeleteBySource:
    def test_delete_by_source(self, fake_graph_store):
        """按源文档删除实体和关系"""
        r = client.delete("/graph/by-source/doc-A")
        assert r.status_code == 200
        data = r.json()
        assert data["source_doc_id"] == "doc-A"
        assert data["relations_removed"] == 3
        assert data["entities_deleted"] == 1


# ────────── B. Wiki 维护端点 ──────────


@pytest.fixture
def patched_wiki_funcs(monkeypatch):
    """Patch wiki 知识库函数与 VersionControl，模拟 wiki 页面数据"""
    # 准备 fake wiki 页面列表
    pages_data = [
        {"doc_key": "wiki:nginx", "title": "Nginx", "version": 1},
        {"doc_key": "wiki:redis", "title": "Redis", "version": 1},
        {"doc_key": "wiki:orphan-page", "title": "Orphan", "version": 1},
    ]
    deleted_slugs: list[str] = []

    # Patch 各个 wiki 函数
    import app.routers.llm_wiki_router as lr_module

    monkeypatch.setattr(lr_module, "get_all_slugs",
                        lambda: {"nginx", "redis", "orphan-page"})
    monkeypatch.setattr(lr_module, "get_orphan_slugs",
                        lambda all_slugs: {"orphan-page"} if "orphan-page" in all_slugs else set())
    monkeypatch.setattr(lr_module, "get_all_deadlinks",
                        lambda all_slugs=None: [])  # 无死链
    monkeypatch.setattr(lr_module, "list_stale_pages", lambda: [])
    monkeypatch.setattr(lr_module, "rebuild_index", lambda: {"saved": True, "pages": 2})

    # Patch VersionControl
    class _FakeVC:
        def list_by_prefix(self, prefix, limit=2000):
            return pages_data

        def get_latest(self, doc_key):
            for p in pages_data:
                if p["doc_key"] == doc_key:
                    return p
            return None

        def delete_all(self, doc_key):
            slug = doc_key.removeprefix("wiki:")
            deleted_slugs.append(slug)
            return 1

    fake_vc = _FakeVC()
    monkeypatch.setattr(lr_module, "get_version_control", lambda: fake_vc)

    # maintenance_overview 等端点内部用 `from app.storage.version_control
    # import get_version_control` 局部导入，需要同时 patch 源模块
    import app.storage as storage_module
    import app.storage.version_control as vc_module
    monkeypatch.setattr(vc_module, "get_version_control", lambda: fake_vc)
    monkeypatch.setattr(storage_module, "get_version_control", lambda: fake_vc)

    # Patch search engine 与 webhook（_delete_wiki_page 调用）
    import sys
    if "app.search" in sys.modules:
        import app.search as search_module
        class _FakeSearch:
            def remove_index(self, doc_key):
                pass
        monkeypatch.setattr(search_module, "get_search_engine", lambda: _FakeSearch())

    return {"deleted_slugs": deleted_slugs, "pages_data": pages_data, "fake_vc": fake_vc}


class TestWikiMaintenanceOverview:
    def test_overview_returns_metrics(self, patched_wiki_funcs):
        """维护总览返回聚合指标"""
        r = client.get("/llm-wiki/maintenance/overview")
        assert r.status_code == 200
        data = r.json()
        assert data["total_pages"] == 3
        assert data["orphan_count"] == 1
        assert data["deadlink_count"] == 0
        assert data["stale_count"] == 0
        assert "orphan-page" in data["orphans"]


class TestWikiMaintenanceBulkDelete:
    def test_bulk_delete_returns_results(self, patched_wiki_funcs):
        """批量删除返回逐个结果"""
        r = client.post(
            "/llm-wiki/maintenance/bulk-delete",
            json={"slugs": ["nginx", "redis"]},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["deleted_count"] == 2
        assert data["failed_count"] == 0
        assert data["index_rebuilt"] is True
        assert len(data["results"]) == 2
        # 验证删除被调用
        assert set(patched_wiki_funcs["deleted_slugs"]) == {"nginx", "redis"}

    def test_bulk_delete_empty_slugs_returns_400(self, patched_wiki_funcs):
        """空 slugs 返回 400"""
        r = client.post(
            "/llm-wiki/maintenance/bulk-delete",
            json={"slugs": []},
        )
        assert r.status_code == 400

    def test_bulk_delete_nonexistent_slugs_marked_failed(self, patched_wiki_funcs):
        """不存在的 slug 记为 failed"""
        r = client.post(
            "/llm-wiki/maintenance/bulk-delete",
            json={"slugs": ["nonexistent"]},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["deleted_count"] == 0
        assert data["failed_count"] == 1
        # 不重建索引
        assert data["index_rebuilt"] is False

    def test_bulk_delete_skip_index_rebuild(self, patched_wiki_funcs):
        """skip_index_rebuild=True 时不重建索引"""
        r = client.post(
            "/llm-wiki/maintenance/bulk-delete",
            json={"slugs": ["nginx"], "skip_index_rebuild": True},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["index_rebuilt"] is False


class TestWikiMaintenanceCleanupOrphans:
    def test_dry_run_returns_preview(self, patched_wiki_funcs):
        """dry_run=True 仅返回预览，不删除"""
        r = client.post(
            "/llm-wiki/maintenance/cleanup-orphans",
            json={"dry_run": True},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        assert "orphan-page" in data["would_delete"]
        assert data["count"] == 1
        # 没有实际删除
        assert patched_wiki_funcs["deleted_slugs"] == []

    def test_actual_delete(self, patched_wiki_funcs):
        """实际删除孤岛页面"""
        r = client.post(
            "/llm-wiki/maintenance/cleanup-orphans",
            json={"dry_run": False},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is False
        assert data["deleted_count"] == 1
        assert "orphan-page" in patched_wiki_funcs["deleted_slugs"]


class TestWikiMaintenanceFixDeadlinks:
    def test_invalid_mode_returns_400(self, patched_wiki_funcs):
        """无效 mode 返回 400"""
        r = client.post(
            "/llm-wiki/maintenance/fix-deadlinks",
            json={"mode": "invalid_mode"},
        )
        assert r.status_code == 400

    def test_no_deadlinks_returns_zero(self, patched_wiki_funcs):
        """无死链时返回 fixed_count=0"""
        r = client.post(
            "/llm-wiki/maintenance/fix-deadlinks",
            json={"mode": "remove_link"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["fixed_count"] == 0
        assert "无死链" in data["message"]

    def test_create_stub_mode(self, monkeypatch, patched_wiki_funcs):
        """create_stub 模式为死链创建占位页面"""
        # 临时 patch deadlinks 返回有死链
        import app.routers.llm_wiki_router as lr_module
        from app.knowledge.wikilink import DeadLink

        deadlink = DeadLink(slug="missing-target", source_slug="nginx", line=1)
        monkeypatch.setattr(
            lr_module, "get_all_deadlinks", lambda all_slugs=None: [deadlink],
        )
        # get_all_slugs 已被 patched_wiki_funcs patch 为 {nginx, redis, orphan-page}
        # 所以 missing-target 不在 all_slugs 中，会触发 stub 创建

        # Patch VersionControl.save_version 让创建 stub 成功
        saved_keys: list[str] = []

        class _FakeVCStub:
            def save_version(self, **kwargs):
                saved_keys.append(kwargs.get("doc_key", ""))
                return {"doc_key": kwargs.get("doc_key"), "version": 1}

            def get_latest(self, doc_key):
                return None  # stub 不存在

        fake_vc_stub = _FakeVCStub()
        # 端点内部用 `from app.storage.version_control import get_version_control`
        # 局部导入，需要同时 patch 源模块
        import app.storage.version_control as vc_module
        monkeypatch.setattr(lr_module, "get_version_control", lambda: fake_vc_stub)
        monkeypatch.setattr(vc_module, "get_version_control", lambda: fake_vc_stub)

        r = client.post(
            "/llm-wiki/maintenance/fix-deadlinks",
            json={"mode": "create_stub", "dry_run": False},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "create_stub"
        assert data["fixed_count"] >= 1
        assert "wiki:missing-target" in saved_keys
