# Alembic 数据库迁移工具

## 概述

Alembic 是 SQLAlchemy 生态的数据库 schema 版本管理工具。
OpsKG 使用 Alembic 管理 SQLite 数据库 schema 变更。

## 配置

Alembic 配置位于 `alembic.ini`，数据库 URL 通过环境变量 `OPSKG_DATABASE_URL` 配置。

## 常用命令

```bash
# 生成迁移（autogenerate，基于模型变更）
cd backend
alembic revision --autogenerate -m "描述"

# 创建空迁移
alembic revision -m "描述"

# 升级到最新版本
alembic upgrade head

# 升级到指定版本
alembic upgrade <revision>

# 回滚一个版本
alembic downgrade -1

# 查看当前版本
alembic current

# 查看迁移历史
alembic history
```

## 版本管理

- 迁移文件位于 `backend/alembic/versions/`
- 每个迁移文件包含 `upgrade()` 和 `downgrade()` 函数
- 迁移 ID 基于时间戳（如 `20260716_001`）

## 生产环境建议

1. 部署前先执行 `alembic upgrade head`
2. 在 Docker 入口脚本中自动执行迁移
3. 迁移前备份数据库