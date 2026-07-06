"""P2-4.7 影响分析结合冗余度 验证脚本

验证：
1. impact_analysis 返回 redundancy 字段
2. SPOF 节点（replicas<=1）severity=critical
3. 多副本节点（replicas>=3）severity=minor
4. 临界副本（replicas=2）severity=degraded
5. summary 含 critical_count/degraded_count/minor_count/blast_radius_score
6. node_is_spof 标注正确
7. MCP 工具 _tool_impact_analysis 输出 redundancy 字段
"""
from __future__ import annotations

import sys
import os
import tempfile
import json
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_p247_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)
import app.aiops.topology_builder as tb_mod
tb_mod.DB_PATH = TMP_DIR / "events.db"

from app.aiops.topology_builder import TopologyBuilder, _get_db


def _setup_topology():
    """构造测试拓扑：
        spof-host (replicas=1)  ←  critical-svc (replicas=1)
        spof-host (replicas=1)  ←  degraded-svc (replicas=2)
        multi-host (replicas=3) ←  minor-svc (replicas=5)
        multi-host (replicas=3) ←  critical-svc (replicas=1)

    即 critical-svc 同时依赖 spof-host 和 multi-host（双向依赖用于多场景验证）。
    """
    builder = TopologyBuilder()
    conn = _get_db()

    # 4 个节点：spof-host / multi-host / critical-svc / degraded-svc / minor-svc
    nodes = [
        {
            "node_type": "Host",
            "name": "spof-host",
            "metadata": {"replicas": 1, "env": "prod"},
        },
        {
            "node_type": "Host",
            "name": "multi-host",
            "metadata": {"replicas": 3, "env": "prod"},
        },
        {
            "node_type": "Service",
            "name": "critical-svc",
            "metadata": {"replicas": 1, "owner": "team-a"},
        },
        {
            "node_type": "Service",
            "name": "degraded-svc",
            "metadata": {"replicas": 2, "owner": "team-b"},
        },
        {
            "node_type": "Service",
            "name": "minor-svc",
            "metadata": {"replicas": 5, "owner": "team-c"},
        },
    ]
    # 边：source 依赖 target（source RUNS_ON / USES target）
    # 下游（受 spof-host 故障影响）= 依赖 spof-host 的服务
    edges = [
        {"source": "critical-svc", "target": "spof-host", "relation": "RUNS_ON"},
        {"source": "degraded-svc", "target": "spof-host", "relation": "RUNS_ON"},
        {"source": "critical-svc", "target": "multi-host", "relation": "RUNS_ON"},
        {"source": "minor-svc", "target": "multi-host", "relation": "RUNS_ON"},
    ]
    builder._merge_to_db(conn, "doc-test", nodes, edges)
    return builder


def test_impact_analysis_returns_redundancy():
    """测试 impact_analysis 返回 redundancy 字段"""
    print("\n[1/7] 测试 impact_analysis 返回 redundancy 字段...")
    builder = _setup_topology()

    result = builder.impact_analysis("spof-host")
    assert "redundancy" in result, f"应含 redundancy 字段: {list(result.keys())}"
    red = result["redundancy"]
    assert "node_replicas" in red
    assert "node_is_spof" in red
    assert "downstream_impacts" in red
    print(f"  ✅ redundancy 字段存在: keys={list(red.keys())}")


def test_spof_node_severity_critical():
    """测试 SPOF 节点（replicas=1）下游 severity=critical"""
    print("\n[2/7] 测试 SPOF 节点 severity=critical...")
    builder = _setup_topology()

    # spof-host 故障 → critical-svc (replicas=1) 受影响
    result = builder.impact_analysis("spof-host")
    impacts = {i["name"]: i for i in result["redundancy"]["downstream_impacts"]}

    assert "critical-svc" in impacts, f"应含 critical-svc: {list(impacts.keys())}"
    cs = impacts["critical-svc"]
    assert cs["replicas"] == 1
    assert cs["is_spof"] is True
    assert cs["severity"] == "critical"
    assert cs["impact_ratio"] == 1.0
    assert cs["capacity_remaining"] == 0
    print(f"  ✅ critical-svc: severity={cs['severity']}, ratio={cs['impact_ratio']}, spof={cs['is_spof']}")


def test_multi_replica_severity_minor():
    """测试多副本节点（replicas>=3）下游 severity=minor"""
    print("\n[3/7] 测试多副本节点 severity=minor...")
    builder = _setup_topology()

    # multi-host 故障 → minor-svc (replicas=5) 受影响
    result = builder.impact_analysis("multi-host")
    impacts = {i["name"]: i for i in result["redundancy"]["downstream_impacts"]}

    ms = impacts["minor-svc"]
    assert ms["replicas"] == 5
    assert ms["is_spof"] is False
    assert ms["severity"] == "minor"
    assert ms["impact_ratio"] == 0.2  # 1/5
    assert ms["capacity_remaining"] == 4
    print(f"  ✅ minor-svc: severity={ms['severity']}, ratio={ms['impact_ratio']}, remaining={ms['capacity_remaining']}")


def test_degraded_severity():
    """测试临界副本（replicas=2）severity=degraded"""
    print("\n[4/7] 测试 replicas=2 severity=degraded...")
    builder = _setup_topology()

    # spof-host 故障 → degraded-svc (replicas=2) 受影响
    result = builder.impact_analysis("spof-host")
    impacts = {i["name"]: i for i in result["redundancy"]["downstream_impacts"]}

    ds = impacts["degraded-svc"]
    assert ds["replicas"] == 2
    assert ds["is_spof"] is False
    assert ds["severity"] == "degraded"
    assert ds["impact_ratio"] == 0.5  # 1/2
    assert ds["capacity_remaining"] == 1
    print(f"  ✅ degraded-svc: severity={ds['severity']}, ratio={ds['impact_ratio']}, remaining={ds['capacity_remaining']}")


def test_summary_aggregation():
    """测试 summary 汇总字段"""
    print("\n[5/7] 测试 summary 汇总...")
    builder = _setup_topology()

    # spof-host 故障 → 下游: critical-svc (replicas=1, critical) + degraded-svc (replicas=2, degraded)
    result = builder.impact_analysis("spof-host")
    s = result["summary"]

    assert s["impacted_count"] == 2, f"impacted_count: {s}"
    assert s["critical_count"] == 1, f"critical_count: {s}"
    assert s["degraded_count"] == 1, f"degraded_count: {s}"
    assert s["minor_count"] == 0, f"minor_count: {s}"
    assert s["single_points_of_failure"] == 1, f"spof: {s}"
    # blast_radius = 1.0 (critical-svc) + 0.5 (degraded-svc) = 1.5
    assert abs(s["blast_radius_score"] - 1.5) < 1e-6, f"blast_radius: {s}"
    print(f"  ✅ summary: impacted={s['impacted_count']}, critical={s['critical_count']}, "
          f"degraded={s['degraded_count']}, spof={s['single_points_of_failure']}, "
          f"blast={s['blast_radius_score']}")


def test_node_is_spof_flag():
    """测试 node_is_spof 标注"""
    print("\n[6/7] 测试 node_is_spof 标注...")
    builder = _setup_topology()

    # spof-host: replicas=1 + 有 dependents → is_spof=True
    result = builder.impact_analysis("spof-host")
    assert result["redundancy"]["node_is_spof"] is True
    assert result["redundancy"]["node_replicas"] == 1
    print(f"  ✅ spof-host: node_is_spof=True, replicas=1")

    # multi-host: replicas=3 + 有 dependents → is_spof=False
    result = builder.impact_analysis("multi-host")
    assert result["redundancy"]["node_is_spof"] is False
    assert result["redundancy"]["node_replicas"] == 3
    print(f"  ✅ multi-host: node_is_spof=False, replicas=3")


def test_mcp_tool_redundancy_field():
    """测试 MCP _tool_impact_analysis 输出 redundancy 字段"""
    print("\n[7/7] 测试 MCP 工具输出 redundancy 字段...")
    builder = _setup_topology()

    from app.mcp.protocol import _tool_impact_analysis

    raw = _tool_impact_analysis({"node_name": "spof-host"})
    data = json.loads(raw)

    assert "redundancy" in data, f"MCP 输出应含 redundancy: {list(data.keys())}"
    red = data["redundancy"]
    assert red["node_is_spof"] is True
    assert red["node_replicas"] == 1
    assert len(red["downstream_impacts"]) == 2

    # summary 应含新字段
    s = data["summary"]
    assert "critical_count" in s
    assert "degraded_count" in s
    assert "minor_count" in s
    assert "single_points_of_failure" in s
    assert "blast_radius_score" in s
    print(f"  ✅ MCP 输出 redundancy: node_is_spof={red['node_is_spof']}, "
          f"impacts={len(red['downstream_impacts'])}")
    print(f"  ✅ MCP summary 新字段: {s}")


def test_backward_compatibility():
    """测试向后兼容：原有字段仍存在"""
    print("\n[额外] 测试向后兼容...")
    builder = _setup_topology()

    result = builder.impact_analysis("spof-host")
    # 原有字段
    assert "node" in result
    assert "impacted_downstream" in result
    assert "potential_root_cause" in result
    assert "edges" in result
    assert "summary" in result
    # 原有 summary 字段
    assert "impacted_count" in result["summary"]
    assert "root_cause_candidates" in result["summary"]
    print(f"  ✅ 原有字段全部保留: {list(result.keys())}")


def main():
    print("=" * 60)
    print("P2-4.7 影响分析结合冗余度 验证")
    print("=" * 60)

    test_impact_analysis_returns_redundancy()
    test_spof_node_severity_critical()
    test_multi_replica_severity_minor()
    test_degraded_severity()
    test_summary_aggregation()
    test_node_is_spof_flag()
    test_mcp_tool_redundancy_field()
    test_backward_compatibility()

    print("\n" + "=" * 60)
    print("✅ P2-4.7 全部验证通过！")
    print("=" * 60)


if __name__ == "__main__":
    main()
