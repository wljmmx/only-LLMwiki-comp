# OpsKG 多阶段 Dockerfile（S13-4 单镜像含前后端）
#
# 构建产物：单镜像，包含前端 dist/ + 后端 FastAPI + nginx + supervisord
# 启动：docker run -p 80:80 -e OPENAI_COMPAT_API_KEY=... opskg:latest
#
# 镜像层级：
#   Stage 1: frontend-builder  构建 Vue 前端 → /app/dist
#   Stage 2: runtime           python:3.12-slim + nginx + supervisor + 后端代码 + 前端 dist
#
# 设计要点：
#   - 前端构建与运行时分离，最终镜像不含 node_modules（节省 ~500MB）
#   - 运行时使用 slim 基础镜像，仅安装必要系统依赖
#   - nginx 反向代理 /api/* → uvicorn:8000（strip /api 前缀，与 vite dev proxy 一致）
#   - supervisord 管理 nginx + uvicorn 双进程
#   - 数据目录 /app/data 建议挂载 PVC 持久化

# ────────── Stage 1: 前端构建 ──────────
FROM node:20-slim AS frontend-builder

WORKDIR /build

# 先复制 package 文件，利用 Docker 层缓存
COPY frontend/package.json frontend/package-lock.json ./

# 安装依赖（ci 模式：严格按 lockfile，可重现构建）
RUN npm ci --no-audit --no-fund

# 复制前端源码
COPY frontend/ ./

# 类型检查 + 构建（生成 dist/）
# 注：typecheck 失败会阻断构建，保证镜像内前端类型安全
RUN npm run build

# ────────── Stage 2: 运行时 ──────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# 安装系统依赖：
#   - nginx：前端静态资源 + 反向代理
#   - supervisor：进程管理（nginx + uvicorn）
#   - curl：健康检查 + 调试
#   - libxml2-dev / libxmlsec1-dev：python3-saml 依赖（SAML SSO）
# 注：--no-install-recommends 避免安装推荐包，保持镜像精简
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
        supervisor \
        curl \
        libxml2 \
        libxmlsec1 \
        libxml2-dev \
        libxmlsec1-dev \
        libxmlsec1-openssl \
        pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip

# 安装 Python 依赖
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 复制后端应用代码
COPY backend/ /app/

# 复制前端构建产物到 nginx 服务目录
COPY --from=frontend-builder /build/dist /usr/share/nginx/html

# 复制 nginx + supervisor 配置
COPY deploy/docker/nginx.conf /etc/nginx/nginx.conf
COPY deploy/docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY deploy/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 数据目录（建议挂载 PVC）
RUN mkdir -p /app/data /var/log/nginx /var/log/supervisor

# 暴露端口
# - 80：nginx（外部访问入口）
# - 8000：uvicorn（仅容器内 nginx 访问，不应暴露）
EXPOSE 80

# 健康检查（HTTP 200 = 进程存活）
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost/health || exit 1

# 环境变量默认值
ENV ENV=production \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1 \
    OPSKG_UVICORN_WORKERS=2

# 入口：动态调整 worker 数 + 启动 supervisord
ENTRYPOINT ["entrypoint.sh"]
