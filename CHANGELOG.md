# OpsKG Changelog

本文件记录 OpsKG 所有用户可见的 API 变更、功能更新和破坏性改动。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [v0.1.0] — 2026-07-16

### 新增
- 知识编译流水线：文档上传 → LLM 解析 → Wiki 编译 → 版本存储
- 6 种 Wiki 页面类型：entity / concept / incident / runbook / service / host
- `[[wikilink]]` 双向链接，自动维护反向索引
- Wiki 健康检查：矛盾检测、Stale 检测、Orphan 检测、死链检测、缺失概念检测
- OKF v0.1 互操作：导出/导入/校验
- SSE 实时编译进度推送
- 管道追踪（PipelineTraceView）：章节级编译详情
- 知识图谱可视化（VueFlow 交互式）
- 知识 Q&A（LLM 问答 + 答案溯源）
- 多语言支持（CJK 分词）
- 文档管理：上传、解析、列表、搜索
- 审查队列（ReviewQueue）：编译质量 > 阈值进入审核
- 知识复利回写：LLM 自校验防幻觉
- AIOps 模块：Incident 管理、变更关联、服务拓扑、Runbook 工作台
- MCP 工具审计：协议编解码、工具日志、权限控制
- Agent Loop MVP：多步推理循环
- Webhook 管理：重试、SSRF 防护
- 模板管理：页面模板 CRUD
- 导出中心：Markdown / JSON / OKF 导出
- 版本控制：Wiki 页面版本历史、diff 对比
- 仪表盘：首页概览统计

### 认证
- 用户名密码登录（bcrypt 哈希）
- OIDC SSO 多提供者（nonce 防重放）
- SAML 2.0 SSO（严格签名验证）
- LDAP/AD 企业目录集成
- API Token 认证
- 限流：登录 5次/分钟，API 60次/分钟
- 账户锁定、强制改密
- 审计日志（所有写操作）

### LLM
- 三后端支持：OpenAI 兼容 / Ollama / vLLM
- 弹性调用：指数退避重试、并发限流、Token Bucket 限流
- 降级链：主后端失败 → fallback 后端链
- 流式响应 + 取消（CancelToken）
- LLM 调用缓存（LRU + TTL）
- Token 用量 Prometheus 指标

### 安全
- CSP 安全头（Content-Security-Policy）
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- X-XSS-Protection
- Referrer-Policy
- SSRF 防护（URL 白名单 + DNS 二次检查）
- CORS 可配置白名单
- 非 root 容器运行
- 生产环境异常屏蔽

### 部署
- Docker 多阶段构建（单镜像交付）
- Docker Compose 一键部署（含 Neo4j）
- Nginx 反向代理 + API 版本化 passthrough
- 非 root 用户 + 非特权端口 8080
- HEALTHCHECK 探针
- 40 个 CI verify 脚本（ruff + pytest + vitest + vue-tsc）
- Grafana Dashboard 预设

### 变更
- API 路径统一加 `/api/v1` 前缀（`/api` 向后兼容）
- 前端默认 API base URL 改为 `/api/v1`

---

## API 兼容性说明

### 当前版本 (v1)
- 所有业务 API 路径：`/api/v1/*`
- 向后兼容路径：`/api/*`（与 v1 相同）
- 基础设施端点（auth/health/metrics）：不版本化

### 废弃策略
- API 版本将至少保留 2 个 minor 版本
- 废弃端点将在响应头中设置 `Deprecation: true`
- 废弃端点将在 1 个 major 版本后移除
- 引入破坏性变更时，将在大版本号中体现

### 当前废弃端点
_无_