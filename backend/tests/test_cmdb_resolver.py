"""P2-3.6 CMDB 实体归一化（IP↔主机名映射）测试

覆盖：
- CMDBResolver.resolve：IP→hostname / hostname→自身 / 短名→FQDN / 无映射
- add_mapping / list_mappings / remove_mapping 持久化
- auto_build_from_topology：从 metadata.ip 自动构建映射
- normalize_topology：IP 节点改名 + IP↔hostname 节点合并
- MCP 工具 resolve_cmdb_entity 调用路径
- TopologyBuilder.normalize_by_cmdb 委托路径

DB 隔离：每个测试通过 monkeypatch 将 events.db 重定向到 tmp_path，
并重置 topology_builder._builder 与 cmdb_resolver._resolver 全局单例。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═══════════════ 公共 fixture ═══════════════


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """隔离 topology_builder + cmdb_resolver DB，重置单例

    topology_builder.DB_PATH 被 monkeypatch 后，cmdb_resolver 通过
    `from app.aiops.topology_builder import _get_db` 调用的 _get_db
    会读取 topology_builder 模块的 DB_PATH，因此自动跟随。
    """
    import app.aiops.cmdb_resolver as cr_mod
    import app.aiops.topology_builder as tb_mod

    db_file = tmp_path / "events.db"
    monkeypatch.setattr(tb_mod, "DB_PATH", db_file)
    # 重置全局单例
    monkeypatch.setattr(tb_mod, "_builder", None)
    monkeypatch.setattr(cr_mod, "_resolver", None)
    yield


def _add_test_node(
    name: str,
    node_type: str = "Host",
    metadata: dict | None = None,
    occurrences: int = 1,
    source_docs: list[str] | None = None,
) -> str:
    """直接用 SQL 插入测试节点（绕过 update 流程）"""
    import app.aiops.topology_builder as tb_mod

    conn = tb_mod._get_db()
    node_id = f"{node_type}:{name.lower()}"
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO topology_nodes
           (node_id, node_type, name, occurrences, source_docs,
            first_seen, last_seen, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            node_id,
            node_type,
            name,
            occurrences,
            json.dumps(source_docs or ["test-doc"]),
            now,
            now,
            json.dumps(metadata or {}, ensure_ascii=False),
        ),
    )
    conn.commit()
    return node_id


def _add_test_edge(
    source_id: str, target_id: str, relation: str = "RUNS_ON"
) -> None:
    """直接用 SQL 插入测试边"""
    import app.aiops.topology_builder as tb_mod

    conn = tb_mod._get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO topology_edges
           (source, target, relation, occurrences, source_docs,
            first_seen, last_seen, inferred, confidence)
           VALUES (?, ?, ?, 1, ?, ?, ?, 0, 1.0)""",
        (source_id, target_id, relation, json.dumps(["test-doc"]), now, now),
    )
    conn.commit()


def _get_node(node_id: str) -> dict | None:
    """按 node_id 查询单个节点"""
    import app.aiops.topology_builder as tb_mod

    conn = tb_mod._get_db()
    r = conn.execute(
        "SELECT * FROM topology_nodes WHERE node_id = ?", (node_id,)
    ).fetchone()
    if not r:
        return None
    d = dict(r)
    d["source_docs"] = json.loads(d.get("source_docs") or "[]")
    d["metadata"] = json.loads(d.get("metadata") or "{}")
    return d


# ═══════════════ resolve 基础查询 ═══════════════


class TestResolve:
    def test_resolve_ip_to_hostname(self, isolated_db):
        """IP → hostname 正向查询"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver

        resolver = get_cmdb_resolver()
        resolver.add_mapping("10.0.0.1", "web1.example.com")
        assert resolver.resolve("10.0.0.1") == "web1.example.com"

    def test_resolve_hostname_to_self(self, isolated_db):
        """hostname → 自身（已归一化）"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver

        resolver = get_cmdb_resolver()
        resolver.add_mapping("10.0.0.1", "web1.example.com")
        # 已映射的 hostname 返回自身
        assert resolver.resolve("web1.example.com") == "web1.example.com"
        # 未映射的 hostname 也返回自身
        assert resolver.resolve("unknown.example.com") == "unknown.example.com"

    def test_resolve_short_name_to_fqdn(self, isolated_db):
        """短名 → FQDN（若有匹配的映射）"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver

        resolver = get_cmdb_resolver()
        resolver.add_mapping("10.0.0.1", "web1.example.com")
        # 短名 web1 应解析为 web1.example.com
        assert resolver.resolve("web1") == "web1.example.com"

    def test_resolve_no_mapping_returns_original(self, isolated_db):
        """无映射时返回原值"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver

        resolver = get_cmdb_resolver()
        # IP 无映射
        assert resolver.resolve("192.168.99.99") == "192.168.99.99"
        # 短名无映射
        assert resolver.resolve("nonexist") == "nonexist"
        # 空字符串
        assert resolver.resolve("") == ""


# ═══════════════ 映射管理 ═══════════════


class TestMappingManagement:
    def test_list_mappings_returns_all(self, isolated_db):
        """list_mappings 返回所有映射"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver

        resolver = get_cmdb_resolver()
        resolver.add_mapping("10.0.0.1", "web1.example.com")
        resolver.add_mapping("10.0.0.2", "web2.example.com")
        mappings = resolver.list_mappings()
        assert len(mappings) == 2
        ips = {m["ip"] for m in mappings}
        assert ips == {"10.0.0.1", "10.0.0.2"}
        hostnames = {m["hostname"] for m in mappings}
        assert hostnames == {"web1.example.com", "web2.example.com"}
        # source 默认为 manual
        assert all(m["source"] == "manual" for m in mappings)

    def test_remove_mapping_then_resolve_fails(self, isolated_db):
        """删除映射后 resolve 失效（返回原值）"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver

        resolver = get_cmdb_resolver()
        resolver.add_mapping("10.0.0.1", "web1.example.com")
        assert resolver.resolve("10.0.0.1") == "web1.example.com"

        # 删除映射
        deleted = resolver.remove_mapping("10.0.0.1")
        assert deleted is True
        # 删除后 resolve 返回原 IP
        assert resolver.resolve("10.0.0.1") == "10.0.0.1"

        # 再次删除返回 False
        deleted_again = resolver.remove_mapping("10.0.0.1")
        assert deleted_again is False

    def test_add_mapping_idempotent_same_ip(self, isolated_db):
        """同 IP 同 hostname 重复添加幂等"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver

        resolver = get_cmdb_resolver()
        r1 = resolver.add_mapping("10.0.0.1", "web1.example.com")
        assert r1["created"] is True
        r2 = resolver.add_mapping("10.0.0.1", "web1.example.com")
        assert r2["created"] is False
        assert len(resolver.list_mappings()) == 1

    def test_add_mapping_invalid_ip_raises(self, isolated_db):
        """非法 IP 抛出 ValueError"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver

        resolver = get_cmdb_resolver()
        with pytest.raises(ValueError, match="非法 IP"):
            resolver.add_mapping("not-an-ip", "web1.example.com")


# ═══════════════ auto_build_from_topology ═══════════════


class TestAutoBuildFromTopology:
    def test_auto_build_from_metadata_ip(self, isolated_db):
        """从节点 metadata.ip 自动构建映射（source=auto）"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver
        from app.aiops.topology_builder import get_topology_builder

        # 节点 name 是 hostname，metadata.ip 是 IP
        _add_test_node(
            "web1.example.com",
            node_type="Host",
            metadata={"ip": "10.0.0.1"},
        )

        builder = get_topology_builder()
        resolver = get_cmdb_resolver()
        added = resolver.auto_build_from_topology(builder)

        assert added == 1
        # 映射应已创建，source=auto
        mappings = resolver.list_mappings()
        assert len(mappings) == 1
        assert mappings[0]["ip"] == "10.0.0.1"
        assert mappings[0]["hostname"] == "web1.example.com"
        assert mappings[0]["source"] == "auto"

        # resolve 现在可以工作
        assert resolver.resolve("10.0.0.1") == "web1.example.com"

    def test_auto_build_skips_ip_named_nodes(self, isolated_db):
        """name 本身是 IP 的节点不构建映射（无 hostname 可映射）"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver
        from app.aiops.topology_builder import get_topology_builder

        _add_test_node(
            "10.0.0.1",
            node_type="Host",
            metadata={"ip": "10.0.0.1"},
        )

        builder = get_topology_builder()
        resolver = get_cmdb_resolver()
        added = resolver.auto_build_from_topology(builder)

        assert added == 0
        assert len(resolver.list_mappings()) == 0


# ═══════════════ normalize_topology ═══════════════


class TestNormalizeTopology:
    def test_normalize_renames_ip_node(self, isolated_db):
        """IP 节点名替换为 hostname（目标 hostname 节点不存在）"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver
        from app.aiops.topology_builder import get_topology_builder

        # 仅 IP 节点，无 hostname 节点
        ip_node_id = _add_test_node("10.0.0.1", node_type="Host")
        resolver = get_cmdb_resolver()
        resolver.add_mapping("10.0.0.1", "web1.example.com")

        builder = get_topology_builder()
        result = resolver.normalize_topology(builder)

        assert result["normalized_count"] == 1
        assert result["merged_count"] == 0
        assert "10.0.0.1" in result["mappings_used"]

        # 旧 IP 节点应消失
        assert _get_node(ip_node_id) is None
        # 新 hostname 节点应存在（node_id 格式 "Host:{name.lower()}"）
        new_node = _get_node("Host:web1.example.com")
        assert new_node is not None
        assert new_node["name"] == "web1.example.com"

    def test_normalize_merges_ip_and_hostname_nodes(self, isolated_db):
        """IP 节点与 hostname 节点合并（同类型、name 匹配）"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver
        from app.aiops.topology_builder import get_topology_builder

        # IP 节点
        ip_node_id = _add_test_node(
            "10.0.0.1",
            node_type="Host",
            source_docs=["doc-a"],
            metadata={"env": "prod"},
        )
        # hostname 节点（同类型）
        host_node_id = _add_test_node(
            "web1.example.com",
            node_type="Host",
            source_docs=["doc-b"],
            metadata={"region": "us-east-1"},
        )
        # 边：service → IP 节点（测试边迁移）
        svc_node_id = _add_test_node("svc1", node_type="Service")
        _add_test_edge(svc_node_id, ip_node_id, "RUNS_ON")

        resolver = get_cmdb_resolver()
        resolver.add_mapping("10.0.0.1", "web1.example.com")

        builder = get_topology_builder()
        result = resolver.normalize_topology(builder)

        assert result["normalized_count"] == 1
        assert result["merged_count"] == 1

        # IP 节点应被删除
        assert _get_node(ip_node_id) is None
        # hostname 节点应保留，且 source_docs 合并
        merged = _get_node(host_node_id)
        assert merged is not None
        assert set(merged["source_docs"]) == {"doc-a", "doc-b"}
        # metadata 合并（target 旧值优先，新字段补充）
        assert merged["metadata"]["env"] == "prod"
        assert merged["metadata"]["region"] == "us-east-1"

        # 边应迁移：service → hostname 节点
        import app.aiops.topology_builder as tb_mod

        conn = tb_mod._get_db()
        edges = conn.execute(
            "SELECT source, target FROM topology_edges WHERE source = ?",
            (svc_node_id,),
        ).fetchall()
        assert len(edges) == 1
        assert edges[0]["target"] == host_node_id

    def test_normalize_merges_via_metadata_ip(self, isolated_db):
        """metadata.ip 与另一节点 name 重合时合并

        场景：
        - 节点 A: name=web1, metadata.ip=10.0.0.2
        - 节点 B: name=10.0.0.2
        - normalize 应自动构建映射 10.0.0.2→web1，然后合并 B 入 A
        """
        from app.aiops.cmdb_resolver import get_cmdb_resolver
        from app.aiops.topology_builder import get_topology_builder

        # 节点 A：hostname 节点，metadata 含 ip
        host_node_id = _add_test_node(
            "web1",
            node_type="Host",
            metadata={"ip": "10.0.0.2"},
            source_docs=["doc-a"],
        )
        # 节点 B：IP 节点
        ip_node_id = _add_test_node(
            "10.0.0.2",
            node_type="Host",
            source_docs=["doc-b"],
        )

        resolver = get_cmdb_resolver()
        builder = get_topology_builder()
        result = resolver.normalize_topology(builder)

        # 应自动构建映射并合并
        assert result["normalized_count"] >= 1
        assert result["merged_count"] >= 1

        # IP 节点应被删除
        assert _get_node(ip_node_id) is None
        # hostname 节点保留，source_docs 合并
        merged = _get_node(host_node_id)
        assert merged is not None
        assert set(merged["source_docs"]) == {"doc-a", "doc-b"}


# ═══════════════ TopologyBuilder.normalize_by_cmdb ═══════════════


class TestBuilderNormalizeByCmdb:
    def test_builder_normalize_by_cmdb_delegates(self, isolated_db):
        """TopologyBuilder.normalize_by_cmdb 委托给 CMDBResolver"""
        from app.aiops.topology_builder import get_topology_builder

        _add_test_node("10.0.0.1", node_type="Host")

        from app.aiops.cmdb_resolver import get_cmdb_resolver

        get_cmdb_resolver().add_mapping("10.0.0.1", "web1.example.com")

        builder = get_topology_builder()
        result = builder.normalize_by_cmdb()

        assert result["normalized_count"] == 1
        assert "10.0.0.1" in result["mappings_used"]


# ═══════════════ MCP 工具 ═══════════════


class TestMcpTools:
    def test_tool_resolve_cmdb_entity(self, isolated_db):
        """MCP 工具 resolve_cmdb_entity 调用路径"""
        from app.aiops.cmdb_resolver import get_cmdb_resolver
        from app.mcp.protocol import _tool_resolve_cmdb_entity

        # 先添加映射
        get_cmdb_resolver().add_mapping("10.0.0.1", "web1.example.com")

        # 调用 MCP 工具
        result_str = _tool_resolve_cmdb_entity({"entity": "10.0.0.1"})
        data = json.loads(result_str)
        assert data["input"] == "10.0.0.1"
        assert data["resolved"] == "web1.example.com"
        assert data["normalized"] is True

    def test_tool_resolve_cmdb_entity_no_mapping(self, isolated_db):
        """MCP 工具 resolve_cmdb_entity 无映射时返回原值"""
        from app.mcp.protocol import _tool_resolve_cmdb_entity

        result_str = _tool_resolve_cmdb_entity({"entity": "10.0.0.99"})
        data = json.loads(result_str)
        assert data["resolved"] == "10.0.0.99"
        assert data["normalized"] is False

    def test_tool_resolve_cmdb_entity_empty_input(self, isolated_db):
        """MCP 工具 resolve_cmdb_entity 空入参报错"""
        from app.mcp.protocol import _tool_resolve_cmdb_entity

        result_str = _tool_resolve_cmdb_entity({"entity": ""})
        data = json.loads(result_str)
        assert "error" in data

    def test_tool_add_cmdb_mapping(self, isolated_db):
        """MCP 工具 add_cmdb_mapping 添加映射"""
        from app.mcp.protocol import _tool_add_cmdb_mapping

        result_str = _tool_add_cmdb_mapping(
            {"ip": "10.0.0.1", "hostname": "web1.example.com"}
        )
        data = json.loads(result_str)
        assert data["ip"] == "10.0.0.1"
        assert data["hostname"] == "web1.example.com"
        assert data["created"] is True

    def test_tool_add_cmdb_mapping_invalid_ip(self, isolated_db):
        """MCP 工具 add_cmdb_mapping 非法 IP 报错"""
        from app.mcp.protocol import _tool_add_cmdb_mapping

        result_str = _tool_add_cmdb_mapping(
            {"ip": "not-ip", "hostname": "web1"}
        )
        data = json.loads(result_str)
        assert "error" in data
        assert "非法 IP" in data["error"]

    def test_tool_normalize_topology_by_cmdb(self, isolated_db):
        """MCP 工具 normalize_topology_by_cmdb 归一化拓扑"""
        from app.mcp.protocol import (
            _tool_add_cmdb_mapping,
            _tool_normalize_topology_by_cmdb,
        )

        _add_test_node("10.0.0.1", node_type="Host")
        _tool_add_cmdb_mapping(
            {"ip": "10.0.0.1", "hostname": "web1.example.com"}
        )

        result_str = _tool_normalize_topology_by_cmdb({})
        data = json.loads(result_str)
        assert data["normalized_count"] == 1
        assert "10.0.0.1" in data["mappings_used"]


# ═══════════════ 工具注册一致性 ═══════════════


class TestToolRegistration:
    def test_tools_registered_in_all_dicts(self):
        """三个新工具应在 TOOLS / TOOL_HANDLERS / TOOL_REQUIRED_ROLES /
        _TOOL_ANNOTATIONS 中一致注册"""
        from app.mcp.protocol import (
            _TOOL_ANNOTATIONS,
            TOOL_HANDLERS,
            TOOL_REQUIRED_ROLES,
            TOOLS,
        )

        new_tools = {
            "resolve_cmdb_entity",
            "add_cmdb_mapping",
            "normalize_topology_by_cmdb",
        }
        tool_names = {t["name"] for t in TOOLS}
        for name in new_tools:
            assert name in tool_names, f"{name} 未在 TOOLS 中注册"
            assert name in TOOL_HANDLERS, f"{name} 未在 TOOL_HANDLERS 中注册"
            assert name in TOOL_REQUIRED_ROLES, (
                f"{name} 未在 TOOL_REQUIRED_ROLES 中注册"
            )
            assert name in _TOOL_ANNOTATIONS, (
                f"{name} 未在 _TOOL_ANNOTATIONS 中注册"
            )

    def test_tool_roles_correct(self):
        """工具权限：查询 viewer，add admin，normalize operator"""
        from app.mcp.protocol import TOOL_REQUIRED_ROLES

        assert TOOL_REQUIRED_ROLES["resolve_cmdb_entity"] == "viewer"
        assert TOOL_REQUIRED_ROLES["add_cmdb_mapping"] == "admin"
        assert (
            TOOL_REQUIRED_ROLES["normalize_topology_by_cmdb"] == "operator"
        )

    def test_tool_annotations_correct(self):
        """工具 annotations 正确"""
        from app.mcp.protocol import _TOOL_ANNOTATIONS

        assert _TOOL_ANNOTATIONS["resolve_cmdb_entity"].get("readOnlyHint") is True
        assert (
            _TOOL_ANNOTATIONS["add_cmdb_mapping"].get("destructiveHint") is True
        )
        assert (
            _TOOL_ANNOTATIONS["normalize_topology_by_cmdb"].get(
                "destructiveHint"
            )
            is True
        )
