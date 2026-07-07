"""P2-4.6 节点 metadata 扩展验证脚本

验证：
1. rule_extractor 抽取 version/owner/env/region/capacity
2. TopologyBuilder._merge_to_db 写入 metadata 列
3. get_topology / get_node 反序列化 metadata
4. update_node_metadata 手动合并/替换
5. REST 端点（导入 router 验证签名）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_p246_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)
import app.aiops.topology_builder as tb_mod

tb_mod.DB_PATH = TMP_DIR / "events.db"

from app.aiops.topology_builder import TopologyBuilder, _get_db
from app.extraction.rule_extractor import RuleBasedExtractor
from app.parsers.base import ParsedDocument, ParsedElement


def test_extractor_metadata():
    """测试 rule_extractor 抽取 metadata 字段"""
    print("\n[1/5] 测试 rule_extractor 抽取 metadata...")

    extractor = RuleBasedExtractor()

    # 构造一个包含丰富 metadata 的文档
    text = """
web-prod-01 部署说明

主机: web-prod-01
IP: 10.0.1.5
环境: prod
region: cn-east-1
owner: infra-team
version: 1.20.1
nginx 1.20.1
容量: 3
负责人: alice

服务依赖：nginx
"""
    doc = ParsedDocument(
        doc_id="test-doc-1",
        source_path="/tmp/test-doc-1.md",
        format="markdown",
        checksum="sha256:abc123",
        title="web-prod-01 部署说明",
        elements=[
            ParsedElement(
                type="paragraph",
                section="部署",
                content=text,
            )
        ],
    )

    entities, relations = extractor.extract(doc)

    # 应抽到 Host（web-prod-01 / 10.0.1.5）+ Service + Component（nginx）
    hosts = [e for e in entities if e.entity_type == "Host"]
    components = [e for e in entities if e.entity_type == "Component"]

    assert len(hosts) >= 1, f"应至少抽到 1 个 Host，实际 {len(hosts)}"
    assert len(components) >= 1, f"应至少抽到 1 个 Component，实际 {len(components)}"

    # 检查 Host 的 metadata 字段
    host = hosts[0]
    props = host.properties
    print(f"  Host: {host.name}, properties keys: {sorted(props.keys())}")

    # 应至少含 env（来自主机名 -prod-）和 global_meta
    assert "env" in props, f"Host properties 应含 env: {props}"
    assert props["env"] == "prod", f"env 期望 prod，实际 {props['env']}"

    # 应含 region / owner / version / capacity
    assert "region" in props, f"应含 region: {props}"
    assert props["region"] == "cn-east-1"
    assert "owner" in props, f"应含 owner: {props}"
    assert "version" in props, f"应含 version: {props}"
    assert "capacity" in props, f"应含 capacity: {props}"
    assert props["capacity"] == 3
    assert props["replicas"] == 3

    # Component nginx 应有版本号
    nginx = next(c for c in components if c.name == "nginx")
    print(f"  Component nginx properties keys: {sorted(nginx.properties.keys())}")
    assert "version" in nginx.properties, f"nginx 应有 version: {nginx.properties}"
    assert nginx.properties["version"] == "1.20.1"

    print("  ✅ rule_extractor metadata 抽取正确")


def test_topology_builder_metadata():
    """测试 TopologyBuilder 持久化与读取 metadata"""
    print("\n[2/5] 测试 TopologyBuilder 持久化 metadata...")

    builder = TopologyBuilder()

    # 模拟抽取的节点（含 metadata）
    nodes = [
        {
            "node_type": "Host",
            "name": "web-prod-01",
            "metadata": {"ip": "10.0.1.5", "env": "prod", "region": "cn-east-1"},
        },
        {
            "node_type": "Service",
            "name": "user-service",
            "metadata": {"owner": "team-a", "version": "v1.2.0"},
        },
        {
            "node_type": "Component",
            "name": "nginx",
            "metadata": {"version": "1.20.1"},
        },
    ]
    edges = [
        {"source": "user-service", "target": "web-prod-01", "relation": "RUNS_ON"},
        {"source": "user-service", "target": "nginx", "relation": "USES"},
    ]

    conn = _get_db()
    builder._merge_to_db(conn, "doc-test-1", nodes, edges)

    # 验证 DB 中 metadata 列
    r = conn.execute(
        "SELECT metadata FROM topology_nodes WHERE node_id = ?", ("Host:web-prod-01",)
    ).fetchone()
    import json
    meta = json.loads(r["metadata"])
    assert meta["ip"] == "10.0.1.5"
    assert meta["env"] == "prod"
    assert meta["region"] == "cn-east-1"
    print(f"  ✅ web-prod-01 metadata: {meta}")

    # 验证 get_topology 反序列化
    topo = builder.get_topology()
    host_node = next(n for n in topo["nodes"] if n["node_id"] == "Host:web-prod-01")
    assert "metadata" in host_node
    assert host_node["metadata"]["ip"] == "10.0.1.5"
    print("  ✅ get_topology 反序列化 metadata OK")

    # 验证 get_node
    node = builder.get_node("Service:user-service")
    assert node is not None
    assert node["metadata"]["owner"] == "team-a"
    assert node["metadata"]["version"] == "v1.2.0"
    print(f"  ✅ get_node 返回 metadata: {node['metadata']}")

    # 验证 get_neighbors 也反序列化 metadata
    neighbors = builder.get_neighbors("web-prod-01", depth=1)
    for n in neighbors["neighbors"]:
        assert "metadata" in n
    print("  ✅ get_neighbors 反序列化 metadata OK")


def test_metadata_merge_on_update():
    """测试增量合并时 metadata 互补累积"""
    print("\n[3/5] 测试 metadata 增量合并...")

    builder = TopologyBuilder()
    conn = _get_db()

    # 第一次：只有 ip
    builder._merge_to_db(
        conn,
        "doc-A",
        [{"node_type": "Host", "name": "db-01", "metadata": {"ip": "10.0.2.5"}}],
        [],
    )
    node = builder.get_node("Host:db-01")
    assert node["metadata"] == {"ip": "10.0.2.5"}, f"首次 metadata: {node['metadata']}"
    print(f"  ✅ 首次抽取: {node['metadata']}")

    # 第二次：补充 env + owner（同节点不同文档）
    builder._merge_to_db(
        conn,
        "doc-B",
        [{"node_type": "Host", "name": "db-01", "metadata": {"env": "prod", "owner": "dba"}}],
        [],
    )
    node = builder.get_node("Host:db-01")
    # 旧值优先（ip 不被覆盖），新字段补充
    assert node["metadata"]["ip"] == "10.0.2.5", f"ip 不应被覆盖: {node['metadata']}"
    assert node["metadata"]["env"] == "prod"
    assert node["metadata"]["owner"] == "dba"
    print(f"  ✅ 二次抽取合并后: {node['metadata']}")

    # 第三次：尝试覆盖 ip（旧值优先，应保持原值）
    builder._merge_to_db(
        conn,
        "doc-C",
        [{"node_type": "Host", "name": "db-01", "metadata": {"ip": "10.99.99.99"}}],
        [],
    )
    node = builder.get_node("Host:db-01")
    assert node["metadata"]["ip"] == "10.0.2.5", f"ip 应保持原值: {node['metadata']}"
    print(f"  ✅ 三次抽取（旧值优先）: {node['metadata']}")


def test_update_node_metadata_api():
    """测试 update_node_metadata 方法（手动设置）"""
    print("\n[4/5] 测试 update_node_metadata 手动设置...")

    builder = TopologyBuilder()

    # 不存在的节点
    result = builder.update_node_metadata("Host:not-exist", {"env": "test"})
    assert "error" in result
    print(f"  ✅ 不存在的节点返回 error: {result['error']}")

    # merge=True 模式
    result = builder.update_node_metadata(
        "Host:db-01",
        {"owner": "new-dba", "region": "us-west-2"},
        merge=True,
    )
    assert "error" not in result
    assert result["metadata"]["owner"] == "new-dba"  # 覆盖
    assert result["metadata"]["region"] == "us-west-2"  # 新增
    assert result["metadata"]["ip"] == "10.0.2.5"  # 保留
    assert result["metadata"]["env"] == "prod"  # 保留
    assert "owner" in result["updated_fields"]
    assert "region" in result["updated_fields"]
    print(f"  ✅ merge=True: {result['metadata']}")
    print(f"     updated_fields: {result['updated_fields']}")

    # merge=False 模式（整体替换）
    result = builder.update_node_metadata(
        "Host:db-01",
        {"capacity": 5},
        merge=False,
    )
    assert result["metadata"] == {"capacity": 5}
    print(f"  ✅ merge=False: {result['metadata']}")


def test_router_endpoints():
    """验证 router 端点定义存在"""
    print("\n[5/5] 测试 router 端点定义...")

    from app.routers.topology_router import router

    paths = {route.path: route.methods for route in router.routes}
    assert "/topology/node/{node_id}" in paths
    assert "GET" in paths["/topology/node/{node_id}"]
    assert "/topology/node/{node_id}/metadata" in paths
    assert "PATCH" in paths["/topology/node/{node_id}/metadata"]
    print("  ✅ GET /topology/node/{node_id} 已注册")
    print("  ✅ PATCH /topology/node/{node_id}/metadata 已注册")

    # 验证端点可调用
    builder = TopologyBuilder()
    node = builder.get_node("Host:db-01")
    assert node is not None
    print("  ✅ 端点底层方法可调用")


def main():
    print("=" * 60)
    print("P2-4.6 节点 metadata 扩展验证")
    print("=" * 60)

    test_extractor_metadata()
    test_topology_builder_metadata()
    test_metadata_merge_on_update()
    test_update_node_metadata_api()
    test_router_endpoints()

    print("\n" + "=" * 60)
    print("✅ P2-4.6 全部验证通过！")
    print("=" * 60)


if __name__ == "__main__":
    main()
