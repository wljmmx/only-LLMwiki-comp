# Nginx 性能调优实战手册

版本 3.2 | 最后更新：2026-06-28 | 作者：基础架构组

**⚠️ 警告：**以下配置适用于 Nginx 1.24+，*不支持* 1.22 及以下版本。

生产环境修改前请务必在 staging 环境验证。

## 一、核心参数优化

| 参数 | 默认值 | 推荐值 | 说明 |
| --- | --- | --- | --- |
| `worker_processes` | 1 | auto | 自动匹配 CPU 核心数 |
| `worker_connections` | 512 | 4096 | 单 worker 最大连接数 |
| `keepalive_timeout` | 75s | 65s | 过短增加握手开销，过长占用连接 |
| `proxy_buffer_size` | 4k/8k | 16k | ⚠️ **生产环境必须调大**，否则大响应头被截断 |

## 二、完整配置示例

```
# Nginx 生产配置
upstream backend {
    server 10.0.0.1:8080 weight=5 max_fails=3;
    server 10.0.0.2:8080 weight=3 # 备机;
    keepalive 32;
}

server {
    listen 80;
    server_name api.example.com;
    # 安全头
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;

    location / {
        proxy_pass http://backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffer_size 16k;
        proxy_buffers 4 64k;
        proxy_busy_buffers_size 128k;
    }
}
```

## 三、常见问题

* **502 Bad Gateway**：检查后端服务是否存活，`proxy_connect_timeout` 是否过短
* **504 Gateway Timeout**：后端处理时间过长，增大 `proxy_read_timeout`
* **413 Request Entity Too Large**：文件上传超过 `client_max_body_size` 限制
* ⚠️ 特别注意：`proxy_set_header Host $host;` 必须配置，否则后端无法识别域名

## 四、性能基准测试（ab 压测）

| 并发数 | QPS | P99延迟 | CPU |
| --- | --- | --- | --- |
| 100 | 12,450 | 45ms | 35% |
| 500 | 11,200 | 120ms | 62% |
| 1000 | 9,800 | 380ms | 88% |
| 2000 | 5,200 | 1,250ms | 99% |

参考资料：[Nginx 官方文档](https://nginx.org/en/docs/) | [Nginx Blog](https://www.nginx.com/blog/)

⚠️ 本文档仅供内部使用，禁止外传