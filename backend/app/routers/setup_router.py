"""Setup Wizard API（开箱配置引导）

端点：
- GET  /setup/status           配置完成度检查（不暴露敏感值）
- POST /setup/test-llm         测试 LLM 连通（用当前 settings 或请求体覆盖）
- POST /setup/test-neo4j       测试 Neo4j 连通（用当前 settings 或请求体覆盖）
- POST /setup/generate-command 根据输入生成可复制的 docker 启动命令

设计：
- 只读检测当前环境配置状态，不写 .env 文件（避免敏感信息持久化风险）
- test-* 端点接受请求体覆盖配置，用于 wizard 中"填了 key 但还没重启"的场景
- generate-command 返回 docker run / docker compose 命令字符串，用户自行执行
- 所有端点无需认证（setup wizard 在认证配置前需要可用）
"""

from __future__ import annotations

import os
from typing import Literal

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import get_settings

logger = structlog.get_logger()

router = APIRouter()


# ────────── Schemas ──────────


class SetupStatusResponse(BaseModel):
    """配置完成度状态"""

    # LLM
    llm_backend: str
    llm_configured: bool  # API key 是否已设置（不返回 key 本身）
    llm_backend_options: list[str] = ["openai_compat", "ollama", "vllm"]

    # Neo4j
    neo4j_uri: str
    neo4j_configured: bool  # 是否非默认 password

    # 认证
    auth_enabled: bool  # OPSKG_API_TOKEN 是否设置
    bootstrap_admin_configured: bool  # bootstrap admin 是否已配置

    # 可观测性
    tracing_enabled: bool

    # 总体完成度
    ready: bool  # LLM + Neo4j + 认证 三项均 OK
    missing: list[str]  # 缺失项列表（用于 wizard 高亮）


class TestLLMRequest(BaseModel):
    """测试 LLM 连通（请求体可选，未传则用当前 settings）"""

    backend: Literal["openai_compat", "ollama", "vllm"] | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class TestLLMResponse(BaseModel):
    ok: bool
    backend: str
    model: str
    latency_ms: int | None = None
    error: str | None = None


class TestNeo4jRequest(BaseModel):
    """测试 Neo4j 连通（请求体可选，未传则用当前 settings）"""

    uri: str | None = None
    user: str | None = None
    password: str | None = None


class TestNeo4jResponse(BaseModel):
    ok: bool
    uri: str
    version: str | None = None
    latency_ms: int | None = None
    error: str | None = None


class GenerateCommandRequest(BaseModel):
    """生成 docker 启动命令"""

    mode: Literal["docker-run", "docker-compose"] = "docker-compose"
    llm_backend: Literal["openai_compat", "ollama", "vllm"] = "openai_compat"
    openai_compat_base_url: str = "https://api.deepseek.com/v1"
    openai_compat_api_key: str = ""
    openai_compat_model: str = "deepseek-chat"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    vllm_base_url: str = "http://localhost:8000"
    vllm_model: str = "Qwen2.5-14B-Instruct"
    neo4j_password: str = "password"
    enable_auth: bool = False
    api_token: str = ""
    bootstrap_admin_user: str = "admin"
    bootstrap_admin_password: str = "admin"
    port: int = Field(default=80, ge=1, le=65535)
    workers: int = Field(default=2, ge=1, le=32)


class GenerateCommandResponse(BaseModel):
    command: str
    env_file_content: str  # 配套的 .env 文件内容


# ────────── 端点 ──────────


@router.get("/setup/status", response_model=SetupStatusResponse)
async def get_setup_status() -> SetupStatusResponse:
    """配置完成度检查（只读，不暴露敏感值）

    用于前端 SetupWizard 首屏检测：哪些已配置、哪些缺失。
    """
    settings = get_settings()

    # LLM 配置检查
    llm_configured = False
    if settings.llm_backend == "openai_compat":
        llm_configured = bool(settings.openai_compat_api_key)
    elif settings.llm_backend == "ollama":
        llm_configured = bool(settings.ollama_base_url)
    elif settings.llm_backend == "vllm":
        llm_configured = bool(settings.vllm_base_url)

    # Neo4j 配置检查（非默认 password 视为已配置；默认 password 也算可用）
    neo4j_configured = bool(settings.neo4j_uri) and bool(settings.neo4j_user)

    # 认证检查
    auth_enabled = bool(settings.api_token)
    bootstrap_admin_configured = bool(
        os.getenv("OPSKG_BOOTSTRAP_ADMIN_USER", "admin")
        and os.getenv("OPSKG_BOOTSTRAP_ADMIN_PASSWORD", "admin")
    )

    # 可观测性
    tracing_enabled = bool(getattr(settings, "_tracing_enabled", False)) or (
        os.getenv("OPSKG_TRACING_ENABLED", "0") == "1"
    )

    # 完成度
    missing: list[str] = []
    if not llm_configured:
        missing.append("llm")
    if not neo4j_configured:
        missing.append("neo4j")
    if not auth_enabled and not bootstrap_admin_configured:
        missing.append("auth")

    ready = llm_configured and neo4j_configured and (
        auth_enabled or bootstrap_admin_configured
    )

    return SetupStatusResponse(
        llm_backend=settings.llm_backend,
        llm_configured=llm_configured,
        llm_backend_options=["openai_compat", "ollama", "vllm"],
        neo4j_uri=settings.neo4j_uri,
        neo4j_configured=neo4j_configured,
        auth_enabled=auth_enabled,
        bootstrap_admin_configured=bootstrap_admin_configured,
        tracing_enabled=tracing_enabled,
        ready=ready,
        missing=missing,
    )


@router.post("/setup/test-llm", response_model=TestLLMResponse)
async def test_llm(req: TestLLMRequest) -> TestLLMResponse:
    """测试 LLM 连通性

    用请求体覆盖当前 settings（用于 wizard 中"填了 key 但还没重启"场景）。
    发送一个最小 chat 请求验证连通。
    """
    import time

    settings = get_settings()
    backend = req.backend or settings.llm_backend

    # 构建临时客户端配置（不修改全局 settings）
    if backend == "openai_compat":
        base_url = req.base_url or settings.openai_compat_base_url
        api_key = req.api_key or settings.openai_compat_api_key
        model = req.model or settings.openai_compat_model
        if not api_key:
            return TestLLMResponse(
                ok=False, backend=backend, model=model, error="API key 未配置"
            )
    elif backend == "ollama":
        base_url = req.base_url or settings.ollama_base_url
        api_key = req.api_key or "ollama"  # ollama 不需要 key
        model = req.model or settings.ollama_model
    elif backend == "vllm":
        base_url = req.base_url or settings.vllm_base_url
        api_key = req.api_key or "vllm"  # vllm 本地通常不需要 key
        model = req.model or settings.vllm_model
    else:
        return TestLLMResponse(
            ok=False, backend=backend, model="", error=f"未知 backend: {backend}"
        )

    # 用 openai SDK 测试（openai_compat / vllm / ollama 均兼容 OpenAI 协议）
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return TestLLMResponse(
            ok=False, backend=backend, model=model, error="openai SDK 未安装"
        )

    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=15)
    start = time.time()
    try:
        await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            temperature=0,
        )
        latency = int((time.time() - start) * 1000)
        # 收到响应即视为连通
        return TestLLMResponse(
            ok=True, backend=backend, model=model, latency_ms=latency
        )
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return TestLLMResponse(
            ok=False,
            backend=backend,
            model=model,
            latency_ms=latency,
            error=str(e)[:300],
        )


@router.post("/setup/test-neo4j", response_model=TestNeo4jResponse)
async def test_neo4j(req: TestNeo4jRequest) -> TestNeo4jResponse:
    """测试 Neo4j 连通性"""
    import time

    settings = get_settings()
    uri = req.uri or settings.neo4j_uri
    user = req.user or settings.neo4j_user
    password = req.password or settings.neo4j_password

    try:
        from neo4j import GraphDatabase
        from neo4j.exceptions import Neo4jError, ServiceUnavailable
    except ImportError:
        return TestNeo4jResponse(
            ok=False, uri=uri, error="neo4j 驱动未安装"
        )

    start = time.time()
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=10)
        with driver.session() as session:
            result = session.run("RETURN 1 AS n")
            result.single()  # 强制消费
            # 获取版本（best-effort，失败不阻断）
            version: str | None = None
            try:
                v_result = session.run("CALL dbms.components() YIELD versions RETURN versions[0] AS v")
                v_record = v_result.single()
                if v_record:
                    version = v_record["v"]
            except Exception:  # noqa: BLE001
                pass
        driver.close()
        latency = int((time.time() - start) * 1000)
        return TestNeo4jResponse(
            ok=True, uri=uri, version=version, latency_ms=latency
        )
    except (Neo4jError, ServiceUnavailable, OSError) as e:
        latency = int((time.time() - start) * 1000)
        return TestNeo4jResponse(
            ok=False, uri=uri, latency_ms=latency, error=str(e)[:300]
        )
    except Exception as e:  # noqa: BLE001
        latency = int((time.time() - start) * 1000)
        return TestNeo4jResponse(
            ok=False, uri=uri, latency_ms=latency, error=str(e)[:300]
        )


@router.post("/setup/generate-command", response_model=GenerateCommandResponse)
async def generate_command(req: GenerateCommandRequest) -> GenerateCommandResponse:
    """生成可复制的 docker 启动命令 + .env 文件内容

    用户在 wizard 填完参数后，复制命令自行执行，避免后端写文件的安全风险。
    """
    # 构建 .env 内容
    env_lines: list[str] = [
        "# OpsKG .env（由 Setup Wizard 生成）",
        "ENV=production",
        "LOG_LEVEL=INFO",
        f"OPSKG_UVICORN_WORKERS={req.workers}",
        "",
        "# LLM",
        f"LLM_BACKEND={req.llm_backend}",
    ]
    if req.llm_backend == "openai_compat":
        env_lines.extend([
            f"OPENAI_COMPAT_BASE_URL={req.openai_compat_base_url}",
            f"OPENAI_COMPAT_API_KEY={req.openai_compat_api_key}",
            f"OPENAI_COMPAT_MODEL={req.openai_compat_model}",
        ])
    elif req.llm_backend == "ollama":
        env_lines.extend([
            f"OLLAMA_BASE_URL={req.ollama_base_url}",
            f"OLLAMA_MODEL={req.ollama_model}",
        ])
    elif req.llm_backend == "vllm":
        env_lines.extend([
            f"VLLM_BASE_URL={req.vllm_base_url}",
            f"VLLM_MODEL={req.vllm_model}",
        ])

    env_lines.extend([
        "",
        "# Neo4j",
        "NEO4J_URI=bolt://neo4j:7687",
        "NEO4J_USER=neo4j",
        f"NEO4J_PASSWORD={req.neo4j_password}",
        "",
        "# 认证",
    ])
    if req.enable_auth and req.api_token:
        env_lines.append(f"OPSKG_API_TOKEN={req.api_token}")
    else:
        env_lines.append("OPSKG_API_TOKEN=")
    env_lines.extend([
        f"OPSKG_BOOTSTRAP_ADMIN_USER={req.bootstrap_admin_user}",
        f"OPSKG_BOOTSTRAP_ADMIN_PASSWORD={req.bootstrap_admin_password}",
    ])
    env_file_content = "\n".join(env_lines) + "\n"

    # 构建命令
    if req.mode == "docker-compose":
        command = (
            "# 1. 保存上方 .env 内容到项目根目录的 .env 文件\n"
            "# 2. 启动（Neo4j + OpsKG 单镜像）\n"
            "docker compose up -d\n\n"
            "# 3. 查看日志\n"
            "docker compose logs -f opskg\n\n"
            "# 4. 访问 http://localhost\n"
            f"#    （如需改端口，修改 docker-compose.yml 的 ports: \"{req.port}:80\"）"
        )
    else:
        # docker run（单容器，不含 Neo4j，需用户自行启动 Neo4j）
        env_flags = " \\\n  ".join([
            "-e ENV=production",
            f"-e OPSKG_UVICORN_WORKERS={req.workers}",
            f"-e LLM_BACKEND={req.llm_backend}",
        ])
        if req.llm_backend == "openai_compat":
            env_flags += f" \\\n  -e OPENAI_COMPAT_BASE_URL={req.openai_compat_base_url}"
            env_flags += f" \\\n  -e OPENAI_COMPAT_API_KEY={req.openai_compat_api_key}"
            env_flags += f" \\\n  -e OPENAI_COMPAT_MODEL={req.openai_compat_model}"
        elif req.llm_backend == "ollama":
            env_flags += f" \\\n  -e OLLAMA_BASE_URL={req.ollama_base_url}"
            env_flags += f" \\\n  -e OLLAMA_MODEL={req.ollama_model}"
        elif req.llm_backend == "vllm":
            env_flags += f" \\\n  -e VLLM_BASE_URL={req.vllm_base_url}"
            env_flags += f" \\\n  -e VLLM_MODEL={req.vllm_model}"

        env_flags += " \\\n  -e NEO4J_URI=bolt://host.docker.internal:7687"
        env_flags += f" \\\n  -e NEO4J_PASSWORD={req.neo4j_password}"

        if req.enable_auth and req.api_token:
            env_flags += f" \\\n  -e OPSKG_API_TOKEN={req.api_token}"
        env_flags += f" \\\n  -e OPSKG_BOOTSTRAP_ADMIN_USER={req.bootstrap_admin_user}"
        env_flags += f" \\\n  -e OPSKG_BOOTSTRAP_ADMIN_PASSWORD={req.bootstrap_admin_password}"

        command = (
            "# 1. 构建镜像\n"
            "docker build -t opskg:latest .\n\n"
            "# 2. 启动 Neo4j（如需知识图谱功能）\n"
            f"docker run -d --name opskg-neo4j \\\n"
            f"  -p 7474:7474 -p 7687:7687 \\\n"
            f"  -e NEO4J_AUTH=neo4j/{req.neo4j_password} \\\n"
            f"  -v neo4j_data:/data \\\n"
            f"  neo4j:5-community\n\n"
            "# 3. 启动 OpsKG 单镜像\n"
            f"docker run -d --name opskg \\\n"
            f"  -p {req.port}:80 \\\n"
            f"  {env_flags} \\\n"
            f"  -v opskg_data:/app/data \\\n"
            f"  --link opskg-neo4j:neo4j \\\n"
            f"  opskg:latest\n\n"
            f"# 4. 访问 http://localhost:{req.port}"
        )

    return GenerateCommandResponse(
        command=command, env_file_content=env_file_content
    )
