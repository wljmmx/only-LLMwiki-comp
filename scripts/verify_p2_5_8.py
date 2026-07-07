"""P2-5.8 JSON Schema 运行时校验 验证脚本

验证：
1. validate_args 基础类型校验（string/integer/boolean/object/array）
2. validate_args required 必填字段
3. validate_args enum 枚举值
4. validate_args minimum/maximum 数值范围
5. fill_defaults 自动填充 default
6. handle_request 缺失必填字段返回 -32602
7. handle_request 类型不匹配返回 -32602
8. handle_request enum 非法值返回 -32602
9. handle_request 合法请求（带 default 填充）正常执行
10. handle_request 未知工具仍返回 -32601（不混淆）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

TMP_DIR = Path(tempfile.mkdtemp(prefix="opsg_p258_"))
os.environ["OPSKG_DATA_DIR"] = str(TMP_DIR)
import app.aiops.topology_builder as tb_mod

tb_mod.DB_PATH = TMP_DIR / "events.db"

from app.mcp.schema_validator import fill_defaults, validate_args


def test_basic_types():
    """测试基础类型校验"""
    print("\n[1/10] 测试基础类型校验...")
    # string
    assert validate_args("hello", {"type": "string"}) == []
    assert validate_args(123, {"type": "string"}) != []
    # integer（bool 不算 integer）
    assert validate_args(42, {"type": "integer"}) == []
    assert validate_args(True, {"type": "integer"}) != []
    # boolean
    assert validate_args(True, {"type": "boolean"}) == []
    assert validate_args("true", {"type": "boolean"}) != []
    # object
    assert validate_args({}, {"type": "object"}) == []
    assert validate_args([], {"type": "object"}) != []
    # array
    assert validate_args([1, 2], {"type": "array"}) == []
    assert validate_args({}, {"type": "array"}) != []
    print("  ✅ string/integer/boolean/object/array 类型校验正确")


def test_required_fields():
    """测试必填字段"""
    print("\n[2/10] 测试必填字段...")
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    }
    # 缺 query → 报错
    errors = validate_args({"limit": 5}, schema)
    assert any("query" in e for e in errors), f"应报 query 缺失: {errors}"
    # 含 query → 通过
    assert validate_args({"query": "test"}, schema) == []
    print(f"  ✅ required 字段校验正确: {errors}")


def test_enum_validation():
    """测试 enum 枚举值"""
    print("\n[3/10] 测试 enum 枚举值...")
    schema = {
        "type": "object",
        "properties": {
            "node_type": {"type": "string", "enum": ["Host", "Service", "Component"]},
        },
    }
    # 合法值
    assert validate_args({"node_type": "Host"}, schema) == []
    # 非法值
    errors = validate_args({"node_type": "Unknown"}, schema)
    assert any("enum" in e for e in errors), f"应报 enum 错误: {errors}"
    print(f"  ✅ enum 校验正确: {errors}")


def test_numeric_range():
    """测试 minimum/maximum"""
    print("\n[4/10] 测试 minimum/maximum...")
    schema = {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
    }
    assert validate_args(50, schema) == []
    assert validate_args(0, schema) != []  # < minimum
    assert validate_args(101, schema) != []  # > maximum
    print("  ✅ minimum/maximum 校验正确")


def test_fill_defaults():
    """测试 fill_defaults"""
    print("\n[5/10] 测试 fill_defaults...")
    schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 10},
            "name": {"type": "string"},
        },
    }
    args = {"name": "test"}
    fill_defaults(args, schema)
    assert args["limit"] == 10, f"应填充 default=10: {args}"
    assert args["name"] == "test"
    print(f"  ✅ fill_defaults 正确: {args}")


def test_handle_request_missing_required():
    """测试 handle_request 缺失必填字段返回 -32602"""
    print("\n[6/10] 测试 handle_request 缺失必填字段...")
    from app.mcp.protocol import handle_request

    # search_knowledge 必填 query
    req = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "search_knowledge",
            "arguments": {"limit": 5},  # 缺 query
        },
    }
    resp = handle_request(req)
    assert resp["error"]["code"] == -32602, f"应返回 -32602: {resp}"
    assert "query" in resp["error"]["message"]
    print(f"  ✅ 缺 query 返回 -32602: {resp['error']['message']}")


def test_handle_request_type_mismatch():
    """测试 handle_request 类型不匹配返回 -32602"""
    print("\n[7/10] 测试 handle_request 类型不匹配...")
    from app.mcp.protocol import handle_request

    # query 应为 string，传 integer
    req = {
        "jsonrpc": "2.0",
        "id": "2",
        "method": "tools/call",
        "params": {
            "name": "search_knowledge",
            "arguments": {"query": 12345},
        },
    }
    resp = handle_request(req)
    assert resp["error"]["code"] == -32602, f"应返回 -32602: {resp}"
    assert "string" in resp["error"]["message"]
    print(f"  ✅ query=12345 返回 -32602: {resp['error']['message']}")


def test_handle_request_invalid_enum():
    """测试 handle_request enum 非法值返回 -32602"""
    print("\n[8/10] 测试 handle_request enum 非法值...")
    from app.mcp.protocol import handle_request

    # transition_incident 的 target_state enum 不含 "invalid"
    req = {
        "jsonrpc": "2.0",
        "id": "3",
        "method": "tools/call",
        "params": {
            "name": "transition_incident",
            "arguments": {
                "incident_id": "inc-001",
                "target_state": "invalid-state",
            },
        },
    }
    resp = handle_request(req)
    assert resp["error"]["code"] == -32602, f"应返回 -32602: {resp}"
    assert "enum" in resp["error"]["message"]
    print(f"  ✅ target_state=invalid-state 返回 -32602: {resp['error']['message']}")


def test_handle_request_valid_with_default():
    """测试合法请求（带 default 填充）正常执行"""
    print("\n[9/10] 测试合法请求 + default 填充...")
    from app.mcp.protocol import handle_request

    # search_knowledge 只传 query，limit 应被 default 填充
    req = {
        "jsonrpc": "2.0",
        "id": "4",
        "method": "tools/call",
        "params": {
            "name": "search_knowledge",
            "arguments": {"query": "nginx"},
        },
    }
    resp = handle_request(req)
    assert "result" in resp, f"应返回 result: {resp}"
    assert resp["result"]["isError"] is False
    print("  ✅ query=nginx 正常执行，limit 自动填充 default")


def test_handle_request_unknown_tool():
    """测试未知工具仍返回 -32601（不混淆）"""
    print("\n[10/10] 测试未知工具仍返回 -32601...")
    from app.mcp.protocol import handle_request

    req = {
        "jsonrpc": "2.0",
        "id": "5",
        "method": "tools/call",
        "params": {
            "name": "nonexistent_tool",
            "arguments": {},
        },
    }
    resp = handle_request(req)
    assert resp["error"]["code"] == -32601, f"应返回 -32601（未知工具）: {resp}"
    print(f"  ✅ 未知工具返回 -32601: {resp['error']['message']}")


def main():
    print("=" * 60)
    print("P2-5.8 JSON Schema 运行时校验 验证")
    print("=" * 60)

    test_basic_types()
    test_required_fields()
    test_enum_validation()
    test_numeric_range()
    test_fill_defaults()
    test_handle_request_missing_required()
    test_handle_request_type_mismatch()
    test_handle_request_invalid_enum()
    test_handle_request_valid_with_default()
    test_handle_request_unknown_tool()

    print("\n" + "=" * 60)
    print("✅ P2-5.8 全部验证通过！")
    print("=" * 60)


if __name__ == "__main__":
    main()
