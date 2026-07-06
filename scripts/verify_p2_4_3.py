"""P2-4.3 拓扑快照与 diff 验证脚本

验证：
1. TopologyBuilder.save_snapshot / list_snapshots / get_snapshot / diff_snapshots
2. MCP 工具 save_topology_snapshot / diff_topology_snapshots
3. TOOLS / TOOL_HANDLERS / _TOOL_ANNOTATIONS 一致性
"""
from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path

# 设置后端路径
BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# 用临时 DB 避免污染真实数据
TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_test_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)
# 重定向 events.db
import app.aiops.topology_builder as tb_mod
tb_mod.DB_PATH = TMP_DIR / "events.db"

from app.aiops.topology_builder import TopologyBuilder, _get_db, _init_schema


def setup_test_data():
    """直接写入测试拓扑数据"""
    conn = _get_db()
    now = "2026-07-06T10:00:00Z"
    # 3 个节点
    nodes = [
        ("Host:web1", "Host", "web1", 3, '["doc1", "doc2"]'),
        ("Host:db1", "Host", "db1", 2, '["doc1"]'),
        ("Service:nginx", "Service", "nginx", 2, '["doc1", "doc2"]'),
    ]
    for nid, ntype, name, occ, docs in nodes:
        conn.execute(
            "INSERT OR REPLACE INTO topology_nodes "
            "(node_id, node_type, name, occurrences, source_docs, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (nid, ntype, name, occ, docs, now, now),
        )
    # 2 条边
    edges = [
        ("Service:nginx", "Host:web1", "RUNS_ON", 2, '["doc1", "doc2"]', 0, 1.0),
        ("Service:nginx", "Host:db1", "DEPENDS_ON", 1, '["doc1"]', 0, 1.0),
    ]
    for src, tgt, rel, occ, docs, inf, conf in edges:
        conn.execute(
            "INSERT OR REPLACE INTO topology_edges "
            "(source, target, relation, occurrences, source_docs, first_seen, last_seen, inferred, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (src, tgt, rel, occ, docs, now, now, inf, conf),
        )
    conn.commit()
    return conn


def test_save_and_list_snapshot():
    """测试保存快照 + 列出快照"""
    builder = TopologyBuilder()
    snap1 = builder.save_snapshot("v1.0")
    assert snap1["node_count"] == 3, f"node_count 期望 3，实际 {snap1['node_count']}"
    assert snap1["edge_count"] == 2, f"edge_count 期望 2，实际 {snap1['edge_count']}"
    assert snap1["snapshot_id"] > 0
    print(f"  ✅ save_snapshot: id={snap1['snapshot_id']}, label={snap1['label']}, "
          f"nodes={snap1['node_count']}, edges={snap1['edge_count']}")

    # 再保存一个
    snap2 = builder.save_snapshot("v1.1")
    assert snap2["snapshot_id"] > snap1["snapshot_id"]

    snaps = builder.list_snapshots(limit=10)
    assert len(snaps) >= 2, f"快照数期望 >= 2，实际 {len(snaps)}"
    # 应按时间倒序
    assert snaps[0]["snapshot_id"] == snap2["snapshot_id"]
    print(f"  ✅ list_snapshots: 返回 {len(snaps)} 条，最新 label={snaps[0]['label']}")
    return snap1["snapshot_id"], snap2["snapshot_id"]


def test_get_snapshot(snap_id):
    """测试获取快照详情"""
    builder = TopologyBuilder()
    snap = builder.get_snapshot(snap_id)
    assert snap is not None
    assert len(snap["nodes"]) == 3
    assert len(snap["edges"]) == 2
    print(f"  ✅ get_snapshot: id={snap_id}, nodes={len(snap['nodes'])}, edges={len(snap['edges'])}")

    # 不存在的快照
    assert builder.get_snapshot(99999) is None
    print("  ✅ get_snapshot(不存在) 返回 None")


def test_diff_no_change(snap_a, snap_b):
    """测试两个相同快照的 diff（应为空）"""
    builder = TopologyBuilder()
    # v1.0 和 v1.1 数据相同，diff 应全为空
    result = builder.diff_snapshots(snap_a, snap_b)
    assert "error" not in result, f"diff 返回错误: {result}"
    assert result["summary"]["nodes_added"] == 0
    assert result["summary"]["nodes_removed"] == 0
    assert result["summary"]["nodes_changed"] == 0
    assert result["summary"]["edges_added"] == 0
    assert result["summary"]["edges_removed"] == 0
    assert result["summary"]["edges_changed"] == 0
    print(f"  ✅ diff(无变化): summary={result['summary']}")


def test_diff_with_change():
    """测试有变化的 diff：保存 v1，修改拓扑，保存 v2，对比"""
    builder = TopologyBuilder()
    # 保存 v1
    snap_a = builder.save_snapshot("before-change")

    # 修改拓扑：新增节点、删除节点、改边
    conn = _get_db()
    now = "2026-07-06T11:00:00Z"
    # 新增节点 redis
    conn.execute(
        "INSERT OR REPLACE INTO topology_nodes "
        "(node_id, node_type, name, occurrences, source_docs, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("Host:redis1", "Host", "redis1", 1, '["doc3"]', now, now),
    )
    # 删除 db1 节点（及其边）
    conn.execute("DELETE FROM topology_nodes WHERE node_id = 'Host:db1'")
    conn.execute("DELETE FROM topology_edges WHERE source = 'Host:db1' OR target = 'Host:db1'")
    # 修改 nginx 的 occurrences（触发 changed）
    conn.execute(
        "UPDATE topology_nodes SET occurrences = 5, source_docs = ? "
        "WHERE node_id = 'Service:nginx'",
        ('["doc1", "doc2", "doc3"]',),
    )
    # 新增边 nginx → redis1
    conn.execute(
        "INSERT OR REPLACE INTO topology_edges "
        "(source, target, relation, occurrences, source_docs, first_seen, last_seen, inferred, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("Service:nginx", "Host:redis1", "USES", 1, '["doc3"]', now, now, 1, 0.8),
    )
    conn.commit()

    # 保存 v2
    snap_b = builder.save_snapshot("after-change")
    print(f"  ✅ 修改后保存快照: before_id={snap_a['snapshot_id']}, after_id={snap_b['snapshot_id']}")

    # diff
    result = builder.diff_snapshots(snap_a["snapshot_id"], snap_b["snapshot_id"])
    s = result["summary"]
    print(f"  ✅ diff summary: {s}")

    # 验证：新增 redis1 节点
    assert s["nodes_added"] == 1, f"nodes_added 期望 1，实际 {s['nodes_added']}"
    added_names = {n["name"] for n in result["nodes"]["added"]}
    assert "redis1" in added_names, f"redis1 应在 added 中: {added_names}"

    # 验证：删除 db1 节点
    assert s["nodes_removed"] == 1, f"nodes_removed 期望 1，实际 {s['nodes_removed']}"
    removed_names = {n["name"] for n in result["nodes"]["removed"]}
    assert "db1" in removed_names, f"db1 应在 removed 中: {removed_names}"

    # 验证：nginx 节点 changed
    assert s["nodes_changed"] == 1, f"nodes_changed 期望 1，实际 {s['nodes_changed']}"
    changed = result["nodes"]["changed"][0]
    assert changed["name"] == "nginx"
    assert changed["before"]["occurrences"] == 2
    assert changed["after"]["occurrences"] == 5
    print(f"     nginx changed: {changed['before']} → {changed['after']}")

    # 验证边：新增 nginx→redis1 USES，删除 nginx→db1 DEPENDS_ON
    assert s["edges_added"] == 1, f"edges_added 期望 1，实际 {s['edges_added']}"
    assert s["edges_removed"] == 1, f"edges_removed 期望 1，实际 {s['edges_removed']}"

    print(f"  ✅ diff 详情: nodes.added={[n.get('name') for n in result['nodes']['added']]}, "
          f"nodes.removed={[n.get('name') for n in result['nodes']['removed']]}, "
          f"edges.added={[(e.get('source'), e.get('target'), e.get('relation')) for e in result['edges']['added']]}")

    # 测试不存在的快照
    err_result = builder.diff_snapshots(99999, snap_b["snapshot_id"])
    assert "error" in err_result
    print(f"  ✅ diff(不存在的快照) 返回 error: {err_result['error']}")


def test_mcp_tools():
    """测试 MCP 工具"""
    from app.mcp.protocol import (
        TOOLS,
        TOOL_HANDLERS,
        _TOOL_ANNOTATIONS,
        _tool_save_topology_snapshot,
        _tool_diff_topology_snapshots,
        list_tools,
    )

    # 1. 工具注册一致性
    tool_names = {t["name"] for t in TOOLS}
    assert "save_topology_snapshot" in tool_names
    assert "diff_topology_snapshots" in tool_names
    assert "save_topology_snapshot" in TOOL_HANDLERS
    assert "diff_topology_snapshots" in TOOL_HANDLERS
    assert "save_topology_snapshot" in _TOOL_ANNOTATIONS
    assert "diff_topology_snapshots" in _TOOL_ANNOTATIONS
    print("  ✅ 工具注册一致性 OK")

    # 2. annotations 已应用到 TOOLS
    for t in TOOLS:
        if t["name"] in ("save_topology_snapshot", "diff_topology_snapshots"):
            assert "annotations" in t, f"{t['name']} 缺少 annotations"
    save_tool = next(t for t in TOOLS if t["name"] == "save_topology_snapshot")
    assert save_tool["annotations"].get("idempotentHint") is False  # 非幂等
    diff_tool = next(t for t in TOOLS if t["name"] == "diff_topology_snapshots")
    assert diff_tool["annotations"].get("readOnlyHint") is True  # 只读
    print(f"  ✅ annotations: save={save_tool['annotations']}, diff={diff_tool['annotations']}")

    # 3. save 工具调用
    builder = TopologyBuilder()
    snap = builder.save_snapshot("mcp-test")
    result = _tool_save_topology_snapshot({"label": "mcp-test-2"})
    import json
    parsed = json.loads(result)
    assert "saved" in parsed
    assert parsed["saved"]["label"] == "mcp-test-2"
    assert "recent_snapshots" in parsed
    print(f"  ✅ save_topology_snapshot 工具调用 OK，返回 recent_snapshots 数={len(parsed['recent_snapshots'])}")

    # 4. diff 工具调用
    diff_result = _tool_diff_topology_snapshots({
        "snapshot_id_a": snap["snapshot_id"],
        "snapshot_id_b": parsed["saved"]["snapshot_id"],
    })
    diff_parsed = json.loads(diff_result)
    assert "summary" in diff_parsed
    print(f"  ✅ diff_topology_snapshots 工具调用 OK，summary={diff_parsed['summary']}")

    # 5. diff 工具参数校验
    err = _tool_diff_topology_snapshots({"snapshot_id_a": "not-int"})
    err_parsed = json.loads(err)
    assert "error" in err_parsed
    print(f"  ✅ diff 参数校验 OK: {err_parsed['error']}")

    # 6. list_tools 返回新工具
    tools_list = list_tools()
    names = {t["name"] for t in tools_list}
    assert "save_topology_snapshot" in names
    assert "diff_topology_snapshots" in names
    print(f"  ✅ list_tools 返回 {len(tools_list)} 个工具，含新工具")


def main():
    print("=" * 60)
    print("P2-4.3 拓扑快照与 diff 验证")
    print("=" * 60)

    print("\n[1/6] 初始化测试数据...")
    setup_test_data()
    print("  ✅ 测试数据已写入（3 节点 + 2 边）")

    print("\n[2/6] 测试 save_snapshot + list_snapshots...")
    snap_a, snap_b = test_save_and_list_snapshot()

    print("\n[3/6] 测试 get_snapshot...")
    test_get_snapshot(snap_a)

    print("\n[4/6] 测试 diff（无变化）...")
    test_diff_no_change(snap_a, snap_b)

    print("\n[5/6] 测试 diff（有变化）...")
    test_diff_with_change()

    print("\n[6/6] 测试 MCP 工具...")
    test_mcp_tools()

    print("\n" + "=" * 60)
    print("✅ P2-4.3 全部验证通过！")
    print("=" * 60)


if __name__ == "__main__":
    main()
