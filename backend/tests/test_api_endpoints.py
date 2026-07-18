"""API 端点测试（P0-3）

使用 FastAPI TestClient 覆盖所有路由，确保端到端可用。
"""
from __future__ import annotations

import io
import os

# 确保测试期间关闭认证
os.environ.setdefault("OPSKG_API_TOKEN", "")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ────────── 健康检查 & 元数据 ──────────

class TestHealth:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_list_parsers(self):
        r = client.get("/parsers")
        assert r.status_code == 200
        fmts = r.json()["formats"]
        assert "markdown" in fmts
        assert "word" in fmts
        assert len(fmts) >= 20


# ────────── 文档管理 API ──────────

class TestDocumentAPI:
    def test_list_documents_empty(self):
        r = client.get("/documents")
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert "items" in data["data"]
        assert "stats" in data

    def test_document_stats(self):
        r = client.get("/documents/stats")
        assert r.status_code == 200
        assert "total" in r.json()

    def test_search_documents(self):
        r = client.get("/documents/search", params={"q": "test"})
        assert r.status_code == 200
        assert "results" in r.json()

    def test_get_nonexistent_document(self):
        r = client.get("/documents/nonexistent")
        assert r.status_code == 404

    def test_delete_nonexistent_document(self):
        r = client.delete("/documents/nonexistent")
        assert r.status_code == 404


# ────────── 解析 API ──────────

class TestParseAPI:
    def test_parse_markdown(self):
        content = b"# Test\n\nHello world\n"
        r = client.post(
            "/parsers/parse/markdown",
            files={"file": ("test.md", io.BytesIO(content), "text/markdown")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "Test"
        assert data["stored"] is True
        assert "doc_id" in data

    def test_parse_text(self):
        content = b"First paragraph.\n\nSecond paragraph.\n"
        r = client.post(
            "/parsers/parse/txt",
            files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        )
        assert r.status_code == 200
        assert len(r.json()["elements"]) >= 2

    def test_parse_unsupported_format(self):
        r = client.post(
            "/parsers/parse/unknown",
            files={"file": ("test.xyz", io.BytesIO(b"data"), "application/octet-stream")},
        )
        assert r.status_code == 400

    def test_parse_batch(self):
        md_content = b"# Doc 1\n\nContent"
        txt_content = b"Text content\n\nPara 2"
        r = client.post(
            "/parsers/parse/batch",
            files=[
                ("files", ("d1.md", io.BytesIO(md_content), "text/markdown")),
                ("files", ("d2.txt", io.BytesIO(txt_content), "text/plain")),
            ],
        )
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) == 2


# ────────── 审查队列 API ──────────

class TestReviewAPI:
    def test_review_stats(self):
        r = client.get("/review/stats")
        assert r.status_code == 200

    def test_review_queue_list(self):
        r = client.get("/review/queue")
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert "items" in data["data"]

    def test_approve_nonexistent(self):
        r = client.post("/review/999999/approve")
        assert r.status_code == 404

    def test_reject_nonexistent(self):
        r = client.post("/review/999999/reject")
        assert r.status_code == 404


# ────────── 图谱 API（Neo4j 未连接时优雅降级） ──────────

class TestGraphAPI:
    def test_graph_stats(self):
        r = client.get("/graph/stats")
        assert r.status_code == 200
        # Neo4j 未连接时返回 error 字段
        data = r.json()
        assert "error" in data or "entities" in data

    def test_graph_visualize(self):
        r = client.get("/graph/visualize")
        assert r.status_code == 200
        data = r.json()
        assert "nodes" in data
        assert "links" in data

    def test_graph_search(self):
        r = client.get("/graph/search", params={"q": "test"})
        assert r.status_code == 200


# ────────── 认证 API ──────────

class TestAuth:
    def test_health_no_auth_required(self):
        """健康检查不需要认证"""
        r = client.get("/health")
        assert r.status_code == 200

    def test_get_documents_no_auth_required(self):
        """读操作不需要认证（Token 为空时）"""
        r = client.get("/documents")
        assert r.status_code == 200


# ────────── 搜索 API（P1-1） ──────────

class TestSearchAPI:
    def test_search_empty(self):
        r = client.get("/search", params={"q": "nonexistent"})
        assert r.status_code == 200
        assert "results" in r.json()

    def test_search_stats(self):
        r = client.get("/search/stats")
        assert r.status_code == 200
        assert "indexed_docs" in r.json()

    def test_search_after_parse(self):
        """解析文档后应可搜索"""
        content = "# Kubernetes 部署指南\n\n部署 K8s 集群的详细步骤".encode("utf-8")
        client.post(
            "/parsers/parse/markdown",
            files={"file": ("k8s.md", io.BytesIO(content), "text/markdown")},
        )
        r = client.get("/search", params={"q": "Kubernetes"})
        assert r.status_code == 200
        results = r.json()["results"]
        assert len(results) > 0


# ────────── 版本控制 API（P1-2） ──────────

class TestVersionAPI:
    def test_save_and_list(self):
        # 保存版本
        client.post(
            "/versions/test-doc/save",
            params={"title": "测试", "content": "版本1内容", "change_summary": "初始"},
        )
        client.post(
            "/versions/test-doc/save",
            params={"title": "测试", "content": "版本2内容", "change_summary": "修改"},
        )
        r = client.get("/versions/test-doc")
        assert r.status_code == 200
        assert r.json()["count"] >= 2

    def test_get_version(self):
        client.post(
            "/versions/vtest/save",
            params={"title": "T", "content": "Hello"},
        )
        r = client.get("/versions/vtest/1")
        assert r.status_code == 200
        assert r.json()["content"] == "Hello"

    def test_diff(self):
        client.post(
            "/versions/difftest/save",
            params={"title": "T", "content": "line1\nline2"},
        )
        client.post(
            "/versions/difftest/save",
            params={"title": "T", "content": "line1\nline2\nline3"},
        )
        r = client.get("/versions/difftest/diff/1/2")
        assert r.status_code == 200
        assert r.json()["added_lines"] >= 1


# ────────── 模板 API（P1-3） ──────────

class TestTemplateAPI:
    def test_list_builtin(self):
        r = client.get("/templates")
        assert r.status_code == 200
        assert r.json()["count"] >= 5

    def test_get_runbook(self):
        r = client.get("/templates/runbook")
        assert r.status_code == 200
        assert "Runbook" in r.json()["name"]

    def test_render(self):
        r = client.post(
            "/templates/runbook/render",
            json={"title": "部署服务", "description": "部署说明", "steps": [
                {"step_num": 1, "step_name": "拉取镜像", "step_command": "docker pull", "expected_output": "OK"}
            ]},
        )
        assert r.status_code == 200
        assert "部署服务" in r.json()["rendered"]

    def test_create_custom(self):
        # 清理可能残留的同 slug 模板（保证测试隔离）
        client.delete("/templates/my-tpl")
        r = client.post(
            "/templates",
            params={"slug": "my-tpl", "name": "我的模板", "content": "# {{title}}"},
        )
        assert r.status_code == 200

    def test_delete_builtin_fails(self):
        r = client.delete("/templates/runbook")
        assert r.status_code == 403


# ────────── 导出 API（P1-4） ──────────

class TestExportAPI:
    def test_export_markdown(self):
        r = client.post(
            "/export",
            json={"title": "测试", "content": "Hello world", "format": "markdown"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/markdown; charset=utf-8"

    def test_export_html(self):
        r = client.post(
            "/export",
            json={"title": "测试", "content": "# 标题\n段落", "format": "html"},
        )
        assert r.status_code == 200
        assert b"<html" in r.content

    def test_export_text(self):
        r = client.post(
            "/export",
            json={"title": "测试", "content": "**粗体**", "format": "text"},
        )
        assert r.status_code == 200
        assert "粗体" in r.content.decode("utf-8")


# ────────── Wiki API（P1-5） ──────────

class TestWikiAPI:
    def test_publish_and_get(self):
        # 发布
        client.post(
            "/wiki/test-page",
            params={"title": "测试页面", "content": "这是内容", "change_summary": "初始"},
        )
        # 获取
        r = client.get("/wiki/test-page")
        assert r.status_code == 200
        assert r.json()["title"] == "测试页面"
        assert r.json()["content"] == "这是内容"

    def test_list(self):
        client.post(
            "/wiki/list-test",
            params={"title": "列表测试", "content": "内容"},
        )
        r = client.get("/wiki")
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_version_increment(self):
        client.post(
            "/wiki/ver-test",
            params={"title": "V1", "content": "content1"},
        )
        client.post(
            "/wiki/ver-test",
            params={"title": "V2", "content": "content2", "change_summary": "更新"},
        )
        r = client.get("/wiki/ver-test")
        assert r.json()["version"] >= 2

    def test_delete(self):
        client.post("/wiki/del-test", params={"title": "T", "content": "C"})
        r = client.delete("/wiki/del-test")
        assert r.status_code == 200
