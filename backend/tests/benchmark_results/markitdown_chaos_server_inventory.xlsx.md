## 服务器清单
| 生产环境服务器清单（截止 2026-06-30） | Unnamed: 1 | Unnamed: 2 | Unnamed: 3 | Unnamed: 4 | Unnamed: 5 |
| --- | --- | --- | --- | --- | --- |
| 设备信息 | NaN | NaN | NaN | NaN | NaN |
| 主机名 | IP地址 | 角色 | CPU | 内存(GB) | 状态 |
| db-master-01 | 10.0.1.1 | MySQL主库 | 32核 | 256 | ⚠️ 故障 |
| db-slave-01 | 10.0.1.2 | MySQL从库 | 16核 | 128 | 正常 |
| web-01 | 10.0.0.1 | Nginx | 8核 | 64 | 正常 |
| web-02 | 10.0.0.11 | Nginx | 8核 | 64 | 维护中 |
| cache-01 | 10.0.2.1 | Redis Cluster | 16核 | 128 | 正常 |
| cache-02 | 10.0.2.2 | Redis Cluster | 16核 | 128 | 正常 |
| mq-01 | 10.0.3.1 | Kafka | 8核 | 64 | 正常 |
| monitor-01 | 10.0.9.1 | Prometheus | 4核 | 32 | 正常 |

## MySQL参数对比
| MySQL 8.0 参数调优对照表 | Unnamed: 1 | Unnamed: 2 | Unnamed: 3 |
| --- | --- | --- | --- |
| 参数名 | 默认值 | 当前值 | 建议值 |
| innodb\_buffer\_pool\_size | 128M | 64G | 128G |
| innodb\_log\_file\_size | 48M | 2G | 4G |
| max\_connections | 151 | 2000 | 5000 |
| innodb\_flush\_log\_at\_trx\_commit | 1 | 2 | 2 |
| sync\_binlog | 1 | 0 | 1000 |
| innodb\_io\_capacity | 200 | 2000 | 4000 |
| tmp\_table\_size | 16M | 256M | 512M |
| max\_heap\_table\_size | 16M | 256M | 512M |
| innodb\_thread\_concurrency | 0 | 0 | 32 |
| innodb\_read\_io\_threads | 4 | 8 | 16 |
| innodb\_write\_io\_threads | 4 | 8 | 16 |
| query\_cache\_size | 0 | 256M | ❌ 8.0 已移除该参数 |
| innodb\_adaptive\_hash\_index | ON | OFF | ON（建议开启） |

## 故障时间线
| 时间线（隐藏清理中） |
| --- |
| 隐藏行 2 |
| 隐藏行 3 |
| 隐藏行 4 |
| 隐藏行 5 |