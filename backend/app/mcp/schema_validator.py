"""P2-5.8 MCP 工具入参 JSON Schema 运行时校验。

轻量级校验器，覆盖现有 TOOLS 定义的 schema 关键字：
- type: object / string / integer / number / boolean / array
- required: 必填字段列表
- properties: 字段 schema
- enum: 枚举值
- minimum / maximum: 数值范围
- minLength / maxLength: 字符串长度
- items: 数组元素 schema
- default: 缺失字段自动填充默认值（不视为错误）

校验失败时返回错误消息列表，protocol.handle_request 据此返回
JSON-RPC -32602 Invalid Params。

不引入 jsonschema 依赖（保持后端轻量），实现覆盖 MCP 协议常见用法。
"""
from __future__ import annotations

from typing import Any


def validate_args(args: Any, schema: dict) -> list[str]:
    """校验 args 是否符合 JSON Schema

    Args:
        args: 工具入参（通常为 dict）
        schema: 工具的 inputSchema

    Returns:
        错误消息列表（空列表表示校验通过）
    """
    if not schema:
        return []
    return _validate(args, schema, path="")


def fill_defaults(args: dict, schema: dict) -> dict:
    """填充 schema 中 default 字段（缺失时）

    Args:
        args: 原始入参 dict
        schema: 工具的 inputSchema

    Returns:
        填充 default 后的 args（原对象被修改并返回）
    """
    if not isinstance(args, dict) or not schema:
        return args
    properties = schema.get("properties") or {}
    for key, prop_schema in properties.items():
        if (
            key not in args
            and isinstance(prop_schema, dict)
            and "default" in prop_schema
        ):
            args[key] = prop_schema["default"]
    return args


def _validate(value: Any, schema: dict, path: str) -> list[str]:
    """递归校验 value 是否符合 schema

    Args:
        value: 待校验值
        schema: JSON Schema 片段
        path: 当前字段路径（用于错误消息，如 "params.query"）

    Returns:
        错误消息列表
    """
    errors: list[str] = []
    if not schema:
        return errors

    # type 校验
    type_str = schema.get("type")
    if type_str and not _check_type(value, type_str):
        errors.append(f"{path or 'root'}: 期望类型 {type_str}，实际 {type(value).__name__}")
        return errors  # 类型不对，后续校验无意义

    # enum 校验
    if "enum" in schema and value not in schema["enum"]:
        errors.append(
            f"{path or 'root'}: 值 {value!r} 不在 enum {schema['enum']} 中"
        )

    # 数值范围
    if type_str in ("integer", "number") and isinstance(value, (int, float)):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: 值 {value} 小于 minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: 值 {value} 大于 maximum {schema['maximum']}")

    # 字符串长度
    if type_str == "string" and isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: 长度 {len(value)} 小于 minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: 长度 {len(value)} 大于 maxLength {schema['maxLength']}")

    # object: required + properties
    if type_str == "object" and isinstance(value, dict):
        required = schema.get("required") or []
        for field in required:
            if field not in value:
                errors.append(f"{path}.{field}: 缺少必填字段")

        properties = schema.get("properties") or {}
        for key, sub_schema in properties.items():
            if key in value and sub_schema:
                sub_path = f"{path}.{key}" if path else key
                errors.extend(_validate(value[key], sub_schema, sub_path))

    # array: items
    if type_str == "array" and isinstance(value, list):
        items_schema = schema.get("items")
        if items_schema:
            for i, item in enumerate(value):
                sub_path = f"{path}[{i}]"
                errors.extend(_validate(item, items_schema, sub_path))

    return errors


def _check_type(value: Any, type_str: str) -> bool:
    """检查 value 是否符合 JSON Schema type"""
    if type_str == "object":
        return isinstance(value, dict)
    if type_str == "array":
        return isinstance(value, list)
    if type_str == "string":
        return isinstance(value, str)
    if type_str == "integer":
        # bool 是 int 的子类，需排除
        return isinstance(value, int) and not isinstance(value, bool)
    if type_str == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_str == "boolean":
        return isinstance(value, bool)
    if type_str == "null":
        return value is None
    return True  # 未知类型，跳过
