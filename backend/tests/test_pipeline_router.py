"""pipeline_router API 端点测试

验证内容：
1. GET /pipeline/runs — 列出运行记录，支持 doc_id/status 过滤
2. GET /pipeline/runs/{run_id} — 获取单次运行详情
3. GET /pipeline/runs/{run_id} — 不存在的 run_id 返回 404
4. GET /pipeline/doc/{doc_id}/latest — 获取最近运行
5. GET /pipeline/doc/{doc_id}/latest — 无运行记录返回 404
6. GET /pipeline/runs/{run_id}/stages — 列出阶段产物元数据
7. GET /pipeline/runs/{run_id}/stages/{stage} — 查看阶段输入+输出
8. GET /pipeline/runs/{run_id}/stages/{stage}/{direction} — 查看单一方向产物
9. POST /pipeline/doc/{doc_id}/reprocess — 无效 stage 返回 400
10. POST /pipeline/doc/{doc_id}/reprocess — 文档不存在返回 404
11. DELETE /pipeline/runs/{run_id} — 删除产物
12. DELETE /pipeline/doc/{doc_id}/artifacts — 删除文档所有产物
13. 无效 stage / direction 参数返回 400

使用临时 SQLite DB（通过 monkeypatch）避免污染默认 DB。
"""
from __future__ import annotations

import os

# 确保测试期间关闭认证
os.environ.setdefault("OPSKG_API_TOKEN", "")

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import document_store as ds_module
from app.storage import pipeline_tracker as pt_module

client = TestClient(app)


# ────────── 临时 DB fixture ──────────


@pytest.fixture
def temp_pipeline_db(monkeypatch, tmp_path):
    """为 pipeline 相关测试创建临时 SQLite 数据库（文件式）

    使用文件 DB 而非 :memory:，因为 FastAPI TestClient 在独立线程运行，
    SQLite 默认禁止跨线程使用同一连接。
    替换 document_store._get_db 和 pipeline_tracker._get_db 返回同一连接，
    确保 DocumentStore 与 PipelineTracker 共享同一 schema。
    """
    db_path = tmp_path / "test_pipeline_router.db"
    # check_same_thread=False 允许 TestClient 的子线程使用同一连接
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ds_module._init_schema(conn)

    def _patched_get_db() -> sqlite3.Connection:
        return conn

    monkeypatch.setattr(ds_module, "_get_db", _patched_get_db)
    monkeypatch.setattr(pt_module, "_get_db", _patched_get_db)
    monkeypatch.setattr(ds_module, "_ensure_dirs", lambda: None)
    monkeypatch.setattr(ds_module, "UPLOADS_DIR", tmp_path)

    return conn


def _create_run(conn: sqlite3.Connection, doc_id: str = "doc-001", run_id: str = "run-001") -> str:
    """在 pipeline_runs 表插入运行记录"""
    now = datetime.now(timezone.utc).isoformat()
    steps = json.dumps([
        {"name": "parse", "label": "解析", "status": "done"},
        {"name": "extract", "label": "知识抽取", "status": "done"},
        {"name": "compile", "label": "编译 Wiki", "status": "running"},
        {"name": "index", "label": "重建索引", "status": "pending"},
    ])
    conn.execute(
        """INSERT INTO pipeline_runs (run_id, doc_id, status, current_step, steps_json, created_at)
           VALUES (?, ?, 'running', 'compile', ?, ?)""",
        (run_id, doc_id, steps, now),
    )
    conn.commit()
    return run_id


def _create_doc(conn: sqlite3.Connection, doc_id: str = "doc-001") -> str:
    """在 documents 表插入文档记录"""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO documents
           (doc_id, filename, format, ext, checksum, stored_path, size_bytes,
            title, status, metadata, created_at, updated_at)
           VALUES (?, 'test.md', 'markdown', '.md', 'sha256:abc', '/tmp/test.md',
                   100, 'Test', 'uploaded', '{}', ?, ?)""",
        (doc_id, now, now),
    )
    conn.commit()
    return doc_id


def _save_artifact(
    conn: sqlite3.Connection,
    run_id: str,
    doc_id: str,
    stage: str,
    direction: str,
    payload: dict,
) -> None:
    """直接通过 SQL 插入 artifact（绕过 PipelineTracker 的 monkeypatch）"""
    now = datetime.now(timezone.utc).isoformat()
    payload_str = json.dumps(payload, ensure_ascii=False)
    conn.execute(
        """INSERT INTO pipeline_artifacts
           (run_id, doc_id, stage, direction, payload, payload_size, mime_type, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'application/json', ?)""",
        (run_id, doc_id, stage, direction, payload_str, len(payload_str), now),
    )
    conn.commit()


# ────────── 1. GET /pipeline/runs ──────────


class TestListPipelineRuns:
    def test_list_runs_empty(self, temp_pipeline_db):
        """无运行记录时返回空列表"""
        r = client.get("/pipeline/runs")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["runs"] == []

    def test_list_runs_returns_all(self, temp_pipeline_db):
        """返回所有运行记录"""
        _create_run(temp_pipeline_db, doc_id="doc-A", run_id="run-A")
        _create_run(temp_pipeline_db, doc_id="doc-B", run_id="run-B")

        r = client.get("/pipeline/runs")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert len(data["runs"]) == 2

    def test_list_runs_filter_by_doc_id(self, temp_pipeline_db):
        """按 doc_id 过滤"""
        _create_run(temp_pipeline_db, doc_id="doc-A", run_id="run-A")
        _create_run(temp_pipeline_db, doc_id="doc-B", run_id="run-B")

        r = client.get("/pipeline/runs", params={"doc_id": "doc-A"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["runs"][0]["doc_id"] == "doc-A"

    def test_list_runs_filter_by_status(self, temp_pipeline_db):
        """按 status 过滤"""
        _create_run(temp_pipeline_db, doc_id="doc-A", run_id="run-A")
        # 创建一个 done 状态的 run
        now = datetime.now(timezone.utc).isoformat()
        temp_pipeline_db.execute(
            """INSERT INTO pipeline_runs (run_id, doc_id, status, steps_json, created_at)
               VALUES ('run-done', 'doc-C', 'done', '[]', ?)""",
            (now,),
        )
        temp_pipeline_db.commit()

        r = client.get("/pipeline/runs", params={"status": "done"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["runs"][0]["run_id"] == "run-done"

    def test_list_runs_pagination(self, temp_pipeline_db):
        """分页参数 limit/offset"""
        for i in range(5):
            _create_run(temp_pipeline_db, doc_id=f"doc-{i}", run_id=f"run-{i}")

        r = client.get("/pipeline/runs", params={"limit": 2, "offset": 0})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 5
        assert len(data["runs"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 0


# ────────── 2. GET /pipeline/runs/{run_id} ──────────


class TestGetPipelineRun:
    def test_get_run_returns_details(self, temp_pipeline_db):
        """获取运行详情（含 artifacts 字段）"""
        run_id = _create_run(temp_pipeline_db)

        r = client.get(f"/pipeline/runs/{run_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["run_id"] == run_id
        assert data["doc_id"] == "doc-001"
        assert data["status"] == "running"
        assert "steps" in data
        assert "artifacts" in data
        assert isinstance(data["artifacts"], list)

    def test_get_run_not_found(self, temp_pipeline_db):
        """不存在的 run_id 返回 404"""
        r = client.get("/pipeline/runs/nonexistent-run")
        assert r.status_code == 404


# ────────── 3. GET /pipeline/doc/{doc_id}/latest ──────────


class TestGetLatestRun:
    def test_get_latest_run(self, temp_pipeline_db):
        """获取文档最近的运行"""
        _create_run(temp_pipeline_db, doc_id="doc-001", run_id="run-old")
        # 等一下再创建新的，时间戳不同
        import time
        time.sleep(0.01)
        now = datetime.now(timezone.utc).isoformat()
        temp_pipeline_db.execute(
            """INSERT INTO pipeline_runs (run_id, doc_id, status, steps_json, created_at)
               VALUES ('run-new', 'doc-001', 'done', '[]', ?)""",
            (now,),
        )
        temp_pipeline_db.commit()

        r = client.get("/pipeline/doc/doc-001/latest")
        assert r.status_code == 200
        data = r.json()
        assert data["run_id"] == "run-new"

    def test_get_latest_run_not_found(self, temp_pipeline_db):
        """无运行记录返回 404"""
        r = client.get("/pipeline/doc/no-such-doc/latest")
        assert r.status_code == 404


# ────────── 4. GET /pipeline/runs/{run_id}/stages ──────────


class TestListStageArtifacts:
    def test_list_stages_empty(self, temp_pipeline_db):
        """运行存在但无产物时返回空 artifacts"""
        run_id = _create_run(temp_pipeline_db)

        r = client.get(f"/pipeline/runs/{run_id}/stages")
        assert r.status_code == 200
        data = r.json()
        assert data["run_id"] == run_id
        assert data["artifacts"] == []

    def test_list_stages_returns_artifacts(self, temp_pipeline_db):
        """返回所有阶段产物元数据"""
        run_id = _create_run(temp_pipeline_db)
        _save_artifact(temp_pipeline_db, run_id, "doc-001", "parse", "input", {"a": 1})
        _save_artifact(temp_pipeline_db, run_id, "doc-001", "parse", "output", {"b": 2})

        r = client.get(f"/pipeline/runs/{run_id}/stages")
        assert r.status_code == 200
        data = r.json()
        assert len(data["artifacts"]) == 2
        for a in data["artifacts"]:
            assert "payload" not in a  # 元数据不含 payload
            assert a["run_id"] == run_id

    def test_list_stages_run_not_found(self, temp_pipeline_db):
        """不存在的 run_id 返回 404"""
        r = client.get("/pipeline/runs/nonexistent/stages")
        assert r.status_code == 404


# ────────── 5. GET /pipeline/runs/{run_id}/stages/{stage} ──────────


class TestGetStageData:
    def test_get_stage_data_returns_input_and_output(self, temp_pipeline_db):
        """查看阶段输入+输出"""
        run_id = _create_run(temp_pipeline_db)
        _save_artifact(temp_pipeline_db, run_id, "doc-001", "parse", "input", {"who": "input"})
        _save_artifact(temp_pipeline_db, run_id, "doc-001", "parse", "output", {"who": "output"})

        r = client.get(f"/pipeline/runs/{run_id}/stages/parse")
        assert r.status_code == 200
        data = r.json()
        assert data["stage"] == "parse"
        assert data["input"] == {"who": "input"}
        assert data["output"] == {"who": "output"}
        assert data["input_meta"]["stage"] == "parse"
        assert data["input_meta"]["direction"] == "input"
        assert data["output_meta"]["stage"] == "parse"
        assert data["output_meta"]["direction"] == "output"

    def test_get_stage_data_only_input(self, temp_pipeline_db):
        """仅有 input 时也返回（output 为 None）"""
        run_id = _create_run(temp_pipeline_db)
        _save_artifact(temp_pipeline_db, run_id, "doc-001", "extract", "input", {"a": 1})

        r = client.get(f"/pipeline/runs/{run_id}/stages/extract")
        assert r.status_code == 200
        data = r.json()
        assert data["input"] == {"a": 1}
        assert data["output"] is None

    def test_get_stage_data_invalid_stage(self, temp_pipeline_db):
        """无效 stage 返回 400"""
        run_id = _create_run(temp_pipeline_db)

        r = client.get(f"/pipeline/runs/{run_id}/stages/invalid_stage")
        assert r.status_code == 400
        assert "无效阶段" in r.json()["detail"]

    def test_get_stage_data_no_artifacts_returns_404(self, temp_pipeline_db):
        """无产物数据返回 404"""
        run_id = _create_run(temp_pipeline_db)

        r = client.get(f"/pipeline/runs/{run_id}/stages/parse")
        assert r.status_code == 404
        assert "无产物数据" in r.json()["detail"]

    def test_get_stage_data_run_not_found(self, temp_pipeline_db):
        """run 不存在返回 404"""
        r = client.get("/pipeline/runs/nonexistent/stages/parse")
        assert r.status_code == 404


# ────────── 6. GET /pipeline/runs/{run_id}/stages/{stage}/{direction} ──────────


class TestGetStageArtifact:
    def test_get_input_artifact(self, temp_pipeline_db):
        """查看单一 input 产物"""
        run_id = _create_run(temp_pipeline_db)
        _save_artifact(temp_pipeline_db, run_id, "doc-001", "parse", "input", {"data": "in"})

        r = client.get(f"/pipeline/runs/{run_id}/stages/parse/input")
        assert r.status_code == 200
        data = r.json()
        assert data["direction"] == "input"
        assert data["payload"] == {"data": "in"}
        assert data["meta"]["stage"] == "parse"

    def test_get_output_artifact(self, temp_pipeline_db):
        """查看单一 output 产物"""
        run_id = _create_run(temp_pipeline_db)
        _save_artifact(temp_pipeline_db, run_id, "doc-001", "parse", "output", {"data": "out"})

        r = client.get(f"/pipeline/runs/{run_id}/stages/parse/output")
        assert r.status_code == 200
        data = r.json()
        assert data["direction"] == "output"
        assert data["payload"] == {"data": "out"}

    def test_get_artifact_invalid_direction(self, temp_pipeline_db):
        """无效 direction 返回 400"""
        run_id = _create_run(temp_pipeline_db)

        r = client.get(f"/pipeline/runs/{run_id}/stages/parse/invalid")
        assert r.status_code == 400
        assert "无效方向" in r.json()["detail"]

    def test_get_artifact_invalid_stage(self, temp_pipeline_db):
        """无效 stage 返回 400"""
        run_id = _create_run(temp_pipeline_db)

        r = client.get(f"/pipeline/runs/{run_id}/stages/bad_stage/input")
        assert r.status_code == 400

    def test_get_artifact_not_found(self, temp_pipeline_db):
        """artifact 不存在返回 404"""
        run_id = _create_run(temp_pipeline_db)

        r = client.get(f"/pipeline/runs/{run_id}/stages/parse/output")
        assert r.status_code == 404


# ────────── 7. POST /pipeline/doc/{doc_id}/reprocess ──────────


class TestReprocessDoc:
    def test_reprocess_invalid_stage(self, temp_pipeline_db):
        """无效 start_from_stage 返回 400"""
        _create_doc(temp_pipeline_db, "doc-001")

        r = client.post(
            "/pipeline/doc/doc-001/reprocess",
            json={"start_from_stage": "invalid_stage"},
        )
        assert r.status_code == 400
        assert "无效 start_from_stage" in r.json()["detail"]

    def test_reprocess_doc_not_found(self, temp_pipeline_db):
        """文档不存在返回 404"""
        r = client.post(
            "/pipeline/doc/nonexistent/reprocess",
            json={"start_from_stage": "parse"},
        )
        assert r.status_code == 404
        assert "文档不存在" in r.json()["detail"]

    def test_reprocess_no_history_returns_400(self, temp_pipeline_db):
        """从 extract 阶段重处理但无历史 run 返回 400"""
        _create_doc(temp_pipeline_db, "doc-001")

        r = client.post(
            "/pipeline/doc/doc-001/reprocess",
            json={"start_from_stage": "extract"},
        )
        assert r.status_code == 400
        assert "无历史运行记录" in r.json()["detail"]


# ────────── 8. POST /pipeline/runs/{run_id}/reprocess ──────────


class TestReprocessRun:
    def test_reprocess_run_invalid_stage(self, temp_pipeline_db):
        """无效 stage 返回 400"""
        run_id = _create_run(temp_pipeline_db)

        r = client.post(
            f"/pipeline/runs/{run_id}/reprocess",
            json={"start_from_stage": "invalid"},
        )
        assert r.status_code == 400

    def test_reprocess_run_not_found(self, temp_pipeline_db):
        """run 不存在返回 404"""
        r = client.post(
            "/pipeline/runs/nonexistent/reprocess",
            json={"start_from_stage": "parse"},
        )
        assert r.status_code == 404


# ────────── 9. DELETE /pipeline/runs/{run_id} ──────────


class TestDeleteRun:
    def test_delete_run_removes_artifacts(self, temp_pipeline_db):
        """删除运行的产物"""
        run_id = _create_run(temp_pipeline_db)
        _save_artifact(temp_pipeline_db, run_id, "doc-001", "parse", "input", {"a": 1})
        _save_artifact(temp_pipeline_db, run_id, "doc-001", "parse", "output", {"b": 2})

        r = client.delete(f"/pipeline/runs/{run_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["run_id"] == run_id
        assert data["artifacts_deleted"] == 2

        # 验证 artifacts 已删除
        r2 = client.get(f"/pipeline/runs/{run_id}/stages")
        assert r2.status_code == 200
        assert r2.json()["artifacts"] == []

    def test_delete_run_no_artifacts(self, temp_pipeline_db):
        """删除无产物的 run 返回 0"""
        run_id = _create_run(temp_pipeline_db)

        r = client.delete(f"/pipeline/runs/{run_id}")
        assert r.status_code == 200
        assert r.json()["artifacts_deleted"] == 0


# ────────── 10. DELETE /pipeline/doc/{doc_id}/artifacts ──────────


class TestDeleteDocArtifacts:
    def test_delete_doc_artifacts(self, temp_pipeline_db):
        """删除文档所有产物"""
        _create_run(temp_pipeline_db, doc_id="doc-001", run_id="run-001")
        _create_run(temp_pipeline_db, doc_id="doc-002", run_id="run-002")
        _save_artifact(temp_pipeline_db, "run-001", "doc-001", "parse", "input", {"a": 1})
        _save_artifact(temp_pipeline_db, "run-002", "doc-002", "parse", "input", {"b": 2})

        r = client.delete("/pipeline/doc/doc-001/artifacts")
        assert r.status_code == 200
        assert r.json()["artifacts_deleted"] == 1

        # doc-002 的产物仍在
        r2 = client.get("/pipeline/runs/run-002/stages")
        assert r2.status_code == 200
        assert len(r2.json()["artifacts"]) == 1

    def test_delete_doc_artifacts_no_artifacts(self, temp_pipeline_db):
        """删除无产物的文档返回 0"""
        r = client.delete("/pipeline/doc/no-such-doc/artifacts")
        assert r.status_code == 200
        assert r.json()["artifacts_deleted"] == 0
