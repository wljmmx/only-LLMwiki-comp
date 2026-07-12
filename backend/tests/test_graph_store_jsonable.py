"""GraphStore Neo4j temporal 类型 JSON 序列化测试

问题背景：
    Neo4j Cypher `datetime()` / `date()` / `time()` 写入的 temporal 属性，
    被 Python 驱动读回为 `neo4j.time.DateTime` 等自定义类型，不可被
    `json.dumps` / FastAPI `jsonable_encoder` 序列化：
    - DateTime: json.dumps 抛 TypeError；jsonable_encoder 抛 ValueError
    - Date/Time: jsonable_encoder 返回乱码 dict（如 {'_Date__ordinal': ...}）
    - Duration: json.dumps 返回乱码数组 [years, months, days, seconds]

    这会导致 GET /graph/entity/{name}（返回 properties(n) 含 updated_at）
    在 FastAPI 响应阶段崩溃，被外层 except 捕获后降级为 {"error": ...}。

验证：
    1. _to_jsonable 正确转换 4 种 temporal 类型为 ISO 8601 字符串
    2. 转换后可被 json.dumps 序列化
    3. 转换后可被 FastAPI jsonable_encoder 处理
    4. 递归处理 dict / list / tuple
    5. 原生类型（str/int/float/None/bool）原样返回
"""
from __future__ import annotations

import json
from datetime import timezone

import pytest
from fastapi.encoders import jsonable_encoder
from neo4j.time import Date, DateTime, Duration, Time

from app.knowledge.graph_store import _to_jsonable


# ────────── 1. 单类型转换 ──────────


class TestToJsonableTemporals:
    """4 种 neo4j.time temporal 类型的转换"""

    def test_datetime_to_iso_string(self) -> None:
        """DateTime → ISO 8601 字符串（含时区）"""
        dt = DateTime(2026, 7, 12, 10, 30, 45, 123456789, tzinfo=timezone.utc)
        result = _to_jsonable(dt)
        assert isinstance(result, str)
        assert result == "2026-07-12T10:30:45.123456789+00:00"
        # 验证可被标准 json 序列化
        json.dumps(result)

    def test_date_to_iso_string(self) -> None:
        """Date → ISO 8601 日期字符串"""
        d = Date(2026, 7, 12)
        result = _to_jsonable(d)
        assert isinstance(result, str)
        assert result == "2026-07-12"
        json.dumps(result)

    def test_time_to_iso_string(self) -> None:
        """Time → ISO 8601 时间字符串"""
        t = Time(10, 30, 45, 123456789)
        result = _to_jsonable(t)
        assert isinstance(result, str)
        assert result == "10:30:45.123456789"
        json.dumps(result)

    def test_duration_to_iso_string(self) -> None:
        """Duration → ISO 8601 duration 字符串（PnYnMnDTnHnMnS）"""
        dur = Duration(days=1, hours=2, minutes=30)
        result = _to_jsonable(dur)
        assert isinstance(result, str)
        # str(Duration) 返回 ISO 8601 duration 格式
        assert result.startswith("P")
        json.dumps(result)


# ────────── 2. 递归容器转换 ──────────


class TestToJsonableContainers:
    """dict / list / tuple 递归转换"""

    def test_dict_with_datetime_value(self) -> None:
        """dict 含 DateTime 值 → 递归转换"""
        dt = DateTime(2026, 7, 12, 10, 30, 0, 0, tzinfo=timezone.utc)
        props = {
            "name": "nginx",
            "entity_type": "Service",
            "confidence": 0.95,
            "updated_at": dt,
            "source_doc_id": "doc-001",
        }
        result = _to_jsonable(props)
        assert result["updated_at"] == "2026-07-12T10:30:00.000000000+00:00"
        assert result["name"] == "nginx"  # 原生类型不变
        assert result["confidence"] == 0.95
        # 整个 dict 可被 json 序列化
        json.dumps(result)

    def test_list_with_temporals(self) -> None:
        """list 含多种 temporal 类型 → 递归转换"""
        items = [
            DateTime(2026, 1, 1, tzinfo=timezone.utc),
            Date(2026, 1, 2),
            Duration(hours=3),
        ]
        result = _to_jsonable(items)
        assert result[0] == "2026-01-01T00:00:00.000000000+00:00"
        assert result[1] == "2026-01-02"
        assert result[2].startswith("P")
        json.dumps(result)

    def test_tuple_becomes_list(self) -> None:
        """tuple → list（JSON 无 tuple 类型）"""
        dt = DateTime(2026, 7, 12, tzinfo=timezone.utc)
        result = _to_jsonable((dt, "str", 42))
        assert isinstance(result, list)
        assert result[0] == "2026-07-12T00:00:00.000000000+00:00"
        assert result[1] == "str"
        assert result[2] == 42

    def test_nested_dict_in_list(self) -> None:
        """嵌套结构：list 内含 dict 含 DateTime"""
        dt = DateTime(2026, 7, 12, 10, 0, 0, 0, tzinfo=timezone.utc)
        data = [{"updated_at": dt, "name": "a"}, {"updated_at": dt, "name": "b"}]
        result = _to_jsonable(data)
        assert result[0]["updated_at"] == "2026-07-12T10:00:00.000000000+00:00"
        assert result[1]["updated_at"] == "2026-07-12T10:00:00.000000000+00:00"
        json.dumps(result)


# ────────── 3. 原生类型透传 ──────────


class TestToJsonablePrimitives:
    """原生 Python 类型应原样返回"""

    @pytest.mark.parametrize(
        "value",
        [
            "string",
            42,
            3.14,
            True,
            False,
            None,
        ],
    )
    def test_primitive_passthrough(self, value: object) -> None:
        assert _to_jsonable(value) == value


# ────────── 4. JSON / FastAPI 序列化验证（核心场景） ──────────


class TestSerializationEndToEnd:
    """验证修复的核心场景：模拟 query_entity 返回的 properties(n)"""

    def test_properties_dict_json_serializable(self) -> None:
        """模拟 query_entity 返回的 properties(n) dict 可被 json.dumps 序列化"""
        # 模拟 Neo4j 节点 properties：含 Cypher datetime() 写入的 updated_at
        dt = DateTime(2026, 7, 12, 10, 30, 0, 0, tzinfo=timezone.utc)
        props = {
            "name": "nginx",
            "entity_type": "Service",
            "source_doc_id": "doc-abc123",
            "confidence": 0.92,
            "updated_at": dt,  # ← 修复前会导致 json.dumps 失败
        }

        # 修复前：json.dumps(props) 抛 TypeError: Object of type DateTime is not JSON serializable
        # 修复后：经 _to_jsonable 转换后可序列化
        converted = _to_jsonable(props)
        serialized = json.dumps(converted)
        assert "2026-07-12T10:30:00" in serialized
        assert "nginx" in serialized

    def test_properties_dict_fastapi_jsonable_encoder(self) -> None:
        """模拟 query_entity 返回的 properties(n) dict 可被 FastAPI jsonable_encoder 处理

        修复前：jsonable_encoder(DateTime) 抛 ValueError
        修复后：经 _to_jsonable 转换为字符串后可正常处理
        """
        dt = DateTime(2026, 7, 12, 10, 30, 0, 0, tzinfo=timezone.utc)
        props = {
            "name": "nginx",
            "entity_type": "Service",
            "confidence": 0.92,
            "updated_at": dt,
        }

        # 修复前：jsonable_encoder(props) 抛 ValueError
        # 修复后：经 _to_jsonable 转换后可正常处理
        converted = _to_jsonable(props)
        encoded = jsonable_encoder(converted)
        assert encoded["updated_at"] == "2026-07-12T10:30:00.000000000+00:00"
        assert encoded["name"] == "nginx"

    def test_date_not_garbled_dict(self) -> None:
        """Date 经 _to_jsonable 后不返回乱码 dict

        修复前：jsonable_encoder(Date) 返回 {'_Date__ordinal': 739809, ...}
        修复后：_to_jsonable 先转为 ISO 字符串，jsonable_encoder 原样保留
        """
        d = Date(2026, 7, 12)
        converted = _to_jsonable(d)
        encoded = jsonable_encoder(converted)
        assert encoded == "2026-07-12"  # 不是 {'_Date__ordinal': ...}

    def test_time_not_garbled_dict(self) -> None:
        """Time 经 _to_jsonable 后不返回乱码 dict"""
        t = Time(10, 30, 0)
        converted = _to_jsonable(t)
        encoded = jsonable_encoder(converted)
        assert encoded == "10:30:00.000000000"  # 不是 {'_Time__ticks': ...}

    def test_duration_not_garbled_array(self) -> None:
        """Duration 经 _to_jsonable 后不返回乱码数组

        修复前：json.dumps(Duration) 返回 [0, 0, 0, 3600]（乱码数组）
        修复后：_to_jsonable 先转为 ISO 字符串 "PT1H"
        """
        dur = Duration(hours=1)
        converted = _to_jsonable(dur)
        # 修复前：json.dumps(dur) 返回 [0, 0, 0, 3600]
        # 修复后：返回 "PT1H"
        serialized = json.dumps(converted)
        assert serialized == '"PT1H"'
