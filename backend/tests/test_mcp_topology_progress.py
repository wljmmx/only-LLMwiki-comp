"""P2-5.5 候选 4 测试：MCP 工具 emit_progress 扩展

验证三个拓扑工具在设置进度回调时推送进度：
- _tool_infer_topology：4 步进度（开始/读取节点/计算共现/完成）
- _tool_merge_topology_aliases：3 步进度（开始/检测别名/完成）
- _tool_impact_analysis：3 步进度（开始/BFS遍历/完成）

无回调时（普通 JSON-RPC 路径）应为 no-op，不影响返回值。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp.progress import (
    reset_progress_context,
    set_progress_callback,
)

# ═══════════════ fixture ═══════════════


@pytest.fixture
def isolated_topology_db(tmp_path, monkeypatch):
    """隔离 topology_builder DB + 重置单例"""
    import app.aiops.topology_builder as tb_mod

    db_file = tmp_path / "topology.db"
    monkeypatch.setattr(tb_mod, "DB_PATH", db_file)
    # 重置全局单例
    monkeypatch.setattr(tb_mod, "_builder", None)
    yield tb_mod.get_topology_builder


@pytest.fixture(autouse=True)
def reset_progress():
    """每个测试前后清理 progress context"""
    reset_progress_context()
    yield
    reset_progress_context()


def _capture_progress() -> list[tuple[str, int, int]]:
    """设置进度回调，捕获事件，返回事件列表"""
    events: list[tuple[str, int, int]] = []
    set_progress_callback(
        lambda msg, cur, total: events.append((msg, cur, total)),
        token="test-token",
    )
    return events


def _add_test_node(builder, name: str, node_type: str = "Host") -> str:
    """直接用 SQL 插入测试节点（绕过 update 流程）"""
    import json
    from datetime import datetime, timezone

    import app.aiops.topology_builder as tb_mod

    conn = tb_mod._get_db()
    node_id = name.lower().replace(" ", "-")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO topology_nodes
           (node_id, node_type, name, occurrences, source_docs,
            first_seen, last_seen, metadata)
           VALUES (?, ?, ?, 1, ?, ?, ?, ?)""",
        (
            node_id,
            node_type,
            name,
            json.dumps(["test-doc"]),
            now,
            now,
            json.dumps({}, ensure_ascii=False),
        ),
    )
    conn.commit()
    return node_id


# ═══════════════ infer_topology ═══════════════


class TestInferTopologyProgress:
    def test_emits_4_progress_events(self, isolated_topology_db):
        """infer_topology 应推送 4 步进度"""
        from app.mcp.protocol import _tool_infer_topology

        events = _capture_progress()
        _tool_infer_topology({"min_cooccurrence": 2, "min_confidence": 0.3})

        assert len(events) == 4, f"应有 4 个进度事件，实际: {len(events)}"
        # 验证 current 单调递增到 total
        assert events[0][1] == 0 and events[0][2] == 4
        assert events[-1][1] == 4 and events[-1][2] == 4
        # 验证消息含关键信息
        assert "开始" in events[0][0]
        assert "完成" in events[-1][0]

    def test_progress_messages_contain_runtime_data(self, isolated_topology_db):
        """完成消息应含运行时数据（评估对数、推断边数）"""
        from app.mcp.protocol import _tool_infer_topology

        events = _capture_progress()
        _tool_infer_topology({})

        final_msg = events[-1][0]
        # 应含"评估"、"推断"、"跳过"等动态数据
        assert "评估" in final_msg or "对" in final_msg
        assert "推断" in final_msg or "边" in final_msg

    def test_noop_without_callback(self, isolated_topology_db):
        """无回调时应正常执行，返回值不受影响"""
        from app.mcp.protocol import _tool_infer_topology

        # 无回调（reset_progress_context 已在 fixture 执行）
        result = _tool_infer_topology({})
        # 应返回有效 JSON
        data = json.loads(result)
        assert "considered_pairs" in data
        assert "inferred_edges" in data
        assert "skipped_existing" in data
        assert "edges" in data

    def test_min_params_in_message(self, isolated_topology_db):
        """进度消息应含 min_cooccurrence/min_confidence 参数"""
        from app.mcp.protocol import _tool_infer_topology

        events = _capture_progress()
        _tool_infer_topology({"min_cooccurrence": 5, "min_confidence": 0.7})

        # 第 2 个事件应含参数信息
        assert any("5" in msg and "0.7" in msg for msg, _, _ in events), (
            f"进度消息应含参数值，events: {events}"
        )


# ═══════════════ merge_topology_aliases ═══════════════


class TestMergeAliasesProgress:
    def test_emits_3_progress_events(self, isolated_topology_db):
        """merge_aliases 应推送 3 步进度"""
        from app.mcp.protocol import _tool_merge_topology_aliases

        events = _capture_progress()
        _tool_merge_topology_aliases({})

        assert len(events) == 3, f"应有 3 个进度事件，实际: {len(events)}"
        assert events[0][1] == 0 and events[0][2] == 3
        assert events[-1][1] == 3 and events[-1][2] == 3
        assert "开始" in events[0][0]
        assert "完成" in events[-1][0]

    def test_completion_message_contains_stats(self, isolated_topology_db):
        """完成消息应含合并统计"""
        from app.mcp.protocol import _tool_merge_topology_aliases

        events = _capture_progress()
        _tool_merge_topology_aliases({})

        final_msg = events[-1][0]
        # 应含"合并"、"移除"、"重定向"等统计
        assert "合并" in final_msg
        assert "移除" in final_msg
        assert "重定向" in final_msg

    def test_noop_without_callback(self, isolated_topology_db):
        """无回调时正常执行"""
        from app.mcp.protocol import _tool_merge_topology_aliases

        result = _tool_merge_topology_aliases({})
        data = json.loads(result)
        assert "merged_pairs" in data
        assert "removed_nodes" in data
        assert "redirected_edges" in data


# ═══════════════ impact_analysis ═══════════════


class TestImpactAnalysisProgress:
    def test_emits_3_progress_events(self, isolated_topology_db):
        """impact_analysis 应推送 3 步进度"""
        from app.mcp.protocol import _tool_impact_analysis

        builder = isolated_topology_db()
        _add_test_node(builder, "nginx-01", "Host")

        events = _capture_progress()
        _tool_impact_analysis({"node_name": "nginx-01"})

        assert len(events) == 3, f"应有 3 个进度事件，实际: {len(events)}"
        assert events[0][1] == 0 and events[0][2] == 3
        assert events[-1][1] == 3 and events[-1][2] == 3
        assert "开始" in events[0][0]
        assert "完成" in events[-1][0]

    def test_start_message_contains_node_name(self, isolated_topology_db):
        """开始消息应含节点名"""
        from app.mcp.protocol import _tool_impact_analysis

        builder = isolated_topology_db()
        _add_test_node(builder, "redis-01", "Host")

        events = _capture_progress()
        _tool_impact_analysis({"node_name": "redis-01"})

        assert "redis-01" in events[0][0], (
            f"开始消息应含节点名，实际: {events[0][0]}"
        )

    def test_completion_message_contains_downstream_count(self, isolated_topology_db):
        """完成消息应含影响下游数"""
        from app.mcp.protocol import _tool_impact_analysis

        builder = isolated_topology_db()
        _add_test_node(builder, "web-01", "Host")

        events = _capture_progress()
        _tool_impact_analysis({"node_name": "web-01"})

        final_msg = events[-1][0]
        assert "影响" in final_msg
        assert "下游" in final_msg

    def test_empty_node_name_returns_error_no_progress(self, isolated_topology_db):
        """空 node_name 应直接返回 error，不推送进度"""
        from app.mcp.protocol import _tool_impact_analysis

        events = _capture_progress()
        result = _tool_impact_analysis({"node_name": ""})

        # 空节点名应直接返回 error，不触发任何进度
        assert len(events) == 0, f"空节点名不应推送进度，实际: {events}"
        data = json.loads(result)
        assert "error" in data

    def test_noop_without_callback(self, isolated_topology_db):
        """无回调时正常执行"""
        from app.mcp.protocol import _tool_impact_analysis

        builder = isolated_topology_db()
        _add_test_node(builder, "db-01", "Host")

        result = _tool_impact_analysis({"node_name": "db-01"})
        data = json.loads(result)
        assert "node" in data
        assert "impacted_downstream" in data
        assert "redundancy" in data


# ═══════════════ 与 generate_runbook 一致性 ═══════════════


class TestConsistencyWithRunbook:
    """验证新增的 emit_progress 调用与 _tool_generate_runbook 风格一致"""

    def test_all_tools_use_local_import(self):
        """所有工具应在函数内部局部 import emit_progress（避免顶层依赖）"""
        import inspect

        from app.mcp.protocol import (
            _tool_impact_analysis,
            _tool_infer_topology,
            _tool_merge_topology_aliases,
        )

        for func in [_tool_infer_topology, _tool_merge_topology_aliases, _tool_impact_analysis]:
            source = inspect.getsource(func)
            assert "from app.mcp.progress import emit_progress" in source, (
                f"{func.__name__} 应局部 import emit_progress"
            )

    def test_all_tools_call_emit_progress(self):
        """所有工具应实际调用 emit_progress"""
        import inspect

        from app.mcp.protocol import (
            _tool_impact_analysis,
            _tool_infer_topology,
            _tool_merge_topology_aliases,
        )

        for func in [_tool_infer_topology, _tool_merge_topology_aliases, _tool_impact_analysis]:
            source = inspect.getsource(func)
            call_count = source.count("emit_progress(")
            # 至少 2 次（开始 + 完成），减去 import 行的 1 次
            assert call_count >= 3, (
                f"{func.__name__} 应至少调用 emit_progress 2 次（含 import 共 3 处），"
                f"实际 {call_count} 处"
            )
