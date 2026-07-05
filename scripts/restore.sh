#!/usr/bin/env bash
# OpsKG 恢复脚本（P0-4）
# 从备份文件恢复数据
# 用法: ./scripts/restore.sh <backup_file.tar.gz>
set -euo pipefail

BACKUP_FILE="${1:-}"
if [ -z "${BACKUP_FILE}" ]; then
    echo "用法: $0 <backup_file.tar.gz>"
    echo "可用备份:"
    ls -lh /workspace/data/backups/*.tar.gz 2>/dev/null || echo "  无备份文件"
    exit 1
fi

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "错误: 备份文件不存在: ${BACKUP_FILE}"
    exit 1
fi

echo "━━━ OpsKG 恢复开始 ━━━"
echo "备份文件: ${BACKUP_FILE}"

# 解压到临时目录
TEMP_DIR=$(mktemp -d)
tar xzf "${BACKUP_FILE}" -C "${TEMP_DIR}"
EXTRACTED=$(ls -d "${TEMP_DIR}"/opskg_* | head -1)

if [ ! -d "${EXTRACTED}" ]; then
    echo "错误: 备份文件格式不正确"
    rm -rf "${TEMP_DIR}"
    exit 1
fi

# 1. 恢复 SQLite + 上传文件
DATA_DIR="/workspace/backend/data"
if [ -d "${EXTRACTED}/data" ]; then
    echo "[1/2] 恢复数据目录..."
    mkdir -p "${DATA_DIR}"
    cp -rf "${EXTRACTED}/data/"* "${DATA_DIR}/"
    echo "  ✓ 数据已恢复到 ${DATA_DIR}"
else
    echo "  ⚠ 备份中无 data 目录"
fi

# 2. 恢复 Neo4j
if [ -f "${EXTRACTED}/neo4j_dump.cypher" ]; then
    echo "[2/2] 恢复 Neo4j 数据..."
    if command -v cypher-shell &>/dev/null; then
        NEO4J_USER="${NEO4J_USER:-neo4j}"
        NEO4J_PASSWORD="${NEO4J_PASSWORD:-password}"
        NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
        echo "  注意: Neo4j 恢复需要先清空数据库，请确认后执行:"
        echo "    cypher-shell -u ${NEO4J_USER} -p ${NEO4J_PASSWORD} -a ${NEO4J_URI} < ${EXTRACTED}/neo4j_dump.cypher"
    else
        echo "  ⚠ cypher-shell 未安装，Neo4j 备份文件位于: ${EXTRACTED}/neo4j_dump.cypher"
    fi
else
    echo "[2/2] 无 Neo4j 备份，跳过"
fi

# 清理
rm -rf "${TEMP_DIR}"
echo ""
echo "━━━ 恢复完成 ━━━"
echo "请重启 OpsKG 服务使数据生效"
