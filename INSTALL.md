# OpsKG 安装指南

本文档详细介绍 OpsKG 的本地开发、Docker 部署与生产部署。

## 目录

- [前置要求](#前置要求)
- [方式一：本地开发（推荐首次体验）](#方式一本地开发推荐首次体验)
- [方式二：Docker 部署](#方式二docker-部署)
- [方式三：生产部署](#方式三生产部署)
- [Setup Wizard（开箱配置向导）](#setup-wizard开箱配置向导)
- [LLM 后端配置](#llm-后端配置)
- [Neo4j 配置](#neo4j-配置)
- [认证配置](#认证配置)
- [可观测性配置](#可观测性配置)
- [OIDC SSO 配置](#oidc-sso-配置)
- [常见问题](#常见问题)

## 前置要求

| 组件 | 最低版本 | 用途 | 是否必需 |
|------|---------|------|---------|
| Python | 3.11 | 后端运行时 | ✅ 必需 |
| Node.js | 20 | 前端构建 | ✅ 必需 |
| npm | 10 | 前端依赖管理 | ✅ 必需 |
| Neo4j | 5.0 | 知识图谱存储 | ⚠️ 可选（缺失时图功能降级） |
| Docker | 24 | 容器化部署 | ⚠️ 可选 |
| Docker Compose | 2 | 多容器编排 | ⚠️ 可选 |

### LLM 后端（三选一）

- **DeepSeek API**（推荐，云端）：需要 API Key，访问 https://api.deepseek.com
- **Ollama**（本地）：需安装 Ollama 并下载模型（如 `qwen2.5:7b`）
- **vLLM**（本地）：需 GPU，自建 OpenAI 兼容服务

## 方式一：本地开发（推荐首次体验）

### 1. 克隆仓库

```bash
git clone https://github.com/wljmmx/only-LLMwiki-comp.git
cd only-LLMwiki-comp
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，**至少配置以下项**：

```bash
# LLM 后端（三选一，默认 DeepSeek）
LLM_BACKEND=openai_compat
OPENAI_COMPAT_API_KEY=sk-your-deepseek-key  # 必填（若用 DeepSeek）
# 或切换到本地 Ollama：
# LLM_BACKEND=ollama
# OLLAMA_BASE_URL=http://localhost:11434
# OLLAMA_MODEL=qwen2.5:7b
```

其他配置项保持默认即可。生产部署时需设置 `OPSKG_API_TOKEN`（见[认证配置](#认证配置)）。

### 3. 启动后端

```bash
# 创建虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# 安装依赖
pip install -r requirements.txt

# 启动（开发模式，热重载）
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

**首次启动行为**：
- 自动创建 7 个 SQLite 数据库（`backend/data/*.db`）
- 自动创建 `uploads/` 目录
- 自动创建 bootstrap admin 用户（用户名 `admin`，密码打印在日志中，可通过 `OPSKG_BOOTSTRAP_ADMIN_USER` / `OPSKG_BOOTSTRAP_ADMIN_PASSWORD` 预设）
- 若 Neo4j 不可达，图功能降级（不影响其他功能）

**验证**：
```bash
curl http://localhost:8000/health
# {"status":"healthy",...}

curl http://localhost:8000/docs  # 浏览器打开 API 文档
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

**验证**：浏览器打开 http://localhost:5173

- dev 模式（未设置 `OPSKG_API_TOKEN`）：无需登录直接进入
- 生产模式（设置了 `OPSKG_API_TOKEN` 或配置了认证）：跳转登录页

### 5. 启动 Neo4j（可选）

若需要知识图谱可视化与拓扑推断功能：

```bash
# 方式 A：Docker
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  -v neo4j_data:/data \
  neo4j:5-community

# 方式 B：本地安装
# 见 https://neo4j.com/download/
```

确保 `.env` 中 `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` 与实际一致。

## 方式二：Docker 部署

OpsKG 使用 **单镜像** 架构：Dockerfile 多阶段构建（Stage 1 构建前端 dist → Stage 2 装 Python 后端 + nginx + supervisord），最终镜像同时承载 nginx（前端静态资源 + 反向代理）与 uvicorn（后端）。docker-compose 编排 OpsKG 单镜像与 Neo4j 两个容器。

```bash
# 1. 配置环境变量（也可留空，启动后由 UI Setup Wizard 引导填写）
cp .env.example .env
# 编辑 .env，配置 OPENAI_COMPAT_API_KEY 等

# 2. 启动（Neo4j + OpsKG 单镜像）
docker compose up -d

# 3. 验证
curl http://localhost:8080/health          # OpsKG 健康检查
# 浏览器打开 http://localhost:8080        # OpsKG 控制台（首次自动跳转 Setup Wizard）
# Neo4j 控制台：http://localhost:7474  # neo4j / password
```

**docker-compose 关键设计**：
- `opskg` 服务：单镜像，暴露宿主端口 8080（容器内 nginx），healthcheck `curl -fsS http://localhost:8080/health`。宿主端口可通过 `.env` 中 `OPSKG_PORT` 修改（如 `OPSKG_PORT=9982`）
- `neo4j` 服务：5-community，含 healthcheck，`start_period: 30s` 等待 Neo4j 启动
- `opskg` 通过 `depends_on: neo4j: condition: service_healthy` 等待 Neo4j 健康后再启动
- 所有环境变量从 `.env` 注入

**单容器模式（不含 Neo4j，自行启动 Neo4j）**：

```bash
docker build -t opskg:latest .
docker run -d --name opskg \
  -p 80:8080 \
  -e LLM_BACKEND=openai_compat \
  -e OPENAI_COMPAT_API_KEY=sk-xxx \
  -e NEO4J_URI=bolt://host.docker.internal:7687 \
  -e NEO4J_PASSWORD=password \
  -e OPSKG_BOOTSTRAP_ADMIN_USER=admin \
  -e OPSKG_BOOTSTRAP_ADMIN_PASSWORD=admin \
  -v opskg_data:/app/data \
  opskg:latest
```

容器内 `entrypoint.sh` 会根据 `OPSKG_UVICORN_WORKERS`（默认 2）动态调整 uvicorn workers。

## 方式三：生产部署

### 1. 构建单镜像（已内含前端构建）

```bash
# Dockerfile 多阶段构建会先在 Stage 1（node:20-slim）中 npm ci + npm run build，
# 再把 dist 拷贝到 Stage 2（python:3.12-slim）的 nginx 静态目录。
# 因此生产部署无需在宿主机单独构建前端。
docker build -t opskg:latest .
```

### 2. 配置生产环境变量

```bash
cp .env.example .env.prod
```

编辑 `.env.prod`，**必须配置**：

```bash
ENV=production
LOG_LEVEL=WARNING

# 认证（生产必填）
OPSKG_API_TOKEN=your-random-secure-token  # 或配置完整认证体系

# LLM
LLM_BACKEND=openai_compat
OPENAI_COMPAT_API_KEY=sk-your-key

# Neo4j
NEO4J_URI=bolt://neo4j:7687
NEO4J_PASSWORD=your-strong-password

# 可观测性（推荐开启）
OPSKG_TRACING_ENABLED=1
OPSKG_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces

# HA
OPSKG_DEPLOYMENT_MODE=replicated
OPSKG_INSTANCE_ID=opskg-prod-01

# Uvicorn workers（按 CPU 核数调整）
OPSKG_UVICORN_WORKERS=4
```

### 3. 部署

```bash
docker compose --env-file .env.prod up -d
```

### 4. 自定义前端 nginx 配置（可选）

单镜像内 nginx 配置位于 `/etc/nginx/conf.d/default.conf`（源文件 `deploy/docker/nginx.conf`），
若需自定义路由（如额外 WebSocket、长连接超时），可挂载覆盖：

```bash
docker run -d --name opskg \
  -p 80:8080 \
  -v /path/to/my-nginx.conf:/etc/nginx/conf.d/default.conf:ro \
  ... \
  opskg:latest
```

## Setup Wizard（开箱配置向导）

OpsKG 提供 UI 上的 **Setup Wizard** 多步骤引导，让首次开箱用户无需阅读本文档即可完成环境配置。

### 何时自动出现

- **首次访问**：浏览器打开 OpsKG 后，前端会调用 `GET /setup/status` 检测配置完成度。若 `ready=false` 且用户未主动 dismiss，自动跳转到 `/setup` 路由。
- **手动打开**：登录后右上角用户菜单 → "开箱配置向导"，可随时重新打开。
- **跳过后**：localStorage 标记 `opskg:setup:dismissed=true`，不再自动跳转。可通过用户菜单重新打开。

### 5 步引导流程

| 步骤 | 内容 | 操作 |
|------|------|------|
| 1. 配置概览 | 显示 LLM/Neo4j/认证 三项配置完成度（绿色已配置 / 黄色未配置） | 查看缺失项 |
| 2. LLM 配置 | 选择 backend（openai_compat / ollama / vllm），填 base_url + api_key + model | 点击"测试连通" |
| 3. Neo4j 配置 | 填 bolt URI + user + password | 点击"测试连通" |
| 4. 认证配置 | 可选启用 API Token + 配置 Bootstrap Admin 凭据 | 设置初始管理员 |
| 5. 生成命令 | 选择 docker compose 或 docker run，填端口/workers，生成可复制命令 | 复制 .env + 命令执行 |

### 设计原则

- **不写 .env 文件**：所有测试连通都基于表单临时值（通过 `POST /setup/test-llm` / `test-neo4j` 请求体覆盖），不修改后端任何文件。避免敏感信息持久化风险。
- **生成命令而非自动执行**：最后一步生成 `docker run` / `docker compose` 命令字符串 + 配套 `.env` 文件内容，用户自行复制执行。
- **所有端点无需认证**：Setup Wizard 在认证配置前必须可用，4 个端点 (`GET /setup/status`, `POST /setup/test-llm`, `POST /setup/test-neo4j`, `POST /setup/generate-command`) 均不要求 token。

### 后端 API

| 端点 | 方法 | 用途 |
|------|------|------|
| `/setup/status` | GET | 配置完成度检查（不暴露敏感值） |
| `/setup/test-llm` | POST | 用请求体覆盖当前 settings 测试 LLM 连通 |
| `/setup/test-neo4j` | POST | 用请求体覆盖当前 settings 测试 Neo4j 连通 |
| `/setup/generate-command` | POST | 生成可复制的 docker 命令 + .env 文件内容 |

### 典型开箱流程

```bash
# 1. 克隆 + 启动（不编辑 .env）
git clone https://github.com/wljmmx/only-LLMwiki-comp.git
cd only-LLMwiki-comp
docker compose up -d

# 2. 浏览器打开 http://localhost，自动跳转到 Setup Wizard
# 3. 按引导填写 LLM API Key、测试连通
# 4. 填写 Neo4j 密码、测试连通
# 5. 设置 Bootstrap Admin 凭据
# 6. 生成命令 → 复制 .env 内容覆盖 .env 文件 → docker compose down && up -d
# 7. 等待 30 秒后刷新页面，使用 Bootstrap Admin 登录
```

## LLM 后端配置

### DeepSeek（推荐，云端）

```bash
LLM_BACKEND=openai_compat
OPENAI_COMPAT_BASE_URL=https://api.deepseek.com/v1
OPENAI_COMPAT_API_KEY=sk-your-key        # 必填
OPENAI_COMPAT_MODEL=deepseek-chat
```

### Ollama（本地，免费）

```bash
# 先安装 Ollama 并下载模型
ollama pull qwen2.5:7b

LLM_BACKEND=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1   # 注意：OpenAI SDK 要求 /v1 后缀
OLLAMA_MODEL=qwen2.5:7b
```

> **重要**：`OLLAMA_BASE_URL` 必须以 `/v1` 结尾，因为后端使用 OpenAI SDK 调用 Ollama 的兼容端点。若缺少 `/v1` 会报 `404 page not found`。

### vLLM（本地，需 GPU）

```bash
LLM_BACKEND=vllm
VLLM_BASE_URL=http://localhost:8000
VLLM_MODEL=Qwen2.5-14B-Instruct
```

### Embedding 配置（向量检索）

```bash
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5  # 留空则关闭向量检索，退化为关键词
EMBEDDING_DIM=512
EMBEDDING_BATCH_SIZE=16
```

## Neo4j 配置

```bash
NEO4J_URI=bolt://localhost:7687        # Docker 用 bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
```

Neo4j 不可达时，以下功能降级：
- 知识图谱可视化：无数据
- 服务拓扑：无法构建
- 图谱搜索：不可用

其他功能（文档解析、wiki 编译、Q&A、事件关联）不受影响。

## 认证配置

### Dev 模式（默认，无认证）

```bash
OPSKG_API_TOKEN=  # 留空
```

所有 API 直接可访问，前端无需登录。

### Token 模式（简单认证）

```bash
OPSKG_API_TOKEN=your-random-token
```

所有写操作需在 Header 中携带 `Authorization: Bearer your-random-token`。该 token 视为 admin 角色。

### 完整认证模式（用户名密码 + RBAC）

设置 `OPSKG_API_TOKEN` 后，系统自动启用完整认证：
- 首次启动创建 bootstrap admin（`admin` 用户，密码见日志）
- 前端跳转登录页
- 支持 3 角色：admin（全权）> operator（读写）> viewer（只读）
- 通过 `POST /auth/users` 创建更多用户（admin 操作）

### OIDC SSO（企业级）

配置 OIDC 提供者（Google / GitHub / Keycloak 等）：

```bash
# .env
OIDC_PROVIDERS='[
  {
    "name": "google",
    "issuer": "https://accounts.google.com",
    "client_id": "your-client-id.googleusercontent.com",
    "client_secret": "your-client-secret",
    "scopes": ["openid", "email", "profile"]
  },
  {
    "name": "github",
    "issuer": "https://token.actions.githubusercontent.com",
    "client_id": "your-github-oauth-app-id",
    "client_secret": "your-github-oauth-app-secret",
    "scopes": ["read:user", "user:email"]
  }
]'
OIDC_DEFAULT_ROLE=viewer
OIDC_REDIRECT_BASE_URL=https://your-backend-domain.com
FRONTEND_BASE_URL=https://your-frontend-domain.com
```

首次 OIDC 登录自动创建本地用户（默认 viewer 角色），后续登录按 `provider + sub` 匹配。

## 可观测性配置

### Prometheus 指标（默认启用）

```bash
# 无需配置，自动启用
# 抓取端点：http://localhost:8000/metrics
```

Prometheus scrape 配置：

```yaml
scrape_configs:
  - job_name: 'opskg'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
```

### OpenTelemetry 分布式追踪（默认关闭）

```bash
OPSKG_TRACING_ENABLED=1
OPSKG_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces
OPSKG_OTLP_SERVICE_NAME=opskg-backend
# 未配置 OTLP_ENDPOINT 时降级为 Console 输出（开发调试）
```

## 常见问题

### Q: 启动后端报 `ModuleNotFoundError: No module named 'app'`

A: 需在 `backend/` 目录下运行，或设置 `PYTHONPATH=backend`：

```bash
cd backend && python -m uvicorn app.main:app --reload
# 或
PYTHONPATH=backend python -m uvicorn app.main:app --reload
```

### Q: 前端 `npm run dev` 后页面空白

A: 检查后端是否已启动（`http://localhost:8000/health`），前端代理配置在 `vite.config.ts` 中指向 `http://localhost:8000`。

### Q: LLM 调用失败，wiki 编译降级为模板

A: 检查 `.env` 中 `LLM_BACKEND` 与对应配置项是否正确。DeepSeek 需有效 API Key；Ollama 需先 `ollama pull` 模型。

### Q: Neo4j 连接失败

A: 确认 Neo4j 已启动（`docker ps` 或 `cypher-shell -u neo4j`），`.env` 中 `NEO4J_URI` / `NEO4J_PASSWORD` 正确。Neo4j 不可达不影响其他功能。

### Q: 测试失败

A: 后端测试需在 `backend/` 目录运行；verify 脚本需在项目根目录运行并设置 `PYTHONPATH=backend`：

```bash
cd backend && python -m pytest tests/                          # 后端测试
cd .. && PYTHONPATH=backend python scripts/verify_auth.py      # verify 脚本
cd frontend && npm test                                          # 前端测试
```

### Q: 如何重置数据

A: 删除 `backend/data/*.db` 文件后重启，会自动重建空数据库：

```bash
rm backend/data/*.db
cd backend && python -m uvicorn app.main:app --reload
```

### Q: Windows 本地部署有什么注意事项

A: Windows 本地部署（非 Docker）需注意以下几点：

1. **Python 版本**：项目要求 Python 3.11+，确保使用正确版本创建虚拟环境。

2. **uvloop 不支持 Windows**：`requirements.txt` 中的 `uvloop` 在 Windows 上无法安装，可安全跳过（后端会自动降级到 asyncio 默认事件循环，不影响功能）。

3. **Neo4j 启动方式**：若 Docker/wslc 因网络问题无法拉取镜像，可直接下载 Neo4j Windows 社区版（zip 包），解压后运行：
   ```powershell
   neo4j-admin.bat set-initial-password password
   neo4j.bat console
   ```

4. **SSRF 防护与 localhost**：后端默认阻止访问 localhost 地址（SSRF 防护）。若 LLM 和 Neo4j 均部署在本地，需在 `.env` 中设置：
   ```bash
   OPSKG_ALLOW_LOOPBACK_URLS=1
   ```
   否则 Setup Wizard 中的"测试连通"会失败。

5. **Ollama base_url 必须带 /v1**：Windows 本地 Ollama 的 `OLLAMA_BASE_URL` 应为 `http://localhost:11434/v1`（OpenAI SDK 要求）。

### Q: 如何备份

A: 使用提供的备份脚本：

```bash
./scripts/backup.sh    # 备份到 data/backups/
./scripts/restore.sh data/backups/opskg_YYYYMMDD_HHMMSS.tar.gz
```
