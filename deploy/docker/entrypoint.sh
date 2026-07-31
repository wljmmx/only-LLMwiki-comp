#!/bin/sh
# OpsKG 单镜像 entrypoint（S13-4 多阶段构建）
#
# 功能：
#   1. 以 root 修复挂载卷权限（PVC 场景下卷属主默认是 root，opskg 无法写入）
#   2. 创建必要的运行时目录
#   3. 根据 OPSKG_UVICORN_WORKERS 环境变量动态调整 uvicorn worker 数
#   4. 验证关键文件权限（防止 EACCES 类错误）
#   5. 用 gosu 降权到 opskg 用户启动 supervisord（非 root 运行服务）
#
# 设计：容器以 root 启动（Dockerfile 不设 USER），entrypoint 完成初始化后
#       用 gosu 降权。这是 Docker 非 root 部署的标准模式（参考 nginx 官方镜像）。
#       实际服务进程（nginx/uvicorn）仍以 opskg 非 root 运行。
set -e

# ── 1. 以 root 准备运行时目录 + 修复挂载卷权限 ──
# /app/data 是 VOLUME 挂载点，PVC 场景下卷属主默认是 root，opskg 用户无法写入。
# 这里 chown 确保挂载后仍归 opskg 所有（仅 root 能 chown）。
mkdir -p /app/data /var/log/nginx /var/log/supervisor \
         /var/lib/nginx/body /var/lib/nginx/proxy /var/lib/nginx/fastcgi \
         /var/cache/nginx /run
chown -R opskg:opskg /app/data /var/log/nginx /var/log/supervisor \
                    /var/lib/nginx /var/cache/nginx /run

# ── 2. 验证关键文件权限（防止 EACCES）──
echo "[entrypoint] 验证文件权限..."

# 验证 nginx 二进制
if [ -x /usr/sbin/nginx ]; then
    echo "  ✓ nginx 可执行"
else
    echo "  ✗ nginx 不可执行"
    exit 1
fi

# 验证 nginx 配置
if [ -r /etc/nginx/nginx.conf ]; then
    echo "  ✓ nginx.conf 可读"
else
    echo "  ✗ nginx.conf 不可读"
    exit 1
fi

# 验证 mime.types
if [ -r /etc/nginx/mime.types ]; then
    echo "  ✓ mime.types 可读"
else
    echo "  ✗ mime.types 不可读"
    exit 1
fi

# 验证 supervisord 二进制
if [ -x /usr/bin/supervisord ]; then
    echo "  ✓ supervisord 可执行"
else
    echo "  ✗ supervisord 不可执行"
    exit 1
fi

# 验证 uvicorn 启动脚本
if [ -x /usr/local/bin/start-uvicorn.sh ]; then
    echo "  ✓ start-uvicorn.sh 可执行"
else
    echo "  ✗ start-uvicorn.sh 不可执行"
    exit 1
fi

# 验证 Python + 依赖
if /usr/local/bin/python3 -c "import fastapi, uvicorn, slowapi, structlog; print('  ✓ Python 依赖 OK')"; then
    echo "[entrypoint] Python 核心依赖验证通过"
else
    echo "[entrypoint] ✗ Python 核心依赖验证失败"
    exit 1
fi

# 验证后端代码存在
if [ -f /app/app/main.py ]; then
    echo "  ✓ /app/app/main.py 存在"
else
    echo "  ✗ /app/app/main.py 不存在"
    exit 1
fi

# 验证 nginx 配置语法
if nginx -t 2>&1; then
    echo "[entrypoint] ✓ nginx 配置语法正确"
else
    echo "[entrypoint] ✗ nginx 配置语法错误"
    exit 1
fi

# ── 3. 动态调整 uvicorn workers ──
WORKERS="${OPSKG_UVICORN_WORKERS:-2}"
if [ "$WORKERS" != "2" ]; then
    echo "[entrypoint] 调整 uvicorn workers: 2 → $WORKERS"
    sed -i "s/--workers 2/--workers $WORKERS/" /etc/supervisor/conf.d/supervisord.conf
fi

# ── 4. 显示启动信息 ──
cat <<EOF
============================================================
OpsKG 单镜像启动
  uvicorn workers : $WORKERS
  nginx           : 监听 8080（非特权端口）
  后端内部端口    : 8000（仅 nginx 访问）
  数据目录        : /app/data（建议挂载 PVC）
  运行用户        : opskg（非 root，gosu 降权）
  配置来源        : 环境变量（参考 .env.example / deploy/k8s/configmap.yaml）
============================================================
EOF

# ── 5. gosu 降权到 opskg 启动 supervisord ──
echo "[entrypoint] 以 opskg 用户启动 supervisord..."
exec gosu opskg /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
