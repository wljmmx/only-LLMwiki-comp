#!/bin/sh
# OpsKG 单镜像 entrypoint（S13-4 多阶段构建）
#
# 功能：
#   1. 根据 OPSKG_UVICORN_WORKERS 环境变量动态调整 uvicorn worker 数
#   2. 创建必要的运行时目录
#   3. 启动 supervisord（管理 nginx + uvicorn）
set -e

# ── 1. 准备运行时目录 ──
# Dockerfile 已创建并 chown 这些目录；此处 mkdir -p 确保挂载卷场景下目录存在
# nginx 需要 client_temp 和 proxy_temp 目录用于上传和代理临时文件
mkdir -p /app/data /var/log/nginx /var/log/supervisor \
         /var/lib/nginx/body /var/lib/nginx/proxy /var/lib/nginx/fastcgi \
         /var/cache/nginx /var/cache/nginx/client_temp /var/cache/nginx/proxy_temp /run/nginx
chown -R opskg:opskg /app /var/log/nginx /var/log/supervisor \
                     /var/lib/nginx /var/cache/nginx /run/nginx

# ── 2. 动态调整 uvicorn workers ──
WORKERS="${OPSKG_UVICORN_WORKERS:-2}"
if [ "$WORKERS" != "2" ]; then
    echo "[entrypoint] 调整 uvicorn workers: 2 → $WORKERS"
    # 使用 sed 修改 supervisord.conf 中的 --workers 值
    sed -i "s/--workers 2/--workers $WORKERS/" /etc/supervisor/conf.d/supervisord.conf
fi

# ── 3. 显示启动信息 ──
cat <<EOF
============================================================
OpsKG 单镜像启动
  uvicorn workers : $WORKERS
  nginx           : 监听 8080（非特权端口）
  后端内部端口    : 8000（仅 nginx 访问）
  数据目录        : /app/data（建议挂载 PVC）
  运行用户        : opskg（非 root）
  配置来源        : 环境变量（参考 .env.example / deploy/k8s/configmap.yaml）
============================================================
EOF

# ── 4. 启动 supervisord（前台运行，容器主进程）──
# Debian supervisor 包装到 /usr/bin/supervisord（非 /usr/local/bin/）
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
