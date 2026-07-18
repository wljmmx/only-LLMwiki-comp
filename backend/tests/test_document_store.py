"""DocumentStore 单元测试

使用 SQLite 内存数据库（:memory:）作为测试后端，不依赖外部服务。
覆盖 CRUD 操作、文档重复检测、pipeline 状态管理。
"""
from __future__ import annotations

from app.storage.document_store import DocumentStore

# ────────── CRUD 操作 ──────────


class TestDocumentStoreCRUD:
    def test_save_document(self, temp_db_document_store, sample_document):
        """保存文档：返回元数据含 doc_id、filename、size、checksum 等"""
        store = DocumentStore()
        doc = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        assert doc is not None
        assert "doc_id" in doc
        assert doc["filename"] == sample_document["filename"]
        assert doc["format"] == sample_document["fmt"]
        assert doc["status"] == "uploaded"
        assert doc["size"] == len(sample_document["content"])
        assert doc["size_bytes"] == len(sample_document["content"])
        # _normalize_doc 添加 id 别名
        assert doc["id"] == doc["doc_id"]
        # checksum 应存在
        assert "checksum" in doc
        assert len(doc["checksum"]) == 64  # sha256 hex

    def test_save_with_custom_doc_id(self, temp_db_document_store, sample_document):
        """使用自定义 doc_id 保存文档"""
        store = DocumentStore()
        doc = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
            doc_id="custom-doc-001",
        )
        assert doc["doc_id"] == "custom-doc-001"
        assert doc["id"] == "custom-doc-001"

    def test_get_document(self, temp_db_document_store, sample_document):
        """按 doc_id 获取文档"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        doc = store.get(saved["doc_id"])
        assert doc is not None
        assert doc["doc_id"] == saved["doc_id"]
        assert doc["filename"] == sample_document["filename"]

    def test_get_nonexistent(self, temp_db_document_store):
        """获取不存在的文档返回 None"""
        store = DocumentStore()
        assert store.get("nonexistent-id") is None

    def test_get_by_id(self, temp_db_document_store, sample_document):
        """按内部整数 ID 获取文档"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        # _normalize_doc 将 id 覆盖为 doc_id 字符串，因此需通过 DB 获取内部整数 ID
        internal_id = temp_db_document_store.execute(
            "SELECT id FROM documents WHERE doc_id = ?", (saved["doc_id"],)
        ).fetchone()["id"]
        doc = store.get_by_id(internal_id)
        assert doc is not None
        assert doc["doc_id"] == saved["doc_id"]

    def test_get_by_id_nonexistent(self, temp_db_document_store):
        """按不存在的内部 ID 获取返回 None"""
        store = DocumentStore()
        assert store.get_by_id(99999) is None

    def test_list_documents(self, temp_db_document_store, sample_document):
        """列出文档：支持分页和格式/状态过滤"""
        store = DocumentStore()
        # 保存多个文档
        store.save("doc1.md", sample_document["content"], "markdown")
        store.save("doc2.txt", b"plain text content", "text")
        store.save("doc3.md", b"another markdown", "markdown")

        # 全部列出
        all_docs = store.list()
        assert len(all_docs) == 3

        # 按格式过滤
        md_docs = store.list(fmt="markdown")
        assert len(md_docs) == 2

        txt_docs = store.list(fmt="text")
        assert len(txt_docs) == 1

        # 按状态过滤
        uploaded = store.list(status="uploaded")
        assert len(uploaded) == 3

        # 分页
        paged = store.list(limit=2, offset=0)
        assert len(paged) == 2

    def test_list_empty(self, temp_db_document_store):
        """空数据库列出返回空列表"""
        store = DocumentStore()
        docs = store.list()
        assert docs == []

    def test_read_content(self, temp_db_document_store, sample_document):
        """读取文档原始内容"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        content = store.read_content(saved["doc_id"])
        assert content == sample_document["content"]

    def test_read_content_nonexistent(self, temp_db_document_store):
        """读取不存在文档的内容返回 None"""
        store = DocumentStore()
        assert store.read_content("nonexistent") is None

    def test_update_status(self, temp_db_document_store, sample_document):
        """更新文档状态"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )

        # 更新状态
        result = store.update_status(saved["doc_id"], "parsed", title="Nginx Guide")
        assert result is True

        doc = store.get(saved["doc_id"])
        assert doc["status"] == "parsed"
        assert doc["title"] == "Nginx Guide"

    def test_update_status_with_parse_result(self, temp_db_document_store, sample_document):
        """更新状态时附带 parse_result"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )

        parse_result = {"entities": 5, "relations": 3}
        store.update_status(saved["doc_id"], "extracted", parse_result=parse_result)

        doc = store.get(saved["doc_id"])
        assert doc["status"] == "extracted"
        import json

        pr = json.loads(doc["parse_result"])
        assert pr["entities"] == 5

    def test_update_status_nonexistent(self, temp_db_document_store):
        """更新不存在文档的状态返回 False"""
        store = DocumentStore()
        result = store.update_status("nonexistent", "parsed")
        assert result is False

    def test_delete_document(self, temp_db_document_store, sample_document):
        """删除文档：元数据 + 文件一并删除"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )

        result = store.delete(saved["doc_id"])
        assert result is True

        # 删除后无法获取
        assert store.get(saved["doc_id"]) is None

    def test_delete_nonexistent(self, temp_db_document_store):
        """删除不存在的文档返回 False"""
        store = DocumentStore()
        assert store.delete("nonexistent") is False

    def test_search(self, temp_db_document_store, sample_document):
        """按关键词搜索文档"""
        store = DocumentStore()
        store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        store.save("random_doc.txt", b"nothing relevant", "text")

        results = store.search("nginx")
        assert len(results) >= 1
        assert results[0]["filename"] == sample_document["filename"]

        results = store.search("nonexistent_keyword_xyz")
        assert len(results) == 0

    def test_get_stats(self, temp_db_document_store, sample_document):
        """获取文档统计信息"""
        store = DocumentStore()
        store.save("doc1.md", sample_document["content"], "markdown")
        store.save("doc2.txt", b"text", "text")

        stats = store.get_stats()
        assert stats["total"] == 2
        assert stats["total_size_mb"] >= 0  # 小文件可能四舍五入为 0
        assert len(stats["by_format"]) == 2
        assert len(stats["by_status"]) >= 1


# ────────── 重复检测 ──────────


class TestDocumentDeduplication:
    def test_dedup_same_content(self, temp_db_document_store, sample_document):
        """相同内容（相同 checksum）的文档不重复保存，返回已有文档"""
        store = DocumentStore()
        doc1 = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        doc2 = store.save(
            filename="duplicate_name.md",
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        # 应该返回同一个文档
        assert doc2["doc_id"] == doc1["doc_id"]
        assert doc2["checksum"] == doc1["checksum"]

    def test_different_content_not_deduped(self, temp_db_document_store, sample_document):
        """不同内容的文档正常保存"""
        store = DocumentStore()
        doc1 = store.save("doc1.md", sample_document["content"], "markdown")
        doc2 = store.save("doc2.md", b"completely different content", "markdown")
        assert doc1["doc_id"] != doc2["doc_id"]
        assert doc1["checksum"] != doc2["checksum"]


# ────────── Pipeline 状态管理 ──────────


class TestPipelineRun:
    def test_create_pipeline_run(self, temp_db_document_store, sample_document):
        """创建流水线执行记录"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )

        run_id = store.create_pipeline_run(saved["doc_id"])
        assert run_id.startswith("run-")
        assert len(run_id) > 4

        run = store.get_pipeline_run(run_id)
        assert run is not None
        assert run["doc_id"] == saved["doc_id"]
        assert run["status"] == "pending"
        assert len(run["steps"]) == 4
        # 初始步骤名称
        step_names = [s["name"] for s in run["steps"]]
        assert step_names == ["parse", "extract", "compile", "index"]

    def test_start_pipeline_run(self, temp_db_document_store, sample_document):
        """标记流水线开始执行"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        run_id = store.create_pipeline_run(saved["doc_id"])

        result = store.start_pipeline_run(run_id, "parse")
        assert result is True

        run = store.get_pipeline_run(run_id)
        assert run["status"] == "running"
        assert run["current_step"] == "parse"
        assert run["started_at"] is not None

    def test_start_nonexistent_run(self, temp_db_document_store):
        """启动不存在的流水线返回 False"""
        store = DocumentStore()
        assert store.start_pipeline_run("nonexistent-run", "parse") is False

    def test_update_pipeline_step(self, temp_db_document_store, sample_document):
        """更新流水线中某个步骤的状态"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        run_id = store.create_pipeline_run(saved["doc_id"])
        store.start_pipeline_run(run_id, "parse")

        # 更新 parse 步骤
        result = store.update_pipeline_step(
            run_id, "parse", "done", duration_ms=150
        )
        assert result is True

        run = store.get_pipeline_run(run_id)
        parse_step = next(s for s in run["steps"] if s["name"] == "parse")
        assert parse_step["status"] == "done"
        assert parse_step["duration_ms"] == 150

    def test_update_pipeline_step_with_error(self, temp_db_document_store, sample_document):
        """更新步骤状态时附带错误信息"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        run_id = store.create_pipeline_run(saved["doc_id"])
        store.start_pipeline_run(run_id, "parse")

        store.update_pipeline_step(
            run_id, "parse", "error", error="Parse failed: invalid format"
        )
        run = store.get_pipeline_run(run_id)
        parse_step = next(s for s in run["steps"] if s["name"] == "parse")
        assert parse_step["status"] == "error"
        assert parse_step["error"] == "Parse failed: invalid format"

    def test_update_nonexistent_step(self, temp_db_document_store, sample_document):
        """更新不存在流水线的步骤返回 False"""
        store = DocumentStore()
        assert store.update_pipeline_step("nonexistent", "parse", "done") is False

    def test_finish_pipeline_run(self, temp_db_document_store, sample_document):
        """标记流水线完成"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        run_id = store.create_pipeline_run(saved["doc_id"])
        store.start_pipeline_run(run_id, "parse")

        result = store.finish_pipeline_run(run_id, "done")
        assert result is True

        run = store.get_pipeline_run(run_id)
        assert run["status"] == "done"
        assert run["finished_at"] is not None

    def test_fail_pipeline_run(self, temp_db_document_store, sample_document):
        """标记流水线失败"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )
        run_id = store.create_pipeline_run(saved["doc_id"])
        store.start_pipeline_run(run_id, "parse")

        result = store.fail_pipeline_run(run_id, "Extraction timeout")
        assert result is True

        run = store.get_pipeline_run(run_id)
        assert run["status"] == "error"
        assert run["error_message"] == "Extraction timeout"
        assert run["finished_at"] is not None

    def test_get_pipeline_run_nonexistent(self, temp_db_document_store):
        """获取不存在的流水线记录返回 None"""
        store = DocumentStore()
        assert store.get_pipeline_run("nonexistent-run") is None

    def test_get_latest_pipeline_run(self, temp_db_document_store, sample_document):
        """获取文档最近的流水线执行记录"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )

        store.create_pipeline_run(saved["doc_id"])
        run_id2 = store.create_pipeline_run(saved["doc_id"])

        latest = store.get_latest_pipeline_run(saved["doc_id"])
        assert latest is not None
        assert latest["run_id"] == run_id2  # 最新创建的

    def test_get_latest_pipeline_run_none(self, temp_db_document_store):
        """无流水线记录的文档返回 None"""
        store = DocumentStore()
        assert store.get_latest_pipeline_run("no-doc") is None

    def test_pipeline_full_lifecycle(self, temp_db_document_store, sample_document):
        """完整流水线生命周期：创建 → 启动 → 逐步完成 → 结束"""
        store = DocumentStore()
        saved = store.save(
            filename=sample_document["filename"],
            content=sample_document["content"],
            fmt=sample_document["fmt"],
        )

        # 创建
        run_id = store.create_pipeline_run(saved["doc_id"])
        assert run_id is not None

        # 启动
        store.start_pipeline_run(run_id, "parse")
        run = store.get_pipeline_run(run_id)
        assert run["status"] == "running"

        # 逐步完成
        for step_name in ["parse", "extract", "compile", "index"]:
            store.update_pipeline_step(run_id, step_name, "done", duration_ms=100)

        # 完成
        store.finish_pipeline_run(run_id)
        run = store.get_pipeline_run(run_id)
        assert run["status"] == "done"
        for step in run["steps"]:
            assert step["status"] == "done"
            assert step["duration_ms"] == 100
