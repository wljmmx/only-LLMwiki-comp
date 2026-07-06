"""应用配置驱动。LLM 后端由 LLM_BACKEND 切换：ollama | vllm | openai_compat"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "dev"
    log_level: str = "INFO"

    # LLM
    llm_backend: Literal["ollama", "vllm", "openai_compat"] = "openai_compat"
    llm_model: str = "deepseek-chat"
    llm_timeout: int = 120
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.1

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    # vLLM
    vllm_base_url: str = "http://localhost:8000"
    vllm_model: str = "Qwen2.5-14B-Instruct"

    # OpenAI 兼容（DeepSeek/通义）
    openai_compat_base_url: str = "https://api.deepseek.com/v1"
    openai_compat_api_key: str = ""
    openai_compat_model: str = "deepseek-chat"

    # Embedding（P2-1.1 向量检索）
    # 留空则关闭向量检索，搜索退化为纯关键词
    embedding_model: str = ""
    embedding_dim: int = 1024
    embedding_batch_size: int = 16

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    # 抽取门控
    confidence_auto: float = 0.85
    confidence_review: float = 0.60
    dedup_cos_threshold: float = 0.92

    # 知识编译
    authority_source_weight: float = 0.5
    authority_recency_weight: float = 0.3
    authority_consensus_weight: float = 0.2

    # 文档生成
    doc_gen_max_iter: int = 3
    doc_gen_token_soft: int = 120_000
    doc_gen_token_hard: int = 200_000

    # 认证（P0-2）— 留空则关闭认证（开发模式）
    api_token: str = ""

    # OIDC / OAuth2 SSO（P3-1 完整 SSO）
    # oidc_providers 是 JSON 字符串，格式：
    # [{"name":"google","display_name":"Google","client_id":"...","client_secret":"...",
    #   "discovery_url":"https://accounts.google.com/.well-known/openid-configuration",
    #   "scopes":["openid","email","profile"]}, ...]
    # 留空则关闭 OIDC SSO
    oidc_providers: str = ""
    # OIDC 用户默认角色（首次登录自动创建）
    oidc_default_role: str = "viewer"
    # 后端公网回调基址（OIDC callback 端点完整 URL = oidc_redirect_base_url + /auth/oidc/{provider}/callback）
    # 留空则从请求 Host header 推断（开发模式可用 http://localhost:8000）
    oidc_redirect_base_url: str = ""
    # 前端登录后的回调页（后端完成 OIDC 后重定向到此 URL 并附带 ?token=...&redirect=...）
    frontend_base_url: str = "http://localhost:5173"

    # SAML 2.0 SSO（S13-1 企业级 SSO 补齐）
    # saml_providers 是 JSON 字符串，格式见 backend/app/auth/saml.py 模块文档
    # 留空则关闭 SAML SSO
    saml_providers: str = ""
    # SAML 用户默认角色（首次登录自动创建）
    saml_default_role: str = "viewer"
    # SAML 严格模式（生产环境推荐 true：验证签名 + 不允许未签名的 assertion）
    saml_strict: bool = True

    # HA 高可用（P3-4）
    # 实例 ID 用于多实例区分；不传则用 hostname + pid 自动生成
    instance_id: str = ""
    # 部署模式：standalone | replicated
    # standalone: 单实例（默认）
    # replicated: 多实例 + 共享存储（需 NFS 或类似方案）
    deployment_mode: str = "standalone"


@lru_cache
def get_settings() -> Settings:
    return Settings()
