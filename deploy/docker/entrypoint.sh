#!/bin/sh
# OpsKG 单镜像 entrypoint（S13-4 多阶段构建）
#
# 功能：
#   1. 根据 OPSKG_UVICORN_WORKERS 环境变量动态调整 uvicorn worker 数
#   2. 创建必要的运行时目录
#   3. 启动 supervisord（管理 nginx + uvicorn）
set -e

# ── 1. 准备运行时目录 ──
mkdir -p /app/data /var/log/nginx /var/log/supervisor /var/lib/nginx/body /var/lib/nginx/proxy /var/lib/nginx/fastcgi
# 修正 nginx 缓存目录权限（容器以 root 运行，无需 chown）

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
  nginx           : 监听 80
  后端内部端口    : 8000（仅 nginx 访问）
  数据目录        : /app/data（建议挂载 PVC）
  配置来源        : 环境变量（参考 .env.example / deploy/k8s/configmap.yaml）
============================================================
EOF

# ── 4. 启动 supervisord（前台运行，容器主进程）──
# Debian supervisor 包装到 /usr/bin/supervisord（非 /usr/local/bin/）
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
