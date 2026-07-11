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
    # P2-1: LLM 调用弹性
    # 重试次数（0=不重试），指数退避 + 抖动
    llm_max_retries: int = 3
    # 重试基础延迟（秒），实际延迟 = base * 2^attempt + jitter
    llm_retry_base_delay: float = 1.0
    # 重试最大延迟（秒）
    llm_retry_max_delay: float = 30.0
    # 并发限流（同时进行的 LLM 请求数）
    llm_concurrency_limit: int = 10
    # 降级后端链（逗号分隔，如 "ollama,vllm"）
    # 主后端失败后按顺序尝试 fallback 后端
    # 留空则不启用降级
    llm_fallback_backends: str = ""

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
    # P3-2: 知识复利回写校验
    # True 时回写前用 LLM 自校验事实是否由回答支持（避免幻觉污染）
    # 规则校验（非空/支撑/去重）始终运行，此开关仅控制 LLM 校验层
    wiki_writeback_llm_validate: bool = True

    # 文档生成
    doc_gen_max_iter: int = 3
    doc_gen_token_soft: int = 120_000
    doc_gen_token_hard: int = 200_000

    # 认证（P0-2）— 留空则关闭认证（开发模式）
    api_token: str = ""

    # Setup Wizard 一次性 token（P0-3: 防止 setup 端点被滥用）
    # 设置后 /setup/test-* 和 /setup/generate-command 需携带此 token
    # 留空则：bootstrap admin 已配置时要求 admin 登录，未配置时允许首次配置
    setup_token: str = ""

    # CORS 跨域（P0-7）
    # 逗号分隔的允许 origin 列表，如 "http://localhost:5173,https://opskg.example.com"
    # 留空则默认允许 frontend_base_url
    # 设为 "*" 则允许所有（仅开发环境，生产环境不推荐）
    cors_origins: str = ""

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

    # LDAP / Active Directory 认证（S13-2 企业级 SSO 补齐）
    # ldap_providers 是 JSON 字符串，格式见 backend/app/auth/ldap.py 模块文档
    # 留空则关闭 LDAP 认证
    ldap_providers: str = ""
    # LDAP 用户默认角色（首次登录自动创建）
    ldap_default_role: str = "viewer"

    # HA 高可用（P3-4）
    # 实例 ID 用于多实例区分；不传则用 hostname + pid 自动生成
    instance_id: str = ""
    # 部署模式：standalone | replicated
    # standalone: 单实例（默认）
    # replicated: 多实例 + 共享存储（需 NFS 或类似方案）
    deployment_mode: str = "standalone"

    # 数据库后端（P1-6: 存储层迁移评估预留）
    # sqlite: 当前默认，11 个独立 .db 文件（单机）
    # postgresql: 未来迁移目标，支持多实例 HA
    # 评估结论：迁移可行（~1080 行改动），推荐 PostgreSQL（ON CONFLICT/JSONB/tsvector 兼容性最佳）
    # 迁移路径：6 阶段渐进式（低风险单 DB → 共享 auth.db → 共享 events.db → FTS5 重写 → 备份重写）
    db_backend: Literal["sqlite", "postgresql"] = "sqlite"
    # PostgreSQL 连接串（db_backend=postgresql 时使用）
    # 格式：postgresql://user:password@host:5432/opskg
    database_url: str = ""

    # MCP 工具权限控制（P2-5）
    # JSON 字符串，格式：{"tool_name": "min_role", ...}
    # 角色层级：viewer < operator < admin
    # 留空则使用工具默认权限（见 TOOL_REQUIRED_ROLES）
    # 示例：{"transition_incident": "admin", "merge_topology_aliases": "admin"}
    mcp_tool_permissions: str = ""
    # MCP 工具权限检查是否严格模式
    # True: 未登录用户（dev 模式）拒绝所有需权限的工具
    # False: 未登录用户（dev 模式）放行所有工具（向后兼容）
    mcp_permission_strict: bool = False

    # 实时协作 Hub 上限（S16-4 多房间压测防护）
    # 单实例最大房间数；超过则新房间创建被拒绝
    collab_max_rooms: int = 1000
    # 单房间最大连接数；超过则新连接被拒绝（房间满）
    collab_max_connections_per_room: int = 50


@lru_cache
def get_settings() -> Settings:
    return Settings()
