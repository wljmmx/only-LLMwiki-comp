# Nginx 部署运维手册

## 服务概述
Nginx 是高并发 Web 服务器，监听 80 和 443 端口。
- 主机: web-prod-01
- 角色: 反向代理 + 负载均衡
- 部署版本: nginx-1.24.0

## 关键依赖
- 依赖服务: redis-cache (10.0.0.5:6379)
- 依赖服务: mysql-primary (10.0.0.6:3306)
- 上游应用: app-server-01, app-server-02

## 故障处理 Runbook

### 场景 1: 502 Bad Gateway
- 触发条件: 上游应用无响应超过 30 秒
- 处理步骤:
  1. 检查上游应用进程: `systemctl status app-server`
  2. 查看错误日志: `tail -f /var/log/nginx/error.log`
  3. 重启上游: `systemctl restart app-server`
- 关键指标: upstream_response_time, error_rate

### 场景 2: CPU 使用率过高
- 阈值: 持续 5 分钟超过 80%
- 处理步骤:
  1. 检查 worker_processes 配置
  2. 查看 active connections: `nginx -T | grep worker`
  3. 调整 worker_connections 至 10240

## 配置要点
- worker_processes: auto
- worker_connections: 10240
- keepalive_timeout: 65
- gzip: on
- log_format: main
