# OpsKG 多阶段 Dockerfile（单镜像含前后端 + nginx + supervisord）
#
# 构建产物：单镜像，包含前端 dist/ + 后端 FastAPI + nginx + supervisord
# 启动：docker run -p 8080:8080 -e OPENAI_COMPAT_API_KEY=... ghcr.io/wljmmx/only-llmwiki-comp:0.0.1
#
# 镜像层级：
#   Stage 1: frontend-builder  构建 Vue 前端 → /build/dist
#   Stage 2: runtime           python:3.12-slim + nginx + supervisor + 后端代码 + 前端 dist
#
# 设计要点：
#   - 前端构建与运行时分离，最终镜像不含 node_modules（节省 ~500MB）
#   - 运行时使用 slim 基础镜像，仅安装必要系统依赖
#   - nginx 反向代理 /api/* → uvicorn:8000（strip /api 前缀，与 vite dev proxy 一致）
#   - supervisord 管理 nginx + uvicorn 双进程
#   - 数据目录 /app/data 建议挂载 PVC 持久化
#   - 非 root 用户运行（安全）
#   - OCI 标准 LABEL（版本、源码、许可证）

# ────────── 全局构建参数 ──────────
ARG OPSKG_VERSION=0.0.1
ARG OPSKG_IMAGE_REF=ghcr.io/wljmmx/only-llmwiki-comp

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

ARG OPSKG_VERSION
ARG OPSKG_IMAGE_REF
ARG BUILD_DATE
ARG VCS_REF

# OCI 标准 LABEL（docker inspect / dockerhub 显示）
LABEL org.opencontainers.image.title="OpsKG" \
      org.opencontainers.image.description="LLM 驱动的运维知识图谱 Wiki 控制台" \
      org.opencontainers.image.version="${OPSKG_VERSION}" \
      org.opencontainers.image.url="https://github.com/wljmmx/only-LLMwiki-comp" \
      org.opencontainers.image.source="https://github.com/wljmmx/only-LLMwiki-comp" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="OpsKG Contributors" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.ref.name="${OPSKG_IMAGE_REF}:${OPSKG_VERSION}"

WORKDIR /app

# 安装系统依赖：
#   - nginx：前端静态资源 + 反向代理
#   - supervisor：进程管理（nginx + uvicorn）
#   - curl：健康检查 + 调试
#   - libxml2 / libxmlsec1：python3-saml 依赖（SAML SSO）
# 注：--no-install-recommends 避免安装推荐包，保持镜像精简
RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx \
        supervisor \
        curl \
        libxml2 \
        libxmlsec1 \
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

# 创建非 root 用户运行（P1-2: 安全最佳实践）
# nginx 监听 8080（非特权端口），supervisord + nginx + uvicorn 全部以 opskg 用户运行
# 无需 root 绑定特权端口，容器全程非特权运行
RUN groupadd -r opskg && useradd -r -g opskg -d /app -s /sbin/nologin opskg \
    && mkdir -p /app/data /var/log/nginx /var/log/supervisor \
                /var/lib/nginx/body /var/lib/nginx/proxy /var/lib/nginx/fastcgi \
                /var/cache/nginx /run \
    && chown -R opskg:opskg /app /var/log/nginx /var/log/supervisor \
                /var/lib/nginx /var/cache/nginx /run

# 数据目录（建议挂载 PVC）
VOLUME ["/app/data"]

# P1-2: 非特权端口 8080（>1024），全程非 root 运行
EXPOSE 8080

# 健康检查（HTTP 200 = 进程存活）
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

# 环境变量默认值
ENV ENV=production \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1 \
    OPSKG_UVICORN_WORKERS=2 \
    OPSKG_VERSION=${OPSKG_VERSION}

# P1-2: 全程以非 root 用户运行
USER opskg

# 入口：动态调整 worker 数 + 启动 supervisord
ENTRYPOINT ["entrypoint.sh"]
