#!/bin/sh
# OpsKG uvicorn 启动包装脚本
#
# 功能：
#   1. 启动前验证关键 Python 依赖可导入
#   2. 启动前验证后端代码完整
#   3. 输出诊断日志便于排查启动失败
set -e

cd /app

echo "[uvicorn-start] 验证 Python 依赖..."
/usr/local/bin/python3 -c "
import fastapi
import uvicorn
import slowapi
import structlog
print('  核心依赖 OK')
" || {
    echo "[uvicorn-start] 核心依赖导入失败"
    exit 1
}

echo "[uvicorn-start] 验证后端代码..."
if [ ! -f /app/app/main.py ]; then
    echo "[uvicorn-start] 错误: /app/app/main.py 不存在"
    exit 1
fi

echo "[uvicorn-start] 验证配置..."
if [ ! -f /app/app/config.py ]; then
    echo "[uvicorn-start] 错误: /app/app/config.py 不存在"
    exit 1
fi

echo "[uvicorn-start] 启动 uvicorn..."
# 使用环境变量 LOG_LEVEL（默认 info），确保 .env 中的 debug 设置生效
UVICORN_LOG_LEVEL="${LOG_LEVEL:-info}"
exec /usr/local/bin/python3 -m uvicorn app.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 2 \
    --log-level "${UVICORN_LOG_LEVEL}" \
    --access-log \
    --no-proxy-headers
