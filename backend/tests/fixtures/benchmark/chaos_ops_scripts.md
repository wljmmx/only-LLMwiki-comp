# 运维自动化脚本大全

> **维护者**: 基础架构组 | **仓库**: git@ops/scripts.git | **最后更新**: 2026-06-25

## 1. 日志清理脚本

```bash
#!/bin/bash
# 清理 7 天前的日志，保留压缩包
LOG_DIR="/var/log/app"
find "$LOG_DIR" -name "*.log" -mtime +7 -exec gzip {} \;
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 清理完成" >> /var/log/cleanup.log
```

## 2. MySQL 备份脚本

| 参数 | 值 | 说明 |
|------|-----|------|
| DB_HOST | 10.0.1.1 | 主库地址 |
| DB_PORT | 3306 | |
| BACKUP_RETENTION | 7 | 保留天数 |
| PARALLEL | 4 | 并发线程数 |

```sql
-- 备份前检查
SELECT COUNT(*) AS total_rows FROM information_schema.processlist WHERE user='backup';
-- 执行备份
mysqldump -h ${DB_HOST} -P ${DB_PORT} --single-transaction --routines --triggers ${DB_NAME} > ${BACKUP_FILE}
```

## 3. 常见问题排查

### 3.1 磁盘空间不足
- **现象**: `df -h` 显示 / 分区使用率 > 90%
- **排查步骤**:
  1. `du -sh /* 2>/dev/null | sort -rh | head -20` 找大目录
  2. 检查 Docker 镜像：`docker system df`
  3. 检查日志文件：`journalctl --disk-usage`
- **解决方案**: 清理旧日志、Docker 镜像、core dump

### 3.2 内存泄漏
- **现象**: 服务内存持续增长，最终 OOM
- **排查**:
  ```bash
  # 查看进程内存
  ps aux --sort=-%mem | head -10
  # 查看内存详情
  pmap -x $(pgrep java) | tail -20
  ```

## 4. 监控告警规则（Prometheus）

| 告警名 | 表达式 | 阈值 | 级别 |
|--------|--------|------|------|
| HighCPUUsage | `avg(rate(node_cpu[5m])) by (instance) > 0.9` | 90% | P1 |
| DiskFull | `node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes < 0.1` | 10% | P0 |
| MySQLSlowQueries | `rate(mysql_slow_queries[5m]) > 10` | 10/min | P2 |
| Nginx5xx | `rate(nginx_requests{status=~"5.."}[5m]) > 0` | 1 | P1 |

> ⚠️ P0 告警 5 分钟内未响应自动升级至技术总监。
