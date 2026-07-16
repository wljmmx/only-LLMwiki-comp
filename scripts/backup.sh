#!/usr/bin/env bash
# OpsKG 备份脚本（P0-4）
# 备份: SQLite 数据库（在线热备 + sha256 校验） + 上传文件 + Neo4j 数据
# 用法: ./scripts/backup.sh [backup_dir]
set -euo pipefail

BACKUP_DIR="${1:-/workspace/data/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="${BACKUP_DIR}/opskg_${TIMESTAMP}"

echo "━━━ OpsKG 备份开始 ━━━"
echo "备份目录: ${BACKUP_PATH}"
mkdir -p "${BACKUP_PATH}"

# 1. SQLite 数据库：使用 SQLite Online Backup API 热备 + sha256 校验
#    避免 WAL 模式下 cp -r 可能捕获不一致快照（M10 修复）
echo "[1/3] 备份 SQLite 数据库（在线热备）..."
DATA_DIR="/workspace/backend/data"
if [ -d "${DATA_DIR}" ]; then
    cd /workspace/backend
    python3 -c "
from app.ha.backup import backup_all_databases
result = backup_all_databases()
print(f'BACKUP_COUNT={result[\"file_count\"]}')
print(f'BACKUP_SIZE={result[\"total_size_bytes\"]}')
print(f'BACKUP_TS={result[\"timestamp\"]}')
" 2>&1

    # 将热备输出（.db 文件 + manifest.json + sha256 checksums）复制到备份目录
    LATEST_BACKUP=$(ls -1dt "${DATA_DIR}/backups/"*/ 2>/dev/null | head -1)
    if [ -n "${LATEST_BACKUP}" ] && [ -d "${LATEST_BACKUP}" ]; then
        cp -r "${LATEST_BACKUP}" "${BACKUP_PATH}/databases"
        echo "  ✓ SQLite 热备已复制（含 manifest.json + sha256 checksums）"
    else
        echo "  ⚠ 热备未生成输出，回退到文件拷贝"
        mkdir -p "${BACKUP_PATH}/databases"
        for db in "${DATA_DIR}"/*.db; do
            [ -f "${db}" ] && cp "${db}" "${BACKUP_PATH}/databases/"
        done
    fi

    # 复制上传文件
    UPLOADS_DIR="${DATA_DIR}/uploads"
    if [ -d "${UPLOADS_DIR}" ]; then
        cp -r "${UPLOADS_DIR}" "${BACKUP_PATH}/uploads"
        FILE_COUNT=$(find "${BACKUP_PATH}/uploads" -type f 2>/dev/null | wc -l)
        echo "  ✓ uploads 目录已复制（${FILE_COUNT} 个文件）"
    fi
else
    echo "  ⚠ data 目录不存在（首次运行？）"
fi

# 2. 上传文件（已在步骤 1 中处理）
echo "[2/3] 上传文件已在步骤 1 中处理"

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

# SHA256 校验和（M10 修复）
echo "计算 SHA256 校验和..."
sha256sum "${BACKUP_PATH}.tar.gz" > "${BACKUP_PATH}.tar.gz.sha256"
CHECKSUM=$(cut -d' ' -f1 "${BACKUP_PATH}.tar.gz.sha256")
echo "  SHA256: ${CHECKSUM}"

# 清理 7 天前的备份
echo "清理旧备份（保留 7 天）..."
find "${BACKUP_DIR}" -name "opskg_*.tar.gz" -mtime +7 -delete 2>/dev/null || true
find "${BACKUP_DIR}" -name "opskg_*.tar.gz.sha256" -mtime +7 -delete 2>/dev/null || true

echo ""
echo "━━━ 备份完成 ━━━"
echo "备份文件: ${BACKUP_PATH}.tar.gz (${SIZE})"
echo "SHA256:    ${CHECKSUM}"
echo "保留策略: 7 天"
