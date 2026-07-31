#!/bin/sh
# OpsKG nginx 启动包装脚本
#
# 功能：
#   1. 启动前验证 nginx 配置
#   2. 确保所有临时目录存在且可写
#   3. 输出启动错误详情便于排查
set -e

echo "[nginx-start] 验证 nginx 配置..."

# 确保所有临时目录存在
for dir in /var/lib/nginx/body /var/lib/nginx/proxy /var/lib/nginx/fastcgi /var/lib/nginx/uwsgi /var/lib/nginx/scgi /var/cache/nginx; do
    if [ ! -d "$dir" ]; then
        echo "  创建目录 $dir"
        mkdir -p "$dir"
    fi
    # 验证可写
    if [ ! -w "$dir" ]; then
        echo "  X 目录 $dir 不可写"
        exit 1
    fi
done

# 确保日志目录可写
if [ ! -w "/var/log/nginx" ]; then
    echo "  X /var/log/nginx 不可写"
    exit 1
fi

# 确保前端静态文件存在
if [ ! -f /usr/share/nginx/html/index.html ]; then
    echo "  X /usr/share/nginx/html/index.html 不存在"
    exit 1
fi
echo "  OK 前端静态文件存在"

# 验证 nginx 配置语法
if ! /usr/sbin/nginx -t 2>&1; then
    echo "[nginx-start] X nginx 配置验证失败"
    exit 1
fi
echo "[nginx-start] OK nginx 配置验证通过"

# 清理可能残留的旧 PID 文件
rm -f /tmp/nginx.pid 2>/dev/null

echo "[nginx-start] 启动 nginx..."
exec /usr/sbin/nginx -g "daemon off;"
