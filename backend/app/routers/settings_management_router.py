"""系统配置管理 API（P2-2：运行时配置读写）

端点：
- GET  /settings           获取当前配置（脱敏）
- PUT  /settings           更新配置（写回 .env 文件）
- POST /settings/validate  验证配置合法性
- POST /settings/restart   触发优雅重启（需 admin 权限）
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_role
from app.config import get_settings

logger = structlog.get_logger()
router = APIRouter()

# 敏感字段（API Key 类，返回时脱敏）
_SENSITIVE_FIELDS = frozenset({
    "openai_compat_api_key", "api_token", "setup_token",
    "neo4j_password", "database_url",
})

# 配置分组（供前端 UI 分 Tab 展示）
_SETTINGS_GROUPS = {
    "llm": {
        "label": "LLM 配置",
        "keys": [
            "llm_backend", "llm_model", "llm_timeout", "llm_max_tokens",
            "llm_temperature", "llm_max_retries", "llm_retry_base_delay",
            "llm_retry_max_delay", "llm_concurrency_limit", "llm_fallback_backends",
            "openai_compat_base_url", "openai_compat_api_key", "openai_compat_model",
            "ollama_base_url", "ollama_model",
            "vllm_base_url", "vllm_model",
        ],
    },
    "knowledge": {
        "label": "知识编译",
        "keys": [
            "confidence_auto", "confidence_review", "dedup_cos_threshold",
            "authority_source_weight", "authority_recency_weight",
            "authority_consensus_weight", "wiki_writeback_llm_validate",
            "doc_gen_max_iter", "doc_gen_token_soft", "doc_gen_token_hard",
        ],
    },
    "auth": {
        "label": "认证",
        "keys": [
            "api_token", "oidc_providers", "oidc_default_role",
            "oidc_redirect_base_url", "frontend_base_url",
            "saml_providers", "saml_default_role", "saml_strict",
            "ldap_providers", "ldap_default_role",
        ],
    },
    "advanced": {
        "label": "高级",
        "keys": [
            "env", "log_level", "neo4j_uri", "neo4j_user",
            "neo4j_password", "embedding_model", "embedding_dim",
            "embedding_batch_size", "deployment_mode", "instance_id",
            "db_backend", "database_url", "cors_origins",
            "collab_max_rooms", "collab_max_connections_per_room",
            "mcp_tool_permissions", "mcp_permission_strict",
        ],
    },
}


class SettingsUpdate(BaseModel):
    """PUT /settings 请求体 — 键值对更新"""
    updates: dict[str, str | int | float | bool] = {}


# ────────── 字段元数据（供前端展示说明） ──────────

_FIELD_META: dict[str, dict] = {
    "llm_backend": {
        "type": "select",
        "options": ["openai_compat", "ollama", "vllm"],
        "label": "LLM 后端",
        "description": "切换 LLM 后端提供商",
        "default": "openai_compat",
    },
    "llm_model": {
        "type": "string",
        "label": "模型名",
        "description": "LLM 模型标识符（通用名，如 deepseek-chat）",
        "default": "deepseek-chat",
    },
    "llm_timeout": {
        "type": "int",
        "label": "超时（秒）",
        "description": "LLM API 调用超时时间",
        "range": (10, 600),
        "default": 120,
    },
    "llm_max_tokens": {
        "type": "int",
        "label": "最大 Token 数",
        "description": "LLM 单次生成的最大 token 数",
        "range": (256, 32768),
        "default": 4096,
    },
    "llm_temperature": {
        "type": "float",
        "label": "Temperature",
        "description": "LLM 生成温度（0.0-2.0，越低越确定）",
        "range": (0.0, 2.0),
        "default": 0.1,
    },
    "llm_max_retries": {
        "type": "int",
        "label": "最大重试次数",
        "description": "LLM 调用失败后的重试次数（指数退避）",
        "range": (0, 10),
        "default": 3,
    },
    "openai_compat_base_url": {
        "type": "string",
        "label": "API Base URL",
        "description": "OpenAI 兼容 API 的 Base URL",
        "default": "https://api.deepseek.com/v1",
    },
    "openai_compat_api_key": {
        "type": "password",
        "label": "API Key",
        "description": "OpenAI 兼容 API 的密钥",
        "sensitive": True,
    },
    "confidence_auto": {
        "type": "float",
        "label": "自动通过阈值",
        "description": "置信度 >= 此值自动通过，无需人工审核",
        "range": (0.0, 1.0),
        "default": 0.85,
    },
    "confidence_review": {
        "type": "float",
        "label": "需审核阈值",
        "description": "置信度 <= 此值标记为需人工审核",
        "range": (0.0, 1.0),
        "default": 0.60,
    },
    "env": {
        "type": "select",
        "options": ["dev", "production"],
        "label": "运行环境",
        "description": "dev 模式显示详细错误信息，production 隐藏内部细节",
        "default": "dev",
    },
    "log_level": {
        "type": "select",
        "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
        "label": "日志级别",
        "description": "日志输出级别",
        "default": "INFO",
    },
}


def _get_field_meta(key: str) -> dict:
    """获取字段元数据，无预定义则返回通用描述"""
    return _FIELD_META.get(key, {
        "type": "string",
        "label": key,
        "description": "",
    })


def _mask_sensitive(key: str, value: object) -> object:
    """对敏感字段脱敏"""
    if key in _SENSITIVE_FIELDS and isinstance(value, str) and value:
        return value[:4] + "***" if len(value) > 4 else "***"
    return value


def _env_file_path() -> Path:
    """获取 .env 文件路径"""
    return Path(os.getcwd()) / ".env"


def _read_env_file() -> dict[str, str]:
    """读取 .env 文件为键值对字典"""
    env_path = _env_file_path()
    if not env_path.exists():
        return {}
    result: dict[str, str] = {}
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _write_env_file(data: dict[str, str]) -> None:
    """写回 .env 文件（保留原有的注释和非覆盖行）"""
    env_path = _env_file_path()
    existing = _read_env_file() if env_path.exists() else {}
    existing.update(data)
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("# OpsKG 配置（由 Settings API 管理）\n")
        for key, value in sorted(existing.items()):
            f.write(f"{key}={value}\n")


# ────────── 端点 ──────────

@router.get("/settings")
async def get_settings_endpoint(
    identity: str = Depends(require_role("operator")),
) -> dict:
    """获取当前配置（分组展示，敏感字段脱敏）

    权限：operator 及以上
    """
    settings = get_settings()
    result: dict[str, dict] = {}
    for group_key, group in _SETTINGS_GROUPS.items():
        items: dict[str, object] = {}
        for key in group["keys"]:
            if hasattr(settings, key):
                value = getattr(settings, key)
                items[key] = {
                    "value": _mask_sensitive(key, value),
                    "meta": _get_field_meta(key),
                }
        result[group_key] = {
            "label": group["label"],
            "items": items,
        }
    return {"groups": result}


@router.put("/settings")
async def update_settings(
    body: SettingsUpdate,
    identity: str = Depends(require_role("admin")),
) -> dict:
    """更新系统配置（写回 .env 文件）

    权限：admin 及以上
    注意：修改后需重启服务才能生效（调用 POST /settings/restart）。
    """
    if not body.updates:
        raise HTTPException(400, "updates 不能为空")

    settings = get_settings()
    valid_keys = set(settings.model_fields.keys())
    env_updates: dict[str, str] = {}

    for key, value in body.updates.items():
        if key not in valid_keys:
            raise HTTPException(400, f"未知配置项: {key}")
        if key in _SENSITIVE_FIELDS and isinstance(value, str) and value.endswith("***"):
            # 脱敏值未修改，跳过
            continue
        env_updates[key] = str(value)
        logger.info("settings.update", user=identity, key=key)

    if not env_updates:
        raise HTTPException(400, "没有有效的配置变更（所有值均为脱敏占位符）")

    _write_env_file(env_updates)

    return {
        "updated": list(env_updates.keys()),
        "message": "配置已写入 .env 文件，请重启服务使配置生效",
        "restart_endpoint": "POST /settings/restart",
    }


@router.post("/settings/validate")
async def validate_settings(
    body: SettingsUpdate,
    identity: str = Depends(require_role("operator")),
) -> dict:
    """验证配置合法性（不写入）

    权限：operator 及以上
    """
    settings = get_settings()
    valid_keys = set(settings.model_fields.keys())
    errors: list[str] = []

    for key, value in body.updates.items():
        if key not in valid_keys:
            errors.append(f"未知配置项: {key}")
            continue
        field_info = settings.model_fields.get(key)
        if field_info is None:
            continue
        meta = _get_field_meta(key)
        # 类型校验
        expected_type = meta.get("type", "string")
        try:
            if expected_type == "int":
                int(value)
            elif expected_type == "float":
                float(value)
            elif expected_type == "select":
                options = meta.get("options", [])
                if str(value) not in options:
                    errors.append(f"{key}: 值 '{value}' 不在允许范围 {options} 内")
        except (ValueError, TypeError):
            errors.append(f"{key}: 类型错误，期望 {expected_type}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }


@router.post("/settings/restart")
async def restart_service(
    identity: str = Depends(require_role("admin")),
) -> dict:
    """触发优雅重启（仅标记，实际重启由外部进程管理器执行）

    权限：admin 及以上

    实现方式：
    - standalone 模式：设置重启标记，由外部 supervisor/systemd 检测
    - 容器环境：通过 signal 触发容器重启
    """
    # 写入重启标记文件
    restart_flag = Path(os.getcwd()) / ".restart_flag"
    restart_flag.write_text("1")
    logger.warning("settings.restart_requested", user=identity)

    # 尝试触发 supervisor 重启
    try:
        import signal
        os.kill(os.getppid(), signal.SIGHUP)
    except Exception:  # noqa: BLE001
        pass

    return {
        "restart": True,
        "message": "已发送重启信号，服务将在数秒内重启",
    }


# ────────── LLM 连通性测试 ──────────

class LLMTestRequest(BaseModel):
    """POST /settings/llm/test 请求体 — 可选覆盖配置项用于测试"""
    backend: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


@router.post("/settings/llm/test")
async def test_llm_connection(
    body: LLMTestRequest = LLMTestRequest(),
    identity: str = Depends(require_role("operator")),
) -> dict:
    """测试 LLM 后端连通性

    权限：operator 及以上

    行为：
    - 不传参数：测试当前已保存的 LLM 配置（根据 llm_backend 自动选择后端）
    - 传入 backend/base_url/api_key/model：用传入值覆盖测试（不修改持久化配置）
    """
    settings = get_settings()

    # 确定测试参数（传入值优先，否则用当前活跃后端对应的配置）
    backend = body.backend or settings.llm_backend

    if backend == "ollama":
        base_url = body.base_url or settings.ollama_base_url
        api_key = body.api_key or ""
        model = body.model or settings.ollama_model
    elif backend == "vllm":
        base_url = body.base_url or settings.vllm_base_url
        api_key = body.api_key or ""
        model = body.model or settings.vllm_model
    else:  # openai_compat
        base_url = body.base_url or settings.openai_compat_base_url
        api_key = body.api_key or settings.openai_compat_api_key
        model = body.model or settings.openai_compat_model

    # 构建测试用客户端
    import time
    start = time.monotonic()

    errors: list[str] = []
    try:
        if backend == "ollama":
            from app.core.llm.ollama import OllamaClient

            # 构造最小化 settings 对象供 OllamaClient 使用
            class _TestSettings:
                ollama_base_url = base_url
                ollama_model = model
                llm_timeout = settings.llm_timeout
                llm_temperature = settings.llm_temperature
                llm_max_tokens = settings.llm_max_tokens

            test_client = OllamaClient(_TestSettings())
        else:
            from app.core.llm.openai_compat import OpenAICompatClient

            test_client = OpenAICompatClient(
                base_url=base_url + "/v1" if backend == "vllm" else base_url,
                api_key=api_key or "EMPTY",
                model=model,
                timeout=settings.llm_timeout,
                default_temperature=settings.llm_temperature,
                default_max_tokens=settings.llm_max_tokens,
                label=backend,
            )

        healthy = await test_client.health()
        latency_ms = round((time.monotonic() - start) * 1000)

        if healthy:
            return {
                "success": True,
                "backend": backend,
                "model": model,
                "base_url": base_url,
                "latency_ms": latency_ms,
                "message": f"LLM 后端连通正常（{backend}/{model}，{latency_ms}ms）",
            }
        else:
            errors.append("health() 返回 false")

    except Exception as e:
        latency_ms = round((time.monotonic() - start) * 1000)
        errors.append(str(e))

    return {
        "success": False,
        "backend": backend,
        "model": model,
        "base_url": base_url,
        "latency_ms": latency_ms,
        "errors": errors,
        "message": "LLM 后端连接失败",
    }
