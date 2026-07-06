#!/usr/bin/env python3
"""S13-4 Dockerfile 前端集成验证脚本

验证内容：
1. Dockerfile 多阶段构建结构（frontend-builder + runtime）
2. 前端构建阶段：node:20 + npm ci + npm run build
3. 运行时阶段：python:3.12-slim + nginx + supervisor
4. nginx.conf 路由规则（/api strip + /auth 不 strip + SPA fallback）
5. supervisord.conf 双进程管理（nginx + uvicorn）
6. entrypoint.sh 可执行 + 动态 worker 调整
7. .dockerignore 构建上下文优化
8. 镜像层缓存优化（package.json 先复制）
9. 安全加固（非 root 运行 + 健康检查 + 资源限制提示）
10. 单镜像 docker run 启动指令完整
11. 与 vite dev proxy 一致性（/api strip 行为）
12. 与 K8s 部署兼容性（前端 Deployment 镜像名匹配）

运行：python scripts/verify_s13_4_dockerfile.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCKERFILE = ROOT / "Dockerfile"
NGINX_CONF = ROOT / "deploy" / "docker" / "nginx.conf"
SUPERVISORD_CONF = ROOT / "deploy" / "docker" / "supervisord.conf"
ENTRYPOINT = ROOT / "deploy" / "docker" / "entrypoint.sh"
DOCKERIGNORE = ROOT / ".dockerignore"

PASS = 0
FAIL = 0
TESTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        TESTS.append((name, True, detail))
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        TESTS.append((name, False, detail))
        print(f"  ❌ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n── {title} ──")


# ────────── 测试 1：Dockerfile 多阶段结构 ──────────


def test_dockerfile_multistage() -> None:
    section("1. Dockerfile 多阶段构建结构")
    check("Dockerfile 存在", DOCKERFILE.exists())
    if not DOCKERFILE.exists():
        return

    content = DOCKERFILE.read_text(encoding="utf-8")

    # 多阶段构建
    stages = [line for line in content.splitlines() if line.startswith("FROM ")]
    check("至少 2 个 FROM 阶段", len(stages) >= 2, f"got {len(stages)}: {stages}")

    # Stage 1: frontend-builder
    check(
        "Stage 1 名为 frontend-builder",
        any("frontend-builder" in s for s in stages),
    )
    check(
        "Stage 1 基于 node:20-slim",
        any("node:20-slim" in s for s in stages),
    )

    # Stage 2: runtime
    check(
        "Stage 2 名为 runtime",
        any("runtime" in s for s in stages),
    )
    check(
        "Stage 2 基于 python:3.12-slim",
        any("python:3.12-slim" in s for s in stages),
    )

    # COPY --from=frontend-builder
    check(
        "Stage 2 COPY --from=frontend-builder",
        "COPY --from=frontend-builder" in content,
    )


# ────────── 测试 2：前端构建阶段 ──────────


def test_frontend_build_stage() -> None:
    section("2. 前端构建阶段")
    content = DOCKERFILE.read_text(encoding="utf-8")

    # WORKDIR /build
    check("WORKDIR /build", "WORKDIR /build" in content)

    # package.json 先复制（层缓存优化）
    check(
        "package.json 先复制（层缓存优化）",
        "COPY frontend/package.json" in content,
    )

    # npm ci
    check("npm ci 安装依赖", "npm ci" in content)

    # npm run build
    check("npm run build 构建前端", "npm run build" in content)

    # 前端源码复制
    check("COPY frontend/ 源码", "COPY frontend/" in content)

    # 构建产物复制到 nginx 目录
    check(
        "构建产物复制到 /usr/share/nginx/html",
        "/usr/share/nginx/html" in content,
    )


# ────────── 测试 3：运行时阶段 ──────────


def test_runtime_stage() -> None:
    section("3. 运行时阶段")
    content = DOCKERFILE.read_text(encoding="utf-8")

    # 系统依赖
    check("安装 nginx", "nginx" in content)
    check("安装 supervisor", "supervisor" in content)
    check("安装 curl（健康检查）", "curl" in content)

    # SAML 依赖（libxml2 + libxmlsec1）
    check("安装 libxml2（SAML 依赖）", "libxml2" in content)
    check("安装 libxmlsec1（SAML 依赖）", "libxmlsec1" in content)

    # Python 依赖
    check("COPY requirements.txt", "requirements.txt" in content)
    check(
        "pip install -r requirements.txt",
        "pip install" in content and "requirements.txt" in content,
    )

    # 后端代码
    check("COPY backend/ /app/", "COPY backend/" in content)

    # 配置文件
    check("COPY nginx.conf", "nginx.conf" in content)
    check("COPY supervisord.conf", "supervisord.conf" in content)
    check("COPY entrypoint.sh", "entrypoint.sh" in content)

    # 权限
    check("chmod +x entrypoint.sh", "chmod +x" in content and "entrypoint.sh" in content)

    # EXPOSE 80
    check("EXPOSE 80（nginx）", "EXPOSE 80" in content)

    # 数据目录
    check("mkdir /app/data", "/app/data" in content)


# ────────── 测试 4：nginx.conf 路由规则 ──────────


def test_nginx_conf() -> None:
    section("4. nginx.conf 路由规则")
    check("nginx.conf 存在", NGINX_CONF.exists())
    if not NGINX_CONF.exists():
        return

    content = NGINX_CONF.read_text(encoding="utf-8")

    # 监听 80
    check("listen 80", "listen 80" in content)

    # 前端静态文件
    check("root /usr/share/nginx/html", "/usr/share/nginx/html" in content)
    check("try_files SPA fallback", "try_files $uri $uri/ /index.html" in content)

    # /api 反向代理（strip 前缀）
    check(
        "/api/ location 配置 proxy_pass",
        "location /api/" in content and "proxy_pass" in content,
    )
    # 末尾 / 触发 URI 改写（strip /api）
    check(
        "/api proxy_pass 末尾 / （strip 前缀）",
        "proxy_pass http://opskg_backend/;" in content
        or "proxy_pass http://127.0.0.1:8000/;" in content,
    )

    # /auth 反向代理（不 strip）
    check(
        "/auth/ location 配置 proxy_pass",
        "location /auth/" in content,
    )
    check(
        "/auth proxy_pass 无末尾 /（不 strip）",
        "proxy_pass http://opskg_backend;" in content
        or "proxy_pass http://127.0.0.1:8000;" in content,
    )

    # /health /ready /metrics
    check("= /health 精确匹配", "location = /health" in content)
    check("= /ready 精确匹配", "location = /ready" in content)
    check("= /metrics 精确匹配", "location = /metrics" in content)

    # /tracing
    check("/tracing/ 路由", "location /tracing/" in content or "location /tracing/" in content)

    # 上传大小
    check("client_max_body_size 100m", "client_max_body_size 100m" in content)

    # LLM 长响应超时
    check("proxy_read_timeout 300s", "proxy_read_timeout 300s" in content)

    # Gzip
    check("gzip on", "gzip on" in content)

    # 静态资源缓存
    check(
        "静态资源 Cache-Control immutable",
        "immutable" in content,
    )
    check(
        "index.html 不缓存",
        "no-cache" in content or "no-store" in content,
    )

    # upstream 定义
    check("upstream opskg_backend 定义", "upstream opskg_backend" in content)

    # 反向代理 header
    check("X-Real-IP header", "X-Real-IP" in content)
    check("X-Forwarded-For header", "X-Forwarded-For" in content)
    check("X-Forwarded-Proto header", "X-Forwarded-Proto" in content)


# ────────── 测试 5：supervisord.conf ──────────


def test_supervisord_conf() -> None:
    section("5. supervisord.conf 双进程管理")
    check("supervisord.conf 存在", SUPERVISORD_CONF.exists())
    if not SUPERVISORD_CONF.exists():
        return

    content = SUPERVISORD_CONF.read_text(encoding="utf-8")

    # nodaemon=true（前台运行）
    check("nodaemon=true", "nodaemon=true" in content)

    # nginx program
    check("[program:nginx] 配置", "[program:nginx]" in content)
    check(
        "nginx command=daemon off",
        "nginx -g \"daemon off;\"" in content or "daemon off" in content,
    )
    check("nginx autorestart=true", "nginx" in content and "autorestart=true" in content)

    # uvicorn program
    check("[program:uvicorn] 配置", "[program:uvicorn]" in content)
    check(
        "uvicorn command",
        "uvicorn app.main:app" in content,
    )
    check("uvicorn host 127.0.0.1", "127.0.0.1" in content)
    check("uvicorn port 8000", "8000" in content)
    check("uvicorn workers >= 1", "--workers" in content)

    # 日志
    check("uvicorn stdout → /dev/stdout", "/dev/stdout" in content)
    check("uvicorn stderr → /dev/stderr", "/dev/stderr" in content)

    # 优雅停止
    check("stopasgroup=true", "stopasgroup=true" in content)
    check("killasgroup=true", "killasgroup=true" in content)

    # 优先级（nginx 先启动）
    check("nginx priority=10", "priority=10" in content)
    check("uvicorn priority=20", "priority=20" in content)


# ────────── 测试 6：entrypoint.sh ──────────


def test_entrypoint() -> None:
    section("6. entrypoint.sh 入口脚本")
    check("entrypoint.sh 存在", ENTRYPOINT.exists())
    if not ENTRYPOINT.exists():
        return

    # 可执行权限
    check("entrypoint.sh 可执行", os.access(ENTRYPOINT, os.X_OK))

    content = ENTRYPOINT.read_text(encoding="utf-8")

    # shebang
    check("shebang #!/bin/sh", content.startswith("#!/bin/sh"))

    # set -e
    check("set -e", "set -e" in content)

    # 动态 worker 调整
    check(
        "支持 OPSKG_UVICORN_WORKERS 环境变量",
        "OPSKG_UVICORN_WORKERS" in content,
    )
    check(
        "默认 workers=2",
        "WORKERS=\"${OPSKG_UVICORN_WORKERS:-2}\"" in content
        or ":-2}" in content,
    )
    check(
        "sed 修改 supervisord.conf",
        "sed -i" in content,
    )

    # 创建运行时目录
    check("mkdir /app/data", "/app/data" in content)
    check("mkdir nginx 日志目录", "/var/log/nginx" in content)

    # 启动 supervisord
    check(
        "exec supervisord",
        "exec" in content and "supervisord" in content,
    )


# ────────── 测试 7：.dockerignore ──────────


def test_dockerignore() -> None:
    section("7. .dockerignore 构建上下文优化")
    check(".dockerignore 存在", DOCKERIGNORE.exists())
    if not DOCKERIGNORE.exists():
        return

    content = DOCKERIGNORE.read_text(encoding="utf-8")

    # 排除 git
    check("排除 .git", ".git" in content)

    # 排除 Python 缓存
    check("排除 __pycache__", "__pycache__" in content)
    check("排除 .pytest_cache", ".pytest_cache" in content)
    check("排除 .ruff_cache", ".ruff_cache" in content)

    # 排除前端 node_modules + dist（在镜像内重新构建）
    check("排除 frontend/node_modules", "frontend/node_modules" in content)
    check("排除 frontend/dist", "frontend/dist" in content)

    # 排除数据库 + 数据
    check("排除 *.db", "*.db" in content)
    check("排除 backend/data/", "backend/data/" in content)

    # 排除文档
    check("排除 docs/", "docs/" in content)
    check("排除 *.md", "*.md" in content)

    # 排除测试
    check("排除 backend/tests/", "backend/tests/" in content)
    check("排除 scripts/", "scripts/" in content)

    # 排除部署清单
    check("排除 deploy/", "deploy/" in content)

    # 排除环境变量
    check("排除 .env", ".env" in content)


# ────────── 测试 8：镜像层缓存优化 ──────────


def test_layer_cache() -> None:
    section("8. 镜像层缓存优化")
    content = DOCKERFILE.read_text(encoding="utf-8")
    lines = content.splitlines()

    # 找到 frontend-builder 阶段
    fb_start = -1
    for i, line in enumerate(lines):
        if "frontend-builder" in line:
            fb_start = i
            break

    check("找到 frontend-builder 阶段", fb_start >= 0)
    if fb_start < 0:
        return

    # 在 frontend-builder 阶段，COPY package.json 应在 COPY frontend/ 之前
    fb_lines = lines[fb_start:]
    package_copy_idx = -1
    source_copy_idx = -1
    for i, line in enumerate(fb_lines):
        if "COPY frontend/package.json" in line and package_copy_idx < 0:
            package_copy_idx = i
        if "COPY frontend/" in line and "package.json" not in line and source_copy_idx < 0:
            source_copy_idx = i

    check(
        "package.json 在 frontend/ 源码之前复制（层缓存）",
        0 <= package_copy_idx < source_copy_idx,
        f"package.json at {package_copy_idx}, frontend/ at {source_copy_idx}",
    )

    # npm ci 应在 npm run build 之前
    ci_idx = -1
    build_idx = -1
    for i, line in enumerate(fb_lines):
        if "npm ci" in line and ci_idx < 0:
            ci_idx = i
        if "npm run build" in line and build_idx < 0:
            build_idx = i

    check(
        "npm ci 在 npm run build 之前",
        0 <= ci_idx < build_idx,
    )

    # 在 runtime 阶段，requirements.txt 应在 backend/ 之前
    rt_start = -1
    for i, line in enumerate(lines):
        if "runtime" in line and "FROM" in line:
            rt_start = i
            break

    check("找到 runtime 阶段", rt_start >= 0)
    if rt_start >= 0:
        rt_lines = lines[rt_start:]
        req_copy_idx = -1
        backend_copy_idx = -1
        for i, line in enumerate(rt_lines):
            if "requirements.txt" in line and "COPY" in line and req_copy_idx < 0:
                req_copy_idx = i
            if "COPY backend/" in line and backend_copy_idx < 0:
                backend_copy_idx = i

        check(
            "requirements.txt 在 backend/ 之前复制",
            0 <= req_copy_idx < backend_copy_idx,
        )


# ────────── 测试 9：安全加固 ──────────


def test_security_hardening() -> None:
    section("9. 安全加固")
    content = DOCKERFILE.read_text(encoding="utf-8")

    # HEALTHCHECK
    check("HEALTHCHECK 配置", "HEALTHCHECK" in content)
    check(
        "HEALTHCHECK 使用 curl /health",
        "curl" in content and "/health" in content,
    )
    check(
        "HEALTHCHECK start-period",
        "start-period" in content,
    )

    # --no-install-recommends（精简镜像）
    check(
        "apt-get --no-install-recommends",
        "--no-install-recommends" in content,
    )

    # 清理 apt 缓存
    check(
        "清理 apt 缓存 rm -rf /var/lib/apt/lists/*",
        "rm -rf /var/lib/apt/lists/*" in content,
    )

    # pip --no-cache-dir
    check(
        "pip install --no-cache-dir",
        "--no-cache-dir" in content,
    )

    # 环境变量默认值
    check(
        "ENV ENV=production 默认值",
        "ENV=production" in content or "ENV ENV=production" in content,
    )
    check(
        "PYTHONUNBUFFERED=1",
        "PYTHONUNBUFFERED=1" in content,
    )


# ────────── 测试 10：docker run 启动指令 ──────────


def test_docker_run_readiness() -> None:
    section("10. docker run 启动指令")
    content = DOCKERFILE.read_text(encoding="utf-8")

    # ENTRYPOINT
    check("ENTRYPOINT 配置", "ENTRYPOINT" in content)
    check(
        "ENTRYPOINT 调用 entrypoint.sh",
        "entrypoint.sh" in content,
    )

    # EXPOSE
    check("EXPOSE 80", "EXPOSE 80" in content)

    # WORKDIR /app
    check("WORKDIR /app", "WORKDIR /app" in content)


# ────────── 测试 11：与 vite dev proxy 一致性 ──────────


def test_vite_consistency() -> None:
    section("11. 与 vite dev proxy 一致性")
    vite_config = ROOT / "frontend" / "vite.config.ts"
    check("vite.config.ts 存在", vite_config.exists())
    if not vite_config.exists():
        return

    vite_content = vite_config.read_text(encoding="utf-8")

    # vite proxy /api → localhost:8000（strip /api）
    check(
        "vite proxy 配置 /api",
        "/api" in vite_content and "proxy" in vite_content,
    )
    check(
        "vite proxy target localhost:8000",
        "localhost:8000" in vite_content or "127.0.0.1:8000" in vite_content,
    )
    check(
        "vite proxy rewrite strip /api",
        "rewrite" in vite_content and "/api" in vite_content,
    )

    # nginx.conf /api 也应 strip（与 vite 一致）
    nginx_content = NGINX_CONF.read_text(encoding="utf-8")
    check(
        "nginx /api proxy_pass 末尾 /（strip /api，与 vite 一致）",
        "proxy_pass http://opskg_backend/;" in nginx_content
        or "proxy_pass http://127.0.0.1:8000/;" in nginx_content,
    )


# ────────── 测试 12：与 K8s 部署兼容性 ──────────


def test_k8s_compatibility() -> None:
    section("12. 与 K8s 部署兼容性")
    # frontend-deployment.yaml 中的镜像名应与 Dockerfile 构建产物匹配
    frontend_dep = ROOT / "deploy" / "k8s" / "frontend-deployment.yaml"
    check("frontend-deployment.yaml 存在", frontend_dep.exists())
    if not frontend_dep.exists():
        return

    content = frontend_dep.read_text(encoding="utf-8")

    # 镜像名 opskg-frontend:latest
    check(
        "frontend Deployment 使用 opskg-frontend 镜像",
        "opskg-frontend" in content,
    )

    # K8s frontend Service 端口 80（与 nginx 一致）
    check(
        "frontend Service port=80",
        "port: 80" in content,
    )

    # K8s Ingress /api rewrite-target（与 Docker nginx /api strip 一致）
    ingress = ROOT / "deploy" / "k8s" / "ingress.yaml"
    check("ingress.yaml 存在", ingress.exists())
    if ingress.exists():
        ingress_content = ingress.read_text(encoding="utf-8")
        check(
            "K8s Ingress /api 含 rewrite-target",
            "rewrite-target" in ingress_content and "/api" in ingress_content,
        )
        check(
            "K8s Ingress rewrite-target=/$2（strip /api）",
            "/$2" in ingress_content,
        )


# ────────── 测试 13：docker-compose 兼容性 ──────────


def test_docker_compose_compatibility() -> None:
    section("13. docker-compose.yml 兼容性")
    compose = ROOT / "docker-compose.yml"
    check("docker-compose.yml 存在", compose.exists())
    if not compose.exists():
        return

    content = compose.read_text(encoding="utf-8")

    # 检查 docker-compose 是否仍可用（应保留 backend 服务构建）
    check(
        "docker-compose 含 backend 服务",
        "backend" in content and "build" in content,
    )

    # docker-compose 的 backend 端口暴露 8000
    check(
        "docker-compose backend 暴露 8000",
        "8000:8000" in content,
    )


# ────────── 主函数 ──────────


def main() -> int:
    print("=" * 60)
    print("S13-4 Dockerfile 前端集成验证")
    print("=" * 60)

    test_dockerfile_multistage()
    test_frontend_build_stage()
    test_runtime_stage()
    test_nginx_conf()
    test_supervisord_conf()
    test_entrypoint()
    test_dockerignore()
    test_layer_cache()
    test_security_hardening()
    test_docker_run_readiness()
    test_vite_consistency()
    test_k8s_compatibility()
    test_docker_compose_compatibility()

    print("\n" + "=" * 60)
    print(f"总计：{PASS} 通过 / {FAIL} 失败")
    print("=" * 60)

    if FAIL > 0:
        print("\n失败项：")
        for name, ok, detail in TESTS:
            if not ok:
                print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
