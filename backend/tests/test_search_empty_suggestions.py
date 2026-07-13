"""空结果兜底提示测试（P2-1.6）

验证 /search 端点在 0 命中时返回 suggestions 字段，含：
- similar_queries / diagnosis / upload_hint / did_you_mean
- 非空结果不含 suggestions 字段
- did_you_mean 逻辑（基于 FTS 词频命中情况）
- diagnosis 含合理原因

DB 隔离：monkeypatch search_engine.DB_PATH 到 tmp_path，重置单例。
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════ DB 隔离 fixture ═══════════════


@pytest.fixture
def isolated_search_db(tmp_path, monkeypatch):
    """重定向 search_engine DB_PATH 到 tmp_path，重置单例"""
    import app.search.search_engine as se_mod

    se_db = tmp_path / "search_index.db"
    monkeypatch.setattr(se_mod, "DB_PATH", se_db)
    monkeypatch.setattr(se_mod, "_engine", None)
    yield se_mod


@pytest.fixture
def client():
    """FastAPI TestClient（使用全局 app）"""
    from app.main import app

    return TestClient(app)


# ═══════════════ 端点级：空结果返回 suggestions ═══════════════


class TestEmptySuggestionsEndpoint:
    def test_empty_results_return_suggestions_field(self, isolated_search_db, client):
        """空结果时响应应包含 suggestions 字段"""
        r = client.get("/search", params={"q": "zzznonexistentxxx12345"})
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 0
        assert "suggestions" in data
        assert data["suggestions"] is not None

    def test_suggestions_contains_all_fields(self, isolated_search_db, client):
        """suggestions 应包含四个子字段"""
        r = client.get("/search", params={"q": "zzznonexistentxxx12345"})
        sug = r.json()["suggestions"]
        assert "similar_queries" in sug
        assert "diagnosis" in sug
        assert "upload_hint" in sug
        assert "did_you_mean" in sug
        # upload_hint 应是非空文案
        assert isinstance(sug["upload_hint"], str) and sug["upload_hint"]
        # similar_queries 应为列表
        assert isinstance(sug["similar_queries"], list)
        # diagnosis 应为非空字符串
        assert isinstance(sug["diagnosis"], str) and sug["diagnosis"]

    def test_nonempty_results_no_suggestions_field(self, isolated_search_db, client):
        """非空结果不应包含 suggestions 字段"""
        engine = isolated_search_db.get_search_engine()
        engine.index_document("doc-1", "Nginx 故障排查", "Nginx 502 Bad Gateway", "md")

        r = client.get("/search", params={"q": "nginx"})
        assert r.status_code == 200
        data = r.json()
        assert data["count"] > 0
        assert "suggestions" not in data


# ═══════════════ _build_empty_suggestions 单元测试 ═══════════════


class TestBuildEmptySuggestions:
    def test_did_you_mean_logic_with_partial_hits(self, isolated_search_db):
        """did_you_mean 逻辑：部分 term 命中、部分零命中时建议移除零命中 term"""
        engine = isolated_search_db.get_search_engine()
        engine.index_document("doc-1", "Nginx 故障", "Nginx 502 故障排查", "md")

        from app.routers.search_router import _build_empty_suggestions

        # "nginx" 命中，"xyzxyz" 零命中 → did_you_mean 应为 "nginx"
        sug = _build_empty_suggestions("nginx xyzxyz", [])
        assert sug["did_you_mean"] == "nginx"
        # similar_queries 应包含命中的 term
        assert "nginx" in sug["similar_queries"]
        # diagnosis 应提及部分关键词未命中
        assert "部分关键词未命中" in sug["diagnosis"]

    def test_did_you_mean_null_when_all_tokens_miss(self, isolated_search_db):
        """所有 term 均零命中时 did_you_mean 应为 None"""
        engine = isolated_search_db.get_search_engine()
        engine.index_document("doc-1", "Nginx", "Nginx 内容", "md")

        from app.routers.search_router import _build_empty_suggestions

        sug = _build_empty_suggestions("totallyunknownterm", [])
        assert sug["did_you_mean"] is None
        assert sug["similar_queries"] == []
        assert "无相关文档" in sug["diagnosis"]

    def test_did_you_mean_null_when_all_tokens_hit(self, isolated_search_db):
        """所有 term 均命中（但组合无共同文档）时 did_you_mean 应为 None"""
        engine = isolated_search_db.get_search_engine()
        engine.index_document("doc-1", "Nginx", "Nginx 内容", "md")
        engine.index_document("doc-2", "Redis", "Redis 缓存", "md")

        from app.routers.search_router import _build_empty_suggestions

        # "nginx" 与 "redis" 各自命中，但无文档同时含两者
        sug = _build_empty_suggestions("nginx redis", [])
        assert sug["did_you_mean"] is None
        # similar_queries 应提供各 term 作为备选
        assert "nginx" in sug["similar_queries"]
        assert "redis" in sug["similar_queries"]

    def test_diagnosis_contains_reasonable_cause(self, isolated_search_db, client):
        """diagnosis 应包含合理原因（非空且有意义的中文说明）"""
        engine = isolated_search_db.get_search_engine()
        engine.index_document("doc-1", "Nginx 故障", "Nginx 故障排查", "md")

        from app.routers.search_router import _build_empty_suggestions

        # 场景 1：部分未命中
        sug1 = _build_empty_suggestions("nginx nonexistentterm", [])
        assert sug1["diagnosis"]
        assert "部分关键词未命中" in sug1["diagnosis"]

        # 场景 2：完全无命中
        sug2 = _build_empty_suggestions("totallyunknown", [])
        assert sug2["diagnosis"]
        assert "无相关文档" in sug2["diagnosis"]

    def test_query_too_long_diagnosis(self, isolated_search_db):
        """query 过长（token > 6）时 diagnosis 应提示精简"""
        from app.routers.search_router import _build_empty_suggestions

        # 8 个英文 token，均零命中
        sug = _build_empty_suggestions("aa bb cc dd ee ff gg hh", [])
        assert "query 过长" in sug["diagnosis"]

    def test_empty_query_diagnosis(self, isolated_search_db):
        """空 query 的 diagnosis 应提示无有效关键词或中文未分词"""
        from app.routers.search_router import _build_empty_suggestions

        sug = _build_empty_suggestions("   ", [])
        assert sug["diagnosis"]
        assert "无有效关键词" in sug["diagnosis"] or "无相关文档" in sug["diagnosis"]

    def test_existing_response_fields_preserved(self, isolated_search_db, client):
        """空结果时原有返回字段应保留（query/results/count/fusion 等）"""
        r = client.get("/search", params={"q": "zzznonexistentxxx"})
        data = r.json()
        for field in ("query", "results", "count", "vector_enabled",
                      "vector_actually_used", "fusion", "suggestions"):
            assert field in data
        assert data["results"] == []
        assert data["count"] == 0
