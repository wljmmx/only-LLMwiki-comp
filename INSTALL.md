# OpsKG 安装指南

本文档详细介绍 OpsKG 的本地开发、Docker 部署与生产部署。

## 目录

- [前置要求](#前置要求)
- [方式一：本地开发（推荐首次体验）](#方式一本地开发推荐首次体验)
- [方式二：Docker 部署](#方式二docker-部署)
- [方式三：生产部署](#方式三生产部署)
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

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，配置 LLM_API_KEY 等

# 2. 启动（Neo4j + Backend）
docker compose up -d

# 3. 验证
curl http://localhost:8000/health
# Neo4j 控制台：http://localhost:7474（neo4j/password）
```

**注意**：当前 `docker-compose.yml` 仅含后端，前端需单独构建：

```bash
cd frontend && npm run build
# 将 dist/ 部署到 nginx 或其他静态服务器，反向代理 /api 到 backend:8000
```

## 方式三：生产部署

### 1. 构建前端

```bash
cd frontend
npm ci
npm run build
# 产物在 frontend/dist/
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
```

### 3. 部署

```bash
docker compose --env-file .env.prod up -d
```

### 4. 前端部署（nginx 示例）

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 前端静态资源
    location / {
        root /path/to/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    # API 反向代理
    location /api/ {
        proxy_pass http://backend:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
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
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
```

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

### Q: 如何备份

A: 使用提供的备份脚本：

```bash
./scripts/backup.sh    # 备份到 data/backups/
./scripts/restore.sh data/backups/opskg_YYYYMMDD_HHMMSS.tar.gz
```
