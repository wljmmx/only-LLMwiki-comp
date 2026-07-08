# OpsKG — LLM 驱动的运维知识图谱

> 基于 Karpathy LLM Wiki 范式的运维知识管理系统：把 raw 文档编译为结构化 Markdown Wiki，建立双向链接，维护知识网络健康。

![Version](https://img.shields.io/badge/version-0.0.1-blue)
![License](https://img.shields.io/badge/license-Apache--2.0-green)
![Docker](https://img.shields.io/badge/docker-ghcr.io-blue)

## 一键部署（Docker）

```bash
# 1. 下载配置文件（也可留空，启动后由 UI Setup Wizard 引导）
curl -sSL https://raw.githubusercontent.com/wljmmx/only-LLMwiki-comp/main/.env.example -o .env
curl -sSL https://raw.githubusercontent.com/wljmmx/only-LLMwiki-comp/main/docker-compose.yml -o docker-compose.yml

# 2. 启动（拉取 ghcr.io 预构建镜像 + Neo4j）
docker compose up -d

# 3. 访问 http://localhost（首次自动跳转 Setup Wizard 引导配置）
#    Neo4j 控制台 http://localhost:7474（neo4j / password）
```

镜像地址：`ghcr.io/wljmmx/only-llmwiki-comp:0.0.1`
支持架构：`linux/amd64` + `linux/arm64`

<details>
<summary>或使用 docker run（单容器，需自行启动 Neo4j）</summary>

```bash
docker run -d --name opskg \
  -p 80:80 \
  -e OPENAI_COMPAT_API_KEY=sk-xxx \
  -e NEO4J_URI=bolt://host.docker.internal:7687 \
  -e NEO4J_PASSWORD=password \
  -e OPSKG_BOOTSTRAP_ADMIN_USER=admin \
  -e OPSKG_BOOTSTRAP_ADMIN_PASSWORD=admin123 \
  -v opskg_data:/app/data \
  ghcr.io/wljmmx/only-llmwiki-comp:0.0.1
```
</details>

## 项目特色

- **三层架构**：L1 Raw（原始文档）→ L2 Wiki（LLM 编译的结构化页面）→ L3 Schema（AGENTS.md 规范层）
- **四个 Workflow**：Ingest（编译）/ Query（问答）/ Lint（健康检查）/ Maintain（漂移修正）
- **[[wikilink]] 双向链接**：自动维护 backlink，知识网络自组织
- **知识复利**：每次问答产生的新事实可回写 wiki，知识库因使用而增值
- **AIOps 三件套**：事件关联（incident 状态机）+ 变更关联（回滚建议）+ 服务拓扑（共现推断 + 影响分析）
- **企业级就绪**：RBAC + OIDC SSO + Prometheus 指标 + OpenTelemetry 分布式追踪 + Webhook + HA 探针

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.12 + FastAPI + Pydantic 2 + structlog |
| 前端 | Vue 3.5 + TypeScript + Vite + Pinia + Naive UI |
| 存储 | SQLite（WAL）+ Neo4j 5（知识图谱）|
| LLM | DeepSeek / Ollama / vLLM（OpenAI 兼容协议）|
| 可观测性 | Prometheus + OpenTelemetry（OTLP）|
| 测试 | pytest（61 用例）+ Vitest（129 用例）+ 11 个 verify 脚本（470 验证点）|

## 快速开始

### 前置要求

- Python 3.11+
- Node.js 20+
- Neo4j 5+（可选，缺失时图功能降级）

### 一、克隆与配置

```bash
git clone https://github.com/wljmmx/only-LLMwiki-comp.git
cd only-LLMwiki-comp
cp .env.example .env
# 编辑 .env，至少配置 OPENAI_COMPAT_API_KEY（或切换到 ollama 本地模式）
```

> 💡 **首次开箱**：可不预先编辑 `.env`，直接启动后访问 UI，会自动进入 **Setup Wizard** 向导
> （5 步引导：配置概览 → LLM → Neo4j → 认证 → 生成部署命令，每步可在线测试连通）。

### 二、后端启动

```bash
pip install -r requirements.txt
cd backend && python -m uvicorn app.main:app --reload --port 8000
```

首次启动会自动创建 bootstrap admin（用户名 `admin`，密码见启动日志）。

### 三、前端启动

```bash
cd frontend
npm install
npm run dev
# 打开 http://localhost:5173
```

### 四、Docker 部署（推荐生产）

OpsKG 使用 **单镜像** 架构（Dockerfile 多阶段构建，镜像内含前端 dist + 后端 + nginx + supervisord），docker-compose 同时编排 OpsKG 单镜像与 Neo4j：

```bash
cp .env.example .env
# 编辑 .env 后启动
docker compose up -d

# 验证
curl http://localhost/health          # OpsKG 健康检查
# 浏览器打开 http://localhost        # OpsKG 控制台（首次自动跳转 Setup Wizard）
# Neo4j 控制台 http://localhost:7474   # neo4j / password
```

镜像暴露端口 80（容器内 nginx），健康检查 `curl -fsS http://localhost/health`。
依赖 Neo4j healthy 后才启动 OpsKG，避免冷启动连接失败。

> 💡 **未配置即开箱**：即使 `.env` 留空，容器也能启动；浏览器访问后由 UI Setup Wizard 引导你
> 选择 LLM 后端、测试连通、生成可一键复制的 `docker run` / `docker compose` 命令与 `.env` 内容。

详细安装步骤见 [INSTALL.md](INSTALL.md)。

## 项目结构

```
.
├── AGENTS.md                      # L3 Schema 层：Wiki 管理员行为规范
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI 入口（122 端点）
│   │   ├── config.py              # 配置项（LLM/Neo4j/抽取/认证/OIDC/HA）
│   │   ├── auth/                  # RBAC + Session + OIDC SSO
│   │   ├── knowledge/             # LLM Wiki 范式核心（compiler/wikilink/lint/drift/query）
│   │   ├── aiops/                 # 事件关联 + 变更关联 + 服务拓扑
│   │   ├── extraction/            # 知识抽取（LLM + 规则兜底）
│   │   ├── parsers/               # 多格式解析（28 种格式）
│   │   ├── search/                # 混合检索（FTS5 + 向量 + RRF）
│   │   ├── mcp/                   # MCP 协议（JSON-RPC + SSE）
│   │   ├── observability/         # Prometheus 指标 + OpenTelemetry 追踪
│   │   ├── webhooks/              # 事件分发（HMAC 签名 + 指数退避重试）
│   │   └── ha/                    # 高可用（liveness/readiness 探针）
│   ├── tests/                     # pytest 61 用例
│   └── data/                      # SQLite 数据库 + uploads
├── frontend/
│   ├── src/
│   │   ├── views/                 # 19 个视图（14 模块全交付）
│   │   ├── stores/                # Pinia（auth/app/onboarding）
│   │   ├── api/                   # axios 封装（82 端点 / 87 函数）
│   │   └── components/            # 布局 + 错误边界 + 引导
│   └── package.json
├── scripts/
│   ├── verify_*.py                # 11 个验证脚本（470 验证点）
│   ├── backup.sh / restore.sh     # 备份恢复
│   └── smoke_*.py                 # 端到端冒烟测试
├── docs/                          # 审计报告 + 演进路线 + 设计文档
├── .github/workflows/ci.yml       # GitHub Actions CI
├── docker-compose.yml             # Neo4j + OpsKG 单镜像
├── Dockerfile                     # 单镜像（前端 dist + 后端 + nginx + supervisord）
├── requirements.txt               # Python 依赖
└── pyproject.toml                 # ruff 配置
```

## 核心能力

### LLM Wiki 范式（AGENTS.md 9 项核心机制 100% 实现）

| 机制 | 说明 |
|------|------|
| L1/L2/L3 三层架构 | raw 文档 → wiki:{slug} → AGENTS.md 规范 |
| Wiki 页面骨架 | frontmatter（slug/title/type/tags/sources/review_status）+ 类型必含章节 |
| [[wikilink]] 双向链接 | `[[slug]]` / `[[slug\|显示文本]]`，backlink 自动维护 |
| Ingest Workflow | raw → 解析 → 抽取 → 编译 wiki 页面 → 建链 → 重建 index |
| Query Workflow | 向量 + 关键词 RRF 融合召回 → LLM 编译回答 → 引用 [[slug]] |
| Lint Workflow | 矛盾检测 + Stale 检测 + Orphan 检测 + Missing Concept 检测 |
| Maintain Workflow | raw 漂移 → 标记 stale → 自动重编译 → diff → ReviewQueue |
| index.md 自动维护 | 按类型分组 + 最近变更 + orphan 候选 |
| 6 种页面类型 | entity / concept / incident / runbook / service / host |

### AIOps

- **事件关联**：时间窗口聚合 + 拓扑根因推断 + incident 5 状态机（open/ack/investigating/mitigated/resolved）
- **变更关联**：变更-incident 时间窗关联 + 回滚建议
- **服务拓扑**：文档抽取 RUNS_ON/DEPENDS_ON/USES + 共现推断 + 别名合并 + 快照 diff + 影响分析（含冗余度/SPOF/blast_radius）

### 企业级

- **认证**：用户名密码 + RBAC（admin > operator > viewer）+ OIDC SSO（Google/GitHub/Keycloak，PKCE S256）
- **可观测性**：Prometheus 指标（HTTP + 13 业务指标）+ OpenTelemetry 分布式追踪（W3C Trace Context + 日志关联）
- **Webhook**：12 类事件 + HMAC-SHA256 签名 + 指数退避重试
- **HA**：liveness/readiness 分离 + 6 DB 健康检查 + instance_id

## API 概览

后端 122 个端点，按模块分组：

| 模块 | 端点数 | 说明 |
|------|--------|------|
| auth + oidc | 13 | 登录/注销/用户 CRUD/OIDC SSO |
| documents + parsers | 9 | 文档上传/列表/解析 |
| search | 3 | 混合检索 |
| llm-wiki | 16 | Wiki 编译/查询/健康检查/漂移 |
| graph | 6 | 知识图谱 |
| extraction | 2 | 知识抽取/编译 |
| events + changes + topology | 31 | AIOps 三件套 |
| runbook + templates + versions + export | 16 | 文档生成 |
| mcp | 3 | MCP 协议 |
| webhooks | 11 | 事件订阅 |
| health/ready/tracing | 3 | 探针与状态 |

完整 API 文档：启动后端后访问 `http://localhost:8000/docs`。

## 测试与质量

```bash
# 后端
cd backend && python -m pytest tests/                    # 61 用例
cd .. && python scripts/verify_*.py                       # 470 验证点

# 前端
cd frontend && npm test                                    # 129 用例
cd frontend && npm run typecheck                           # vue-tsc 0 错误
cd frontend && npm run build                               # 生产构建

# Lint
ruff check backend/app/ scripts/                          # Python lint
```

CI 自动执行上述全部检查（`.github/workflows/ci.yml`）。

## 配置

核心配置项见 `.env.example`，完整字段说明见 [backend/app/config.py](backend/app/config.py)。

关键配置：
- `LLM_BACKEND`：ollama / vllm / openai_compat
- `OPENAI_COMPAT_API_KEY`：DeepSeek 等 API Key（留空则 LLM 不可用，wiki 编译降级为模板）
- `OPSKG_API_TOKEN`：留空关闭认证（dev 模式），设置后所有写操作需带 token
- `OPSKG_TRACING_ENABLED=1`：启用 OpenTelemetry 分布式追踪
- `OIDC_PROVIDERS`：JSON 数组配置 OIDC 提供者

## 文档

- [INSTALL.md](INSTALL.md) — 详细安装指南
- [docs/项目审计报告与演进路线.md](docs/项目审计报告与演进路线.md) — 双视角审计 + Sprint 11+ 演进路线
- [AGENTS.md](AGENTS.md) — LLM Wiki 管理员行为规范（L3 Schema 层）
- [docs/LLM-Wiki完整开发计划.md](docs/LLM-Wiki完整开发计划.md) — 范式落地计划

## License

实验项目，仅供学习参考。
