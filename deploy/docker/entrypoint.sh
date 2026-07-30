#!/bin/sh
# OpsKG 单镜像 entrypoint（S13-4 多阶段构建）
#
# 功能：
#   1. 以 root 修复挂载卷权限（PVC 场景下卷属主默认是 root，opskg 无法写入）
#   2. 创建必要的运行时目录
#   3. 根据 OPSKG_UVICORN_WORKERS 环境变量动态调整 uvicorn worker 数
#   4. 用 gosu 降权到 opskg 用户启动 supervisord（非 root 运行服务）
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

# ── 2. 动态调整 uvicorn workers（需 root 写 /etc/supervisor/）──
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
  运行用户        : opskg（非 root，gosu 降权）
  配置来源        : 环境变量（参考 .env.example / deploy/k8s/configmap.yaml）
============================================================
EOF

# ── 4. gosu 降权到 opskg 启动 supervisord（前台运行，容器主进程）──
# gosu 比 su/sudo 更适合 Docker（不创建新会话，信号传递正确）
# Debian supervisor 包装到 /usr/bin/supervisord（非 /usr/local/bin/）
exec gosu opskg /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
