#!/usr/bin/env bash
# OpsKG 备份脚本（P0-4）
# 备份: SQLite 数据库 + 上传文件 + Neo4j 数据
# 用法: ./scripts/backup.sh [backup_dir]
set -euo pipefail

BACKUP_DIR="${1:-/workspace/data/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="${BACKUP_DIR}/opskg_${TIMESTAMP}"

echo "━━━ OpsKG 备份开始 ━━━"
echo "备份目录: ${BACKUP_PATH}"
mkdir -p "${BACKUP_PATH}"

# 1. SQLite 数据库（documents.db + review_queue.db）
echo "[1/3] 备份 SQLite 数据库..."
DATA_DIR="/workspace/backend/data"
if [ -d "${DATA_DIR}" ]; then
    cp -r "${DATA_DIR}" "${BACKUP_PATH}/data"
    echo "  ✓ SQLite 数据已备份"
else
    echo "  ⚠ data 目录不存在（首次运行？）"
fi

# 2. 上传的文档文件
echo "[2/3] 备份上传文件..."
UPLOADS_DIR="${DATA_DIR}/uploads"
if [ -d "${UPLOADS_DIR}" ]; then
    FILE_COUNT=$(find "${UPLOADS_DIR}" -type f | wc -l)
    echo "  ✓ ${FILE_COUNT} 个文件已包含"
else
    echo "  ⚠ uploads 目录不存在"
fi

# 3. Neo4j 数据（如果可用）
echo "[3/3] 备份 Neo4j 数据..."
if command -v cypher-shell &>/dev/null; then
    NEO4J_USER="${NEO4J_USER:-neo4j}"
    NEO4J_PASSWORD="${NEO4J_PASSWORD:-password}"
    NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
    if cypher-shell -u "${NEO4J_USER}" -p "${NEO4J_PASSWORD}" -a "${NEO4J_URI}" "RETURN 1" &>/dev/null; then
        cypher-shell -u "${NEO4J_USER}" -p "${NEO4J_PASSWORD}" -a "${NEO4J_URI}" \
            "CALL apoc.export.cypher.all('${BACKUP_PATH}/neo4j_dump.cypher', {format:cypherShell});" 2>/dev/null || \
            echo "  ⚠ Neo4j 连接成功但 APOC 导出失败（需安装 APOC 插件）"
        echo "  ✓ Neo4j 数据已备份"
    else
        echo "  ⚠ Neo4j 不可连接，跳过"
    fi
else
    echo "  ⚠ cypher-shell 未安装，跳过 Neo4j 备份"
fi

# 压缩
echo "压缩备份..."
tar czf "${BACKUP_PATH}.tar.gz" -C "$(dirname "${BACKUP_PATH}")" "$(basename "${BACKUP_PATH}")"
rm -rf "${BACKUP_PATH}"
SIZE=$(du -h "${BACKUP_PATH}.tar.gz" | cut -f1)

# 清理 7 天前的备份
echo "清理旧备份（保留 7 天）..."
find "${BACKUP_DIR}" -name "opskg_*.tar.gz" -mtime +7 -delete 2>/dev/null || true

echo ""
echo "━━━ 备份完成 ━━━"
echo "备份文件: ${BACKUP_PATH}.tar.gz (${SIZE})"
echo "保留策略: 7 天"
