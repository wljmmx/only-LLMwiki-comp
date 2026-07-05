# 【紧急】生产环境 MySQL 集群故障复盘报告

<strong>作者：</strong>运维三组 张工  |  日期：<u>2026-06-30</u>

# **一、故障概述**

2026年6月30日 14:23:17，**生产环境 MySQL 主库（db-master-01）** 发生严重故障，导致订单服务、用户服务、支付网关共计 7 个微服务不可用。**影响范围：** 线上交易中断 37 分钟，预估损失约 12.6 万元。（注：该数据由财务部提供，待最终确认）

# **二、故障现象**

- 2.1 监控告警序列（按时间线）
- 14:23:17 - Prometheus 触发 MySQL 连接数告警（阈值 5000，实际值 8743）
- 14:23:25 - Grafana 面板显示 db-master-01 CPU 100%，磁盘 IO 等待 98%
- 14:24:01 - 业务监控：订单创建接口 5xx 错误率 100%
- 14:25:10 - 收到多个用户投诉（App 无法下单、页面白屏）
- 14:26:00 - 运维值班介入，登录堡垒机排查
- 核心服务（T0级别）：

## **2.2 受影响服务清单**

- order-service（订单服务）
- user-service（用户服务）
- payment-gateway（支付网关）

- 非核心服务（T2级别）：

- notification-service（通知服务）
- report-service（报表服务）

# **三、根因分析**

经排查，根因为慢查询导致连接池耗尽。具体分析如下：

<table><tr><td><p><strong>步骤</strong></p></td><td><p><strong>检查项</strong></p></td><td><p><strong>结果</strong></p></td><td><p><strong>结论</strong></p></td></tr><tr><td><p>1</p></td><td><p>SHOW PROCESSLIST</p></td><td><p>大量 SELECT 处于 Sending data 状态</p></td><td><p>存在慢查询</p></td></tr><tr><td><p>2</p></td><td><p>慢查询日志</p></td><td><p>SQL: SELECT * FROM orders WHERE status=0 AND created_at &lt; ... 扫描 870 万行</p></td><td><p>无索引导致全表扫描</p></td></tr><tr><td><p>3</p></td><td><p>EXPLAIN 分析</p></td><td><p>type=ALL, rows=8723451</p></td><td><p>缺少复合索引(status, created_at)</p></td></tr><tr><td><p>4</p></td><td><p>应用日志</p></td><td><p>连接池配置 maxActive=50, 超时 30s</p></td><td><p>连接池过小+慢查询=雪崩</p></td></tr></table>

# **四、涉及配置参数**

**关键配置项：**

innodb\_buffer\_pool\_size = **64G**  // 建议调整为 128G（当前服务器内存 256G，利用率偏低）

max\_connections = **2000**  // 短时峰值已接近上限，建议提升至 5000

slow\_query\_log = **ON**  // long\_query\_time=1s，建议调整为 0.5s

connect\_timeout = **30**  // 建议调整为 10s（快速失败优于长时间等待）

# **五、修复措施与时间线**

修复时间线：

1. 14:30 - 执行 ALTER TABLE orders ADD INDEX idx\_status\_created(status, created\_at);（耗时 11 分钟）
2. 14:41 - 索引创建完毕，慢查询恢复正常（< 0.01s）
3. 14:42 - 重启受影响服务，逐步恢复流量
4. 14:48 - 所有服务恢复正常，监控指标回落
5. 15:00 - 编写事故报告，通知相关方

## **修复 SQL**

-- 添加索引
ALTER TABLE orders ADD INDEX idx\_status\_created(status, created\_at);
-- 验证
EXPLAIN SELECT \* FROM orders WHERE status=0 AND created\_at < NOW();