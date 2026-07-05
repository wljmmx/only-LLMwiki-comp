# 服务器清单

<table>
  <tr>
    <th colspan="6"><p>生产环境服务器清单（截止 2026-06-30）</p></th>
  </tr>
  <tr>
    <td colspan="6"><p>设备信息</p></td>
  </tr>
  <tr>
    <td><p>主机名</p></td>
    <td><p>IP地址</p></td>
    <td><p>角色</p></td>
    <td><p>CPU</p></td>
    <td><p>内存(GB)</p></td>
    <td><p>状态</p></td>
  </tr>
  <tr>
    <td><p>db-master-01</p></td>
    <td><p>10.0.1.1</p></td>
    <td><p>MySQL主库</p></td>
    <td><p>32核</p></td>
    <td><p>256</p></td>
    <td><p>⚠️ 故障</p></td>
  </tr>
  <tr>
    <td><p>db-slave-01</p></td>
    <td><p>10.0.1.2</p></td>
    <td><p>MySQL从库</p></td>
    <td><p>16核</p></td>
    <td><p>128</p></td>
    <td><p>正常</p></td>
  </tr>
  <tr>
    <td><p>web-01</p></td>
    <td><p>10.0.0.1</p></td>
    <td><p>Nginx</p></td>
    <td><p>8核</p></td>
    <td><p>64</p></td>
    <td><p>正常</p></td>
  </tr>
  <tr>
    <td><p>web-02</p></td>
    <td><p>10.0.0.11</p></td>
    <td><p>Nginx</p></td>
    <td><p>8核</p></td>
    <td><p>64</p></td>
    <td><p>维护中</p></td>
  </tr>
  <tr>
    <td><p>cache-01</p></td>
    <td><p>10.0.2.1</p></td>
    <td><p>Redis Cluster</p></td>
    <td><p>16核</p></td>
    <td><p>128</p></td>
    <td><p>正常</p></td>
  </tr>
  <tr>
    <td><p>cache-02</p></td>
    <td><p>10.0.2.2</p></td>
    <td><p>Redis Cluster</p></td>
    <td><p>16核</p></td>
    <td><p>128</p></td>
    <td><p>正常</p></td>
  </tr>
  <tr>
    <td><p>mq-01</p></td>
    <td><p>10.0.3.1</p></td>
    <td><p>Kafka</p></td>
    <td><p>8核</p></td>
    <td><p>64</p></td>
    <td><p>正常</p></td>
  </tr>
  <tr>
    <td><p>monitor-01</p></td>
    <td><p>10.0.9.1</p></td>
    <td><p>Prometheus</p></td>
    <td><p>4核</p></td>
    <td><p>32</p></td>
    <td><p>正常</p></td>
  </tr>
</table>

# MySQL参数对比

<table>
  <tr>
    <th colspan="4"><p>MySQL 8.0 参数调优对照表</p></th>
  </tr>
  <tr>
    <td><p>参数名</p></td>
    <td><p>默认值</p></td>
    <td><p>当前值</p></td>
    <td><p>建议值</p></td>
  </tr>
  <tr>
    <td><p>innodb_buffer_pool_size</p></td>
    <td><p>128M</p></td>
    <td><p>64G</p></td>
    <td><p>128G</p></td>
  </tr>
  <tr>
    <td><p>innodb_log_file_size</p></td>
    <td><p>48M</p></td>
    <td><p>2G</p></td>
    <td><p>4G</p></td>
  </tr>
  <tr>
    <td><p>max_connections</p></td>
    <td><p>151</p></td>
    <td><p>2000</p></td>
    <td><p>5000</p></td>
  </tr>
  <tr>
    <td><p>innodb_flush_log_at_trx_commit</p></td>
    <td><p>1</p></td>
    <td><p>2</p></td>
    <td><p>2</p></td>
  </tr>
  <tr>
    <td><p>sync_binlog</p></td>
    <td><p>1</p></td>
    <td><p>0</p></td>
    <td><p>1000</p></td>
  </tr>
  <tr>
    <td><p>innodb_io_capacity</p></td>
    <td><p>200</p></td>
    <td><p>2000</p></td>
    <td><p>4000</p></td>
  </tr>
  <tr>
    <td><p>tmp_table_size</p></td>
    <td><p>16M</p></td>
    <td><p>256M</p></td>
    <td><p>512M</p></td>
  </tr>
  <tr>
    <td><p>max_heap_table_size</p></td>
    <td><p>16M</p></td>
    <td><p>256M</p></td>
    <td><p>512M</p></td>
  </tr>
  <tr>
    <td><p>innodb_thread_concurrency</p></td>
    <td><p>0</p></td>
    <td><p>0</p></td>
    <td><p>32</p></td>
  </tr>
  <tr>
    <td><p>innodb_read_io_threads</p></td>
    <td><p>4</p></td>
    <td><p>8</p></td>
    <td><p>16</p></td>
  </tr>
  <tr>
    <td><p>innodb_write_io_threads</p></td>
    <td><p>4</p></td>
    <td><p>8</p></td>
    <td><p>16</p></td>
  </tr>
  <tr>
    <td><p>query_cache_size</p></td>
    <td><p>0</p></td>
    <td><p>256M</p></td>
    <td><p>❌ 8.0 已移除该参数</p></td>
  </tr>
  <tr>
    <td><p>innodb_adaptive_hash_index</p></td>
    <td><p>ON</p></td>
    <td><p>OFF</p></td>
    <td><p>ON（建议开启）</p></td>
  </tr>
</table>

# 故障时间线

<table>
  <tr>
    <th><p>时间线（隐藏清理中）</p></th>
  </tr>
  <tr>
    <td><p>隐藏行 2</p></td>
  </tr>
  <tr>
    <td><p>隐藏行 3</p></td>
  </tr>
  <tr>
    <td><p>隐藏行 4</p></td>
  </tr>
  <tr>
    <td><p>隐藏行 5</p></td>
  </tr>
</table>