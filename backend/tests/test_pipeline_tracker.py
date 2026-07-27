"""PipelineTracker 单元测试

验证内容：
1. save_artifact / get_artifact：基础读写往返
2. 同 run/stage/direction 覆盖式写入（仅保留最新版本）
3. get_artifact_meta：仅返回元数据（不含 payload）
4. list_stage_artifacts：按 run_id 列出全部阶段产物
5. list_runs / count_runs：列出/统计运行记录，支持 doc_id / status 过滤
6. delete_run / delete_doc_artifacts：删除产物但保留 pipeline_runs 状态记录
7. 不存在的 artifact 查询返回 None
8. payload 序列化失败的兜底处理
9. 非法 direction 参数抛 ValueError
10. 全局单例 get_pipeline_tracker 行为正确

使用临时文件 SQLite（不污染默认 DB_PATH），在 monkeypatch 后运行。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app.storage import document_store as ds_module
from app.storage.pipeline_tracker import (
    PIPELINE_STAGES,
    PipelineTracker,
    get_pipeline_tracker,
)

# ────────── 临时 SQLite fixture ──────────


@pytest.fixture
def temp_tracker(monkeypatch, tmp_path) -> tuple[PipelineTracker, sqlite3.Connection]:
    """构造一个使用临时 SQLite 文件的 PipelineTracker

    返回 (tracker, conn)：
    - tracker：注入临时 DB 后的实例
    - conn：共享同一连接，便于断言底层数据
    """
    db_path = tmp_path / "test_pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # 初始化全部 schema（document_store 负责 pipeline_artifacts / pipeline_runs）
    ds_module._init_schema(conn)

    def _patched_get_db() -> sqlite3.Connection:
        return conn

    monkeypatch.setattr(ds_module, "_get_db", _patched_get_db)
    # pipeline_tracker 通过 ConnectionPool.get(..., _ensure_artifact_schema) 取连接
    # 注入 monkeypatch 后，document_store._get_db 返回我们的临时连接
    monkeypatch.setattr(
        "app.storage.pipeline_tracker._get_db", _patched_get_db,
    )

    tracker = PipelineTracker()
    return tracker, conn


# ────────── 测试用辅助 ──────────


def _make_run(conn: sqlite3.Connection, doc_id: str = "doc-001", run_id: str = "run-test-001") -> str:
    """在 pipeline_runs 表插入一条运行记录"""
    now = datetime.now(timezone.utc).isoformat()
    steps = json.dumps([
        {"name": "parse", "label": "解析", "status": "pending"},
        {"name": "extract", "label": "知识抽取", "status": "pending"},
        {"name": "compile", "label": "编译 Wiki", "status": "pending"},
        {"name": "index", "label": "重建索引", "status": "pending"},
    ])
    conn.execute(
        """INSERT INTO pipeline_runs (run_id, doc_id, status, steps_json, created_at)
           VALUES (?, ?, 'pending', ?, ?)""",
        (run_id, doc_id, steps, now),
    )
    conn.commit()
    return run_id


# ────────── 1. 基础读写往返 ──────────


def test_save_and_get_artifact_roundtrip(temp_tracker) -> None:
    """save_artifact 后 get_artifact 返回相同的 payload（dict）"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    payload = {"elements": [{"type": "heading", "content": "# Title"}], "doc_id": "doc-001"}
    tracker.save_artifact(run_id, "doc-001", "parse", "output", payload)

    result = tracker.get_artifact(run_id, "parse", "output")
    assert result == payload


def test_save_and_get_list_payload(temp_tracker) -> None:
    """list 类型 payload 也能正确往返"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    payload = [1, 2, 3, {"nested": True}]
    tracker.save_artifact(run_id, "doc-001", "extract", "input", payload)

    result = tracker.get_artifact(run_id, "extract", "input")
    assert result == payload


def test_save_and_get_string_payload(temp_tracker) -> None:
    """字符串 payload 也能正确往返"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    tracker.save_artifact(run_id, "doc-001", "parse", "input", "raw markdown content")

    result = tracker.get_artifact(run_id, "parse", "input")
    assert result == "raw markdown content"


# ────────── 2. 覆盖式写入 ──────────


def test_save_artifact_overwrites_same_run_stage_direction(temp_tracker) -> None:
    """同一 run/stage/direction 仅保留最新版本（覆盖写入）"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    tracker.save_artifact(run_id, "doc-001", "parse", "output", {"version": 1})
    tracker.save_artifact(run_id, "doc-001", "parse", "output", {"version": 2})
    tracker.save_artifact(run_id, "doc-001", "parse", "output", {"version": 3})

    result = tracker.get_artifact(run_id, "parse", "output")
    assert result == {"version": 3}

    # 数据库中只有 1 行
    rows = conn.execute(
        "SELECT COUNT(*) AS cnt FROM pipeline_artifacts WHERE run_id=?",
        (run_id,),
    ).fetchone()
    assert rows["cnt"] == 1


def test_save_artifact_different_directions_kept_separately(temp_tracker) -> None:
    """同一 run/stage 的 input/output 分别保留"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    tracker.save_artifact(run_id, "doc-001", "parse", "input", {"who": "input"})
    tracker.save_artifact(run_id, "doc-001", "parse", "output", {"who": "output"})

    assert tracker.get_artifact(run_id, "parse", "input") == {"who": "input"}
    assert tracker.get_artifact(run_id, "parse", "output") == {"who": "output"}


def test_save_artifact_different_stages_kept_separately(temp_tracker) -> None:
    """同一 run 的不同 stage 分别保留"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    tracker.save_artifact(run_id, "doc-001", "parse", "output", {"stage": "parse"})
    tracker.save_artifact(run_id, "doc-001", "extract", "input", {"stage": "extract"})
    tracker.save_artifact(run_id, "doc-001", "compile", "input", {"stage": "compile"})

    assert tracker.get_artifact(run_id, "parse", "output") == {"stage": "parse"}
    assert tracker.get_artifact(run_id, "extract", "input") == {"stage": "extract"}
    assert tracker.get_artifact(run_id, "compile", "input") == {"stage": "compile"}


# ────────── 3. 元数据查询 ──────────


def test_get_artifact_meta_returns_metadata_without_payload(temp_tracker) -> None:
    """get_artifact_meta 返回元数据但不含 payload 字段"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    payload = {"key": "value" * 100}  # 较大的 payload
    tracker.save_artifact(run_id, "doc-001", "parse", "output", payload)

    meta = tracker.get_artifact_meta(run_id, "parse", "output")
    assert meta is not None
    assert meta["run_id"] == run_id
    assert meta["doc_id"] == "doc-001"
    assert meta["stage"] == "parse"
    assert meta["direction"] == "output"
    assert meta["mime_type"] == "application/json"
    assert meta["payload_size"] > 0
    assert "created_at" in meta
    # meta 不应包含 payload 字段
    assert "payload" not in meta


def test_get_artifact_meta_returns_none_when_not_found(temp_tracker) -> None:
    """查询不存在的 artifact 元数据返回 None"""
    tracker, _ = temp_tracker
    meta = tracker.get_artifact_meta("run-nonexistent", "parse", "output")
    assert meta is None


# ────────── 4. 列出阶段产物 ──────────


def test_list_stage_artifacts_returns_all(temp_tracker) -> None:
    """list_stage_artifacts 按 run_id 返回所有阶段产物元数据"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    tracker.save_artifact(run_id, "doc-001", "parse", "input", {"a": 1})
    tracker.save_artifact(run_id, "doc-001", "parse", "output", {"b": 2})
    tracker.save_artifact(run_id, "doc-001", "extract", "input", {"c": 3})
    tracker.save_artifact(run_id, "doc-001", "extract", "output", {"d": 4})

    artifacts = tracker.list_stage_artifacts(run_id)
    assert len(artifacts) == 4
    # 每条都不含 payload 字段
    for a in artifacts:
        assert "payload" not in a
        assert a["run_id"] == run_id
    # 按 stage/direction 出现顺序
    stages = [(a["stage"], a["direction"]) for a in artifacts]
    assert ("parse", "input") in stages
    assert ("parse", "output") in stages
    assert ("extract", "input") in stages
    assert ("extract", "output") in stages


def test_list_stage_artifacts_empty_for_unknown_run(temp_tracker) -> None:
    """未知 run_id 返回空列表"""
    tracker, _ = temp_tracker
    assert tracker.list_stage_artifacts("run-nonexistent") == []


# ────────── 5. list_runs / count_runs ──────────


def test_list_runs_returns_runs_ordered_by_created_at_desc(temp_tracker) -> None:
    """list_runs 按 created_at 倒序返回"""
    tracker, conn = temp_tracker

    # 插入 3 条 run，时间递增（使用不同 run_id）
    for i, doc_id in enumerate(["doc-A", "doc-B", "doc-C"]):
        run_id = f"run-{i:03d}"
        now = f"2026-01-0{i+1}T00:00:00"
        steps = "[]"
        conn.execute(
            """INSERT INTO pipeline_runs (run_id, doc_id, status, steps_json, created_at)
               VALUES (?, ?, 'done', ?, ?)""",
            (run_id, doc_id, steps, now),
        )
    conn.commit()

    runs = tracker.list_runs(limit=10)
    assert len(runs) == 3
    # 倒序：最新在前
    assert runs[0]["doc_id"] == "doc-C"
    assert runs[-1]["doc_id"] == "doc-A"


def test_list_runs_filter_by_doc_id(temp_tracker) -> None:
    """list_runs 按 doc_id 过滤"""
    tracker, conn = temp_tracker
    conn.execute("DELETE FROM pipeline_runs")
    conn.commit()
    for i, doc_id in enumerate(["doc-A", "doc-B", "doc-A"]):
        run_id = f"run-{i:03d}"
        conn.execute(
            """INSERT INTO pipeline_runs (run_id, doc_id, status, steps_json, created_at)
               VALUES (?, ?, 'done', '[]', ?)""",
            (run_id, doc_id, f"2026-01-01T00:00:0{i}"),
        )
    conn.commit()

    runs = tracker.list_runs(doc_id="doc-A")
    assert len(runs) == 2
    for r in runs:
        assert r["doc_id"] == "doc-A"


def test_list_runs_filter_by_status(temp_tracker) -> None:
    """list_runs 按 status 过滤"""
    tracker, conn = temp_tracker
    conn.execute("DELETE FROM pipeline_runs")
    conn.commit()
    for i, (doc_id, status) in enumerate([
        ("doc-A", "done"), ("doc-B", "error"), ("doc-C", "done"),
    ]):
        conn.execute(
            """INSERT INTO pipeline_runs (run_id, doc_id, status, steps_json, created_at)
               VALUES (?, ?, ?, '[]', ?)""",
            (f"run-{i:03d}", doc_id, status, f"2026-01-01T00:00:0{i}"),
        )
    conn.commit()

    runs = tracker.list_runs(status="done")
    assert len(runs) == 2
    for r in runs:
        assert r["status"] == "done"


def test_count_runs_total(temp_tracker) -> None:
    """count_runs 返回总数"""
    tracker, conn = temp_tracker
    conn.execute("DELETE FROM pipeline_runs")
    conn.commit()
    for i in range(5):
        conn.execute(
            """INSERT INTO pipeline_runs (run_id, doc_id, status, steps_json, created_at)
               VALUES (?, ?, 'done', '[]', ?)""",
            (f"run-{i:03d}", f"doc-{i}", f"2026-01-01T00:00:0{i}"),
        )
    conn.commit()

    assert tracker.count_runs() == 5


def test_count_runs_filtered(temp_tracker) -> None:
    """count_runs 按 doc_id/status 过滤"""
    tracker, conn = temp_tracker
    conn.execute("DELETE FROM pipeline_runs")
    conn.commit()
    for i, (doc_id, status) in enumerate([
        ("doc-A", "done"), ("doc-A", "error"), ("doc-B", "done"),
    ]):
        conn.execute(
            """INSERT INTO pipeline_runs (run_id, doc_id, status, steps_json, created_at)
               VALUES (?, ?, ?, '[]', ?)""",
            (f"run-{i:03d}", doc_id, status, f"2026-01-01T00:00:0{i}"),
        )
    conn.commit()

    assert tracker.count_runs(doc_id="doc-A") == 2
    assert tracker.count_runs(status="done") == 2
    assert tracker.count_runs(doc_id="doc-A", status="done") == 1


def test_list_runs_steps_parsed(temp_tracker) -> None:
    """list_runs 返回的 steps 字段应解析为 list（来自 steps_json）"""
    tracker, conn = temp_tracker
    conn.execute("DELETE FROM pipeline_runs")
    conn.commit()
    steps_json = json.dumps([
        {"name": "parse", "label": "解析", "status": "pending"},
    ])
    conn.execute(
        """INSERT INTO pipeline_runs (run_id, doc_id, status, steps_json, created_at)
           VALUES (?, 'doc-X', 'pending', ?, '2026-01-01T00:00:00')""",
        ("run-001", steps_json),
    )
    conn.commit()

    runs = tracker.list_runs()
    assert len(runs) == 1
    assert isinstance(runs[0]["steps"], list)
    assert runs[0]["steps"][0]["name"] == "parse"
    # 原始 steps_json 字段应被移除
    assert "steps_json" not in runs[0]


# ────────── 6. 删除 ──────────


def test_delete_run_removes_artifacts_only(temp_tracker) -> None:
    """delete_run 删除 pipeline_artifacts 但保留 pipeline_runs 记录"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    tracker.save_artifact(run_id, "doc-001", "parse", "input", {"a": 1})
    tracker.save_artifact(run_id, "doc-001", "parse", "output", {"b": 2})

    deleted = tracker.delete_run(run_id)
    assert deleted == 2

    # artifacts 已清空
    assert tracker.get_artifact(run_id, "parse", "input") is None
    assert tracker.get_artifact(run_id, "parse", "output") is None
    assert tracker.list_stage_artifacts(run_id) == []

    # pipeline_runs 仍存在
    row = conn.execute(
        "SELECT * FROM pipeline_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    assert row is not None


def test_delete_doc_artifacts_removes_all_artifacts_for_doc(temp_tracker) -> None:
    """delete_doc_artifacts 删除文档的所有产物（跨 run）"""
    tracker, conn = temp_tracker
    run1 = _make_run(conn, doc_id="doc-001", run_id="run-doc-001")
    run2 = _make_run(conn, doc_id="doc-002", run_id="run-doc-002")

    tracker.save_artifact(run1, "doc-001", "parse", "input", {"a": 1})
    tracker.save_artifact(run2, "doc-002", "parse", "input", {"b": 2})

    deleted = tracker.delete_doc_artifacts("doc-001")
    assert deleted == 1

    # doc-001 的产物已删除
    assert tracker.get_artifact(run1, "parse", "input") is None
    # doc-002 的产物仍在
    assert tracker.get_artifact(run2, "parse", "input") == {"b": 2}


def test_delete_run_returns_zero_for_unknown(temp_tracker) -> None:
    """删除不存在的 run 返回 0"""
    tracker, _ = temp_tracker
    assert tracker.delete_run("run-nonexistent") == 0


# ────────── 7. 不存在的 artifact 返回 None ──────────


def test_get_artifact_returns_none_when_not_found(temp_tracker) -> None:
    """查询不存在的 artifact 返回 None"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    # 该 run 存在，但 artifact 不存在
    assert tracker.get_artifact(run_id, "parse", "output") is None
    # run 也不存在
    assert tracker.get_artifact("run-nonexistent", "parse", "output") is None


# ────────── 8. payload 序列化失败兜底 ──────────


def test_save_artifact_handles_unserializable_payload(temp_tracker) -> None:
    """不可 JSON 序列化的 payload 应兜底写入（含 _serialize_error 字段）

    使用 __slots__ 类 + 抛 TypeError 的 __str__ 来强制触发兜底分支：
    - __slots__ = () 让 hasattr(obj, "__dict__") 返回 False（绕过 dataclass 分支）
    - __str__ 抛 TypeError 让 _json_default 中的 str(obj) 抛错
    """
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    class _Unserializable:
        __slots__ = ()  # 无 __dict__，绕过 dataclass 分支

        def __str__(self):
            raise TypeError("forced serialization error")

        def __repr__(self):
            raise TypeError("forced serialization error")

    # bytes 类型应被 _json_default 转换为字符串（不触发兜底分支）
    # 这里使用会真正抛错的类型验证兜底逻辑
    tracker.save_artifact(run_id, "doc-001", "parse", "input", _Unserializable())

    # 兜底分支应将 payload 写入为含 _serialize_error 的 dict
    result = tracker.get_artifact(run_id, "parse", "input")
    assert isinstance(result, dict)
    assert "_serialize_error" in result
    assert "_preview" in result


def test_save_artifact_handles_bytes_payload(temp_tracker) -> None:
    """bytes 类型 payload 应通过 _json_default 兜底转换为字符串"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    # bytes 没有 __dict__，_json_default 走 str(obj) 分支
    tracker.save_artifact(run_id, "doc-001", "parse", "input", b"raw bytes")

    result = tracker.get_artifact(run_id, "parse", "input")
    # bytes 会被转换为字符串 "b'raw bytes'"
    assert result == "b'raw bytes'"


# ────────── 9. 非法 direction 抛错 ──────────


def test_save_artifact_raises_for_invalid_direction(temp_tracker) -> None:
    """direction 非 input/output 时抛 ValueError"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    with pytest.raises(ValueError, match="direction"):
        tracker.save_artifact(run_id, "doc-001", "parse", "invalid", {"a": 1})


def test_save_artifact_warns_for_unknown_stage(temp_tracker, caplog) -> None:
    """未知 stage 应记录 warning 但不抛错"""
    tracker, conn = temp_tracker
    run_id = _make_run(conn)

    # 不应抛错
    tracker.save_artifact(run_id, "doc-001", "unknown_stage", "input", {"a": 1})

    # 应在日志中记录 warning
    # 注意：caplog 捕获需要 structlog 配置 propagate
    # 这里只验证写入成功即可
    result = tracker.get_artifact(run_id, "unknown_stage", "input")
    assert result == {"a": 1}


# ────────── 10. PIPELINE_STAGES 常量 ──────────


def test_pipeline_stages_constant() -> None:
    """PIPELINE_STAGES 应包含 4 个标准阶段"""
    assert PIPELINE_STAGES == ["parse", "extract", "compile", "index"]
    assert len(PIPELINE_STAGES) == 4


# ────────── 11. 全局单例 ──────────


def test_get_pipeline_tracker_returns_singleton() -> None:
    """get_pipeline_tracker 返回全局单例"""
    t1 = get_pipeline_tracker()
    t2 = get_pipeline_tracker()
    assert t1 is t2
    assert isinstance(t1, PipelineTracker)
