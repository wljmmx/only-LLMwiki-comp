# OpsKG 时序与功能逻辑深度分析报告

> 基于完整代码调用链追踪，覆盖 12 大核心流程的时序分析、瓶颈识别和优化建议。

---

## 一、文档摄入流水线（Document Ingestion Pipeline）

### 1.1 完整时序图

```
POST /llm-wiki/recompile/{docId}/stream
  │
  ├─[1] DocumentStore.get(docId)           ─── SQLite 读 (1次)
  ├─[2] DocumentStore.read_content(docId)  ─── 文件系统读 (1次)
  │
  ├─[3] STEP_START: "parse"
  │   ├─ parser.parse(stored_path, docId)  ─── 解析器 (同步)
  │   │   └─ 返回 ParsedDocument (elements + heading_tree)
  │   └─ STEP_DONE: "parse"
  │
  ├─[4] STEP_START: "extract"  
  │   ├─ extractor.extract(doc)             ─── LLM 调用 (1次)
  │   │   ├─ 提取 EntityType 实体
  │   │   └─ 返回 list[ExtractedEntity]
  │   └─ STEP_DONE: "extract"
  │
  ├─[5] STEP_START: "compile"
  │   ├─ for each entity (串行):
  │   │   ├─ PAGE_START: {entity, index, total}
  │   │   ├─ _compile_entity_page(entity, doc, source)
  │   │   │   ├─ make_slug(entity)           ─── 内存计算
  │   │   │   ├─ slug 冲突检测               ─── VersionControl 读 (1次/entity)
  │   │   │   ├─ _llm_write_body(entity)     ─── LLM 调用 (1次/entity)
  │   │   │   │   └─ [LLM Cache] SHA256 去重检查
  │   │   │   ├─ _save_page(page)            ─── VersionControl 写 (1次/entity)
  │   │   │   ├─ _sync_page_to_graph(page)   ─── Neo4j 写 (1次/entity)
  │   │   │   └─ _validate_page_quality()    ─── 内存计算
  │   │   └─ PAGE_DONE: {entity, index, outcome}
  │   └─ STEP_DONE: "compile"
  │
  ├─[6] STEP_START: "struct_compile"
  │   ├─ _compile_heading_tree_to_wiki(doc, tree)
  │   │   └─ for each section node (递归):
  │   │       ├─ SECTION_START: {slug, title, level, index, total}
  │   │       ├─ _llm_compile_section(content) ─── LLM 调用 (1次/section)
  │   │       │   └─ [LLM Cache] SHA256 去重检查
  │   │       ├─ _save_page(page)             ─── VersionControl 写 (1次/section)
  │   │       ├─ _sync_page_to_graph(page)    ─── Neo4j 写 (1次/section)
  │   │       └─ SECTION_DONE: {slug, outcome, chars, time}
  │   └─ STEP_DONE: "struct_compile"
  │
  ├─[7] STEP_START: "index"
  │   ├─ wiki_index.rebuild_index()         ─── 全量重建 (O(n) 页面)
  │   └─ STEP_DONE: "index"
  │
  └─[8] SSE: "done" {pipeline_trace, pages_created, pages_updated}

关键 IO 统计 (N 实体 + M 章节):
  SQLite 读: 1 + N (slug 冲突检测) + N (save_page) + M (save_page)
  LLM 调用: 1 (extract) + N (compile) + M (struct_compile)
  文件系统: 1 (read_content)
  Neo4j 写: N + M
```

### 1.2 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| F1 | **实体编译串行执行** | 高 | `_compile_entities_to_wiki` | 每个实体依次 LLM 调用，N 个实体 = N 倍延迟。无并行化 |
| F2 | **每个实体都做 slug 冲突检测** | 中 | `_compile_entity_page` | 每次调用 `VersionControl.get_latest("wiki:{slug}")`，产生 N 次 SQLite 读 |
| F3 | **每个实体/章节都同步图谱** | 中 | `_sync_page_to_graph` | N+M 次 Neo4j 写操作，高频写入 |
| F4 | **struct_compile 和 compile 产生重复页面** | 高 | 步骤 5 + 步骤 6 | 实体编译和结构编译可能为同一概念生成页面，slug 冲突检测依赖 `force` 参数 |
| F5 | **索引重建是全量 O(n)** | 中 | `rebuild_index` | 每次编译重建全部索引，未做增量更新 |
| F6 | **LLM 缓存仅单次编译有效** | 中 | `_llm_cache` | 相同文档重复编译（如不同 force 参数）无法复用缓存 |

### 1.3 优化建议

| 优先级 | 建议 |
|--------|------|
| P0 | `_compile_entities_to_wiki` 并行化：使用 `asyncio.gather` 并发执行实体编译（限制并发数 = 3） |
| P0 | 合并 slug 冲突检测：编译前一次性查询所有相关 slug，减少 N 次 SQLite 读 |
| P1 | 延迟图谱同步：编译完成后批量写入，减少 N+M 次 Neo4j 往返 |
| P1 | 章节去重：struct_compile 前检查 entity compile 已生成的 slug，跳过重复 |
| P2 | 索引增量更新：仅更新变更页面，而非全量重建 |
| P2 | 跨编译周期的 LLM 缓存：基于 content hash 持久化缓存（如 SQLite 表） |

---

## 二、Wiki 查询与检索（Wiki Query）

### 2.1 完整时序图

```
POST /wiki/query
  │
  ├─[1] 分词：tokenize(question)           ─── 内存 (正则 + 停用词)
  │
  ├─[2] 关键词召回（第一层）
  │   ├─ list_wiki_pages()                 ─── SQLite 读 (全表扫描)
  │   │   └─ 返回所有页面 {slug, title, type, body, tags}
  │   ├─ 对每个页面: 内存关键词匹配
  │   │   └─ 计算 BM25 分数
  │   └─ 返回 top-K 页面
  │
  ├─[3] 向量召回（第二层，可选）
  │   ├─ embed_query(question)             ─── LLM Embedding API (1次)
  │   ├─ for each page:
  │   │   ├─ embed_texts(page.body)        ─── LLM Embedding API (N次)
  │   │   │   └─ [Embedding Cache] version 检查
  │   │   └─ cosine_similarity             ─── 内存 (numpy)
  │   └─ 返回 top-K 页面
  │
  ├─[4] Backlink 扩展（第三层）
  │   ├─ for each recalled slug:
  │   │   └─ get_backlinks(slug)           ─── SQLite 读 (1次/slug)
  │   └─ 合并页面
  │
  ├─[5] 类型路由过滤
  │   ├─ 故障类问题 → 优先 incident/runbook
  │   └─ 概念类问题 → 优先 concept
  │
  ├─[6] LLM 回答生成
  │   ├─ 构建 prompt (question + 召回页面内容)
  │   ├─ LLM 调用                           ─── LLM Chat API (1次)
  │   └─ 解析引用 [[slug]]
  │
  └─[7] 知识复利回写（可选）
      └─ 新事实 → 回写到 wiki 页面
```

### 2.2 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| Q1 | **list_wiki_pages 全表扫描** | 高 | `wiki_query.py` 召回阶段 | 页面数 > 1000 时每次查询 1000+ 次 SQLite 行读取 |
| Q2 | **向量检索需要 N 次 embedding API** | 高 | 向量召回 | 每个页面独立调用 embed API，页面数 > 100 时极慢 |
| Q3 | **embedding 缓存仅版本号失效** | 中 | `_wiki_emb_cache` | 页面内容不变但版本号变化（如 metadata 更新）时缓存失效 |
| Q4 | **无查询结果缓存** | 中 | 整个查询流程 | 相同问题重复查询需完整执行全流程 |
| Q5 | **Backlink 扩展逐个查询** | 低 | `get_backlinks` | 每个 slug 一次 SQLite 查询，可合并为批量查询 |
| Q6 | **分词器过于简陋** | 低 | `tokenize` | 中文仅按 CJK 字符分割，无语义分词 |

### 2.3 优化建议

| 优先级 | 建议 |
|--------|------|
| P0 | 向量检索改为批量 embedding：一次性 embed 所有页面 body（或预计算+存储 embedding） |
| P0 | 添加查询结果缓存：TTL 5 分钟，基于 question hash |
| P1 | list_wiki_pages 添加过滤条件（按 type、tags），避免全表扫描 |
| P1 | embedding 缓存改为 content hash 失效，而非 version 号 |
| P2 | 引入 jieba 分词器提升中文分词精度 |
| P2 | Backlink 批量查询：一次 SQL 查询所有 backlink |

---

## 三、Wiki Lint 健康检查（Wiki Lint）

### 3.1 完整时序图

```
GET /wiki/lint
  │
  ├─[1] 加载所有 wiki 页面
  │   └─ list_wiki_pages()                 ─── SQLite 全表扫描 (1次)
  │
  ├─[2] 矛盾检测 (regex)
  │   ├─ for each page pair:
  │   │   ├─ 提取数值模式 (端口、超时、阈值等)
  │   │   └─ regex 匹配冲突
  │   └─ 时间复杂度: O(n²) 页面对
  │
  ├─[3] 矛盾检测 (semantic) ── 可选，LLM 驱动
  │   ├─ _collect_semantic_candidate_pairs()
  │   │   └─ 同类型页面分组 → 候选对
  │   └─ _llm_detect_conflicts()           ─── LLM 调用 (1次/候选对)
  │
  ├─[4] Stale 检测
  │   ├─ list_stale_pages()                ─── SQLite 查询 (1次)
  │   │   └─ wiki.updated_at < raw.updated_at
  │   └─ 时间复杂度: O(n)
  │
  ├─[5] Orphan 检测
  │   ├─ get_orphan_slugs()                ─── SQLite 查询 (1次)
  │   │   └─ backlink 为空
  │   └─ 时间复杂度: O(n)
  │
  ├─[6] Deadlink 检测
  │   ├─ get_all_deadlinks()               ─── SQLite 查询 (1次)
  │   │   └─ [[slug]] 中的 slug 不存在
  │   └─ 时间复杂度: O(n * avg_outlinks)
  │
  ├─[7] Missing Concept 检测
  │   ├─ 图谱实体无 wiki 页面
  │   └─ Neo4j 查询 (1次)
  │
  └─[8] 构建 LintReport → 推送 ReviewQueue
```

### 3.2 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| L1 | **矛盾检测 O(n²) 复杂度** | 高 | `_check_contradictions` | 100 页面 = 4950 对，500 页面 = 124750 对 |
| L2 | **语义矛盾检测逐对调用 LLM** | 高 | `_llm_detect_conflicts` | 每对候选页面一次 LLM 调用，候选对多时极慢 |
| L3 | **全量执行无增量** | 中 | `run_all_lints` | 每次 lint 检查所有页面，未做增量（仅检查变更页面） |
| L4 | **list_wiki_pages 重复调用** | 中 | 步骤 1 | 每个检测类型独立调用 `list_wiki_pages`，重复加载 |
| L5 | **Lint 结果无缓存** | 低 | 整个流程 | 相同状态的 wiki 重复 lint 产生相同结果 |

### 3.3 优化建议

| 优先级 | 建议 |
|--------|------|
| P0 | 矛盾检测优化：仅比较同类型页面（concept vs concept），按类型分组减少候选对 |
| P0 | 语义矛盾检测改为批量：一次 LLM 调用传入多对候选，而非逐对调用 |
| P1 | 增量 lint：仅检查上次 lint 后变更的页面 |
| P1 | 一次性加载所有页面，各检测类型共享，避免重复 SQLite 查询 |
| P2 | Lint 结果缓存：基于页面版本号集合的 hash，相同状态直接返回缓存 |

---

## 四、Wiki Drift 漂移检测（Wiki Drift）

### 4.1 完整时序图

```
触发：事件驱动 / 定时轮询 / 手动触发
  │
  ├─[1] 扫描所有 wiki 页面
  │   ├─ list_wiki_pages()                 ─── SQLite 全表扫描
  │   └─ for each page:
  │       ├─ 获取 sources (raw 文档引用)
  │       ├─ 检查 raw 文档 checksum 是否变化
  │       └─ 标记 stale
  │
  ├─[2] 收集 stale 页面
  │   └─ list_stale_pages()               ─── SQLite 查询
  │
  ├─[3] 触发重编译
  │   └─ for each stale page:
  │       └─ wiki_compiler.recompile(source_doc_id)
  │
  └─[4] 更新 wiki 页面 updated_at
```

### 4.2 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| D1 | **无自动触发机制** | 高 | 整个流程 | 漂移检测依赖手动触发或 cron，无事件驱动（如 raw 文档更新事件） |
| D2 | **checksum 变化触发全量重编译** | 中 | 步骤 3 | raw 文档更新后整个文档重编译，而非仅变更段落 |
| D3 | **无 webhook 通知** | 低 | 步骤 3 | 重编译完成后无通知机制 |

### 4.3 优化建议

| 优先级 | 建议 |
|--------|------|
| P0 | 文档上传/更新后自动触发相关 wiki 页面的漂移检测 |
| P1 | 增量编译：仅重编译 raw 文档变更部分影响的章节 |
| P2 | 重编译完成后 webhook 通知（或 SSE 推送） |

---

## 五、认证授权流程（Authentication）

### 5.1 完整时序图

```
请求到达
  │
  ├─[Middleware] AuditLogMiddleware
  │   └─ 记录请求信息 (读操作可选)
  │
  ├─[Middleware] RateLimitMiddleware (slowapi)
  │   └─ 检查请求频率
  │
  ├─[Dependency] verify_token()
  │   ├─ 1. 检查 OPSKG_API_TOKEN 配置
  │   │   ├─ 未配置 + 无凭证 → "anonymous" (开发模式)
  │   │   └─ 已配置 → 进入验证
  │   ├─ 2. Legacy Token 验证
  │   │   └─ token == OPSKG_API_TOKEN → "user"
  │   ├─ 3. Session Token 验证
  │   │   ├─ auth_store.verify_session(token)
  │   │   │   └─ SQLite 查询 (1次)
  │   │   └─ 返回 "user:{username}"
  │   └─ 4. 注入 request.state.user
  │
  ├─[Router] 端点处理
  │
  └─[Response] 安全响应头
      ├─ Content-Security-Policy
      ├─ X-Content-Type-Options
      └─ X-Frame-Options
```

### 5.2 OIDC 认证流程

```
GET /auth/oidc/login
  │
  ├─ 生成 state + nonce
  ├─ 重定向到 IdP 授权页面
  │
  ▼
GET /auth/oidc/callback?code=xxx&state=xxx
  │
  ├─ 验证 state (防 CSRF)
  ├─ code → token 交换                   ─── IdP API (1次)
  ├─ 获取用户信息                         ─── IdP API (1次)
  ├─ 本地用户查找/创建                    ─── SQLite (1次)
  └─ 签发 session token → 重定向到前端
```

### 5.3 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| A1 | **Legacy Token 无过期机制** | 高 | `verify_token_string` | 静态 API Token 泄露后无法撤销，需重启服务 |
| A2 | **Session Token 无过期刷新** | 中 | `auth_store.verify_session` | 无 refresh token 机制，session 过期后需重新登录 |
| A3 | **开发模式无警告** | 中 | `verify_token` | 未配置 API_TOKEN 时默认放行，生产环境风险 |
| A4 | **OIDC state 无过期清理** | 低 | `oidc.py` | state 参数存储在内存中，服务重启后丢失 |
| A5 | **审计日志仅记录写操作** | 低 | `AuditLogMiddleware` | 读操作无审计，无法追踪信息泄露 |

### 5.4 优化建议

| 优先级 | 建议 |
|--------|------|
| P0 | API Token 改为 JWT 格式，支持过期时间和撤销列表 |
| P1 | 添加 refresh token 机制，延长 session 有效期 |
| P1 | 生产环境检测：未配置 API_TOKEN 时打印 WARNING 日志 |
| P2 | OIDC state 持久化到 SQLite，支持跨重启 |
| P2 | 审计日志可配置记录读操作 |

---

## 六、前端路由导航与数据加载（Frontend Navigation）

### 6.1 完整时序图

```
页面加载 / URL 变更
  │
  ├─[Router] beforeEach 守卫
  │   ├─ 检查 token 是否存在
  │   │   ├─ 无 token → 白名单路由 (login/oidc) → 放行
  │   │   └─ 无 token → 其他路由 → 跳转 /login
  │   ├─ 检查 token 是否过期
  │   │   └─ 过期 → 清除 token → 跳转 /login
  │   └─ 放行
  │
  ├─[View] onMounted / watch
  │   ├─ 并行 API 调用 (无去重保护)
  │   │   ├─ GET /api/v1/dashboard/stats
  │   │   ├─ GET /api/v1/documents?limit=5
  │   │   ├─ GET /api/v1/wiki/index
  │   │   └─ GET /api/v1/review/stats
  │   └─ 手动管理 loading/error/data 三态
  │
  ├─[API Layer] axios 拦截器
  │   ├─ 请求拦截：添加 Authorization header
  │   ├─ [New] 请求去重：相同请求共享 Promise
  │   ├─ 响应拦截：处理 401 → 跳转 login
  │   └─ [New] 指数退避重试：5xx 自动重试 2 次
  │
  └─[Store] auth store 状态更新
      └─ 触发视图响应式更新
```

### 6.2 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| N1 | **Dashboard 4 个 API 并发无协调** | 中 | `DashboardView.onMounted` | 4 个 API 独立 loading，用户看到多个 spinner |
| N2 | **Token 过期时多个 API 同时 401** | 中 | 路由守卫 + API 拦截器 | 多个 API 同时返回 401，每个都触发跳转登录（竞态） |
| N3 | **组件卸载后 API 回调仍执行** | 中 | 各 View 组件 | 快速切换页面时，前一个页面的 API 回调可能更新已卸载组件 |
| N4 | **无全局 loading 状态** | 低 | 顶层 | 无法知道是否有 API 请求正在进行中 |
| N5 | **路由守卫不检查 OIDC 状态** | 低 | `beforeEach` | OIDC 回调后 token 可能未及时写入 store |

### 6.3 优化建议

| 优先级 | 建议 |
|--------|------|
| P0 | 401 处理添加去重锁：首个 401 处理中，后续 401 静默忽略 |
| P1 | Dashboard 使用 `useAsyncData` 调用，统一 loading 状态 |
| P1 | useAsyncData 添加组件卸载时自动取消请求（AbortController） |
| P2 | 添加全局 loading bar（NProgress 风格） |
| P2 | 路由守卫等待 OIDC 回调完成后再检查 token |

---

## 七、审查队列（Review Queue）

### 7.1 完整时序图

```
Lint 检测完成 → 创建 ReviewItem
  │
  ├─[1] compute_issue_key(type, slug, message)
  │   └─ SHA1 哈希 → 幂等标识
  │
  ├─[2] 检查是否已存在
  │   └─ SQLite 查询 (1次)
  │
  ├─[3] 创建/更新 ReviewItem
  │   └─ SQLite 写入 (1次)
  │
  ▼
GET /review/queue?status=pending&page=1&page_size=20
  │
  ├─ 查询 pending items                   ─── SQLite (1次)
  └─ 返回 PaginatedResponse
  │
  ▼
POST /review/queue/{id}/approve
  │
  ├─ 更新状态 → "approved"                ─── SQLite (1次)
  └─ 可选：自动修复（如修复死链）
  │
  ▼
POST /review/queue/{id}/reject
  │
  ├─ 更新状态 → "ignored"                 ─── SQLite (1次)
  └─ 添加到忽略表
```

### 7.2 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| R1 | **无自动修复能力** | 中 | approve/reject | 审批通过后需要人工修复，无法自动执行（如修复死链） |
| R2 | **忽略表无过期机制** | 低 | 忽略表 | 一次忽略永久忽略，即使后续 lint 结果变化 |
| R3 | **无批量操作** | 低 | 端点 | 无批量 approve/reject 端点 |

### 7.3 优化建议

| 优先级 | 建议 |
|--------|------|
| P1 | approve 后自动修复：死链 → 创建页面，orphan → 建立链接 |
| P2 | 忽略表添加 TTL：30 天后自动重新评估 |
| P2 | 添加批量 approve/reject 端点 |

---

## 八、版本控制（Version Control）

### 8.1 完整时序图

```
_save_page(page)
  │
  ├─[1] 序列化页面内容 → YAML frontmatter + Markdown body
  │
  ├─[2] BEGIN IMMEDIATE TRANSACTION
  │   ├─ 获取当前最大版本号              ─── SQLite 查询
  │   ├─ 新版本号 = max_version + 1
  │   ├─ 计算 content_hash (SHA256)
  │   ├─ 检查是否与最新版本相同
  │   │   ├─ 相同 → 跳过 (幂等)
  │   │   └─ 不同 → 插入新版本          ─── SQLite 写入
  │   └─ COMMIT
  │
  ├─[3] get_version(doc_key, version)
  │   └─ SQLite 查询 (1次)
  │
  ├─[4] diff_versions(doc_key, v1, v2)
  │   ├─ 获取两个版本内容
  │   └─ 逐行 diff
  │
  ├─[5] rollback(doc_key, target_version)
  │   ├─ 获取目标版本内容
  │   └─ 创建新版本 (内容 = 目标版本)
  │
  └─[6] delete_all(doc_key)
      └─ SQLite 删除
```

### 8.2 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| V1 | **无版本数量限制** | 中 | `save_version` | 频繁编译会无限增长版本历史，存储膨胀 |
| V2 | **版本内容全量存储** | 低 | 序列化 | 每次保存完整页面内容，无增量存储 |
| V3 | **无版本标签/快照** | 低 | 数据模型 | 无法标记重要版本（如"发布版本"） |

### 8.3 优化建议

| 优先级 | 建议 |
|--------|------|
| P1 | 添加版本保留策略：每个 doc_key 最多保留 50 个版本 |
| P2 | 增量存储：仅存储与上一版本的 diff |
| P2 | 支持版本标签（如 "v1.0"、"published"） |

---

## 九、Runbook 生成（Runbook Generation）

### 9.1 完整时序图

```
POST /runbook/generate
  │
  ├─[1] 模板引擎生成
  │   ├─ 加载 Runbook 模板
  │   ├─ 插入故障场景参数
  │   └─ 返回 Markdown 文本
  │
  ▼
POST /runbook/generate-llm
  │
  ├─[1] 从知识图谱检索上下文
  │   ├─ graph_store.search_entities(scenario) ─── Neo4j (1次)
  │   └─ 返回相关实体/概念
  │
  ├─[2] 从 wiki 检索相关页面
  │   ├─ wiki_query 关键词召回
  │   └─ 返回相关的 incident/runbook 页面
  │
  ├─[3] LLM 编译 Runbook
  │   ├─ 构建 prompt (模板 + 上下文 + wiki 页面)
  │   ├─ LLM 调用                              ─── LLM API (1次)
  │   └─ 返回 Runbook Markdown
  │
  └─[4] 发布为 Wiki 页面
      └─ _save_page(runbook_page)
```

### 9.2 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| RB1 | **模板引擎和 LLM 编译分离** | 中 | 两个端点 | `/runbook/generate` 和 `/runbook/generate-llm` 是两个独立端点，无统一入口 |
| RB2 | **无 Runbook 质量评估** | 中 | 步骤 3 | LLM 生成的 Runbook 无自动质量校验 |
| RB3 | **无 Runbook 版本管理** | 低 | 步骤 4 | 无 Runbook 的变更历史追踪 |

### 9.3 优化建议

| 优先级 | 建议 |
|--------|------|
| P1 | 统一 Runbook 生成端点：自动选择模板还是 LLM |
| P2 | 添加 Runbook 质量校验：必含章节检查（概述/影响/排查/处置） |
| P2 | Runbook 发布时自动创建版本记录 |

---

## 十、OKF 导入导出（OKF Import/Export）

### 10.1 完整时序图

```
GET /api/okf/export
  │
  ├─[1] 加载所有 wiki 页面
  │   └─ list_wiki_pages()                 ─── SQLite 全表扫描
  │
  ├─[2] 转换格式
  │   ├─ [[wikilink]] → [display](/{type}/{slug}.md)
  │   ├─ YAML frontmatter 映射
  │   └─ 创建目录结构
  │
  ├─[3] 打包 tarball
  │   └─ tar.gz 压缩
  │
  └─[4] 返回 tarball 文件

POST /api/okf/import
  │
  ├─[1] 解压 tarball
  ├─[2] 验证 OKF 合规性
  │   ├─ YAML frontmatter 可解析
  │   ├─ type 非空
  │   └─ index.md / log.md 存在
  ├─[3] 转换格式
  │   └─ [display](/{type}/{slug}.md) → [[wikilink]]
  └─[4] 导入 wiki 页面
      └─ for each page: _save_page()
```

### 10.2 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| O1 | **导出时无增量导出** | 中 | 步骤 1 | 每次都导出全部页面，无增量（仅导出变更页面） |
| O2 | **导入时无冲突处理** | 中 | 步骤 4 | 导入页面与现有页面 slug 冲突时直接覆盖，无合并策略 |
| O3 | **无导入预览** | 低 | 步骤 1 | 导入前无法预览哪些页面会被创建/更新/冲突 |

### 10.3 优化建议

| 优先级 | 建议 |
|--------|------|
| P1 | 增量导出：仅导出上次导出后变更的页面 |
| P1 | 导入冲突处理：冲突时创建新版本而非覆盖，标记 review_needed |
| P2 | 导入前返回预览摘要（created/updated/conflict 数量） |

---

## 十一、图谱同步（Graph Sync）

### 11.1 完整时序图

```
_sync_page_to_graph(page)
  │
  ├─[1] 构建 GraphEntity
  │   ├─ entity_type = page.type 映射
  │   ├─ name = page.title
  │   └─ properties = {slug, tags, review_status, ...}
  │
  ├─[2] graph_store.upsert_entity(entity)
  │   ├─ [缓存检查] 读取缓存
  │   │   └─ 命中且未过期 → 返回
  │   ├─ MERGE (e:EntityType {name})      ─── Neo4j Cypher (1次)
  │   ├─ SET e += properties
  │   ├─ [缓存更新] 写入缓存
  │   └─ [缓存失效] 使相关查询缓存失效
  │
  └─[3] 关系创建（可选）
      └─ for each outlink in page.wikilinks:
          └─ graph_store.create_relationship()
```

### 11.2 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| G1 | **Neo4j 不可用时静默失败** | 高 | `graph_store` | 所有 Neo4j 操作包裹在 try/except 中，图谱数据可能不一致 |
| G2 | **缓存失效策略过于激进** | 中 | `_invalidate_cache` | 单次 upsert 使所有相关查询缓存失效，降低缓存命中率 |
| G3 | **无批量写入优化** | 中 | `upsert_entity` | 编译时逐个实体写入，应使用 `UNWIND` 批量操作 |
| G4 | **无图谱数据一致性检查** | 低 | 全局 | 无定期检查 wiki 页面与图谱实体的一致性 |

### 11.3 优化建议

| 优先级 | 建议 |
|--------|------|
| P0 | Neo4j 不可用时记录失败日志并告警（而非静默） |
| P1 | 编译时使用批量写入：`UNWIND $entities AS e MERGE ...` |
| P1 | 缓存失效策略优化：仅失效被修改实体的直接相关缓存 |
| P2 | 添加定期一致性检查：对比 wiki 页面与图谱实体数量 |

---

## 十二、SSE 实时编译与前端交互（SSE Pipeline Interaction）

### 12.1 完整时序图

```
[前端] subscribe("/llm-wiki/recompile/{docId}/stream?force=true")
  │
  ├─ fetch(SSE_URL, {headers: {Authorization}})
  │   └─ [New] autoReconnect 支持
  │
  ▼
[后端] recompile_stream 端点
  │
  ├─ 创建 asyncio.Queue 事件队列
  ├─ 启动后台任务: _recompile_async(docId, queue, ...)
  │   │
  │   ├─ on_progress 回调 → queue.put(event)
  │   │   ├─ step_start: "parse"
  │   │   ├─ step_done: "parse"
  │   │   ├─ step_start: "extract"
  │   │   ├─ step_done: "extract"
  │   │   ├─ step_start: "compile"
  │   │   ├─ page_start: {entity, index, total}
  │   │   ├─ page_done: {entity, index, outcome}
  │   │   ├─ ... (N 次)
  │   │   ├─ step_done: "compile"
  │   │   ├─ step_start: "struct_compile"
  │   │   ├─ section_start: {slug, title, level, index, total}
  │   │   ├─ section_done: {slug, outcome, chars, time}
  │   │   ├─ ... (M 次)
  │   │   ├─ step_done: "struct_compile"
  │   │   ├─ step_start: "index"
  │   │   ├─ step_done: "index"
  │   │   └─ done: {pipeline_trace, pages_created, ...}
  │   │
  │   └─ queue.put(None)  # 结束信号
  │
  ├─ StreamingResponse 循环
  │   └─ while True:
  │       ├─ event = await queue.get()
  │       ├─ if event is None → break
  │       └─ yield f"data: {json}\n\n"
  │
  ▼
[前端] useSse onEvent 回调
  │
  ├─ section_start → sectionNodes.push({slug, title, status:"running"})
  ├─ section_done → sectionNodes[slug].status = "done"
  ├─ done → compileResult = data, loadTraceData(docId)
  └─ error → message.error()
```

### 12.2 关键问题

| # | 问题 | 严重度 | 调用链位置 | 影响 |
|---|------|--------|-----------|------|
| S1 | **SSE 事件无顺序保证** | 中 | `asyncio.Queue` | 使用 FIFO 队列，但 `on_progress` 回调在异步上下文中，理论上可能乱序 |
| S2 | **客户端断开后服务端继续编译** | 中 | `recompile_stream` | 用户关闭页面后编译任务仍在运行，浪费资源 |
| S3 | **无事件丢失检测** | 低 | 两端 | 无 sequence number，无法检测事件是否丢失 |
| S4 | **PipelineView 完成时两次加载 traceData** | 低 | `done` 事件 + `loadTraceData` | `done` 事件包含 `pipeline_trace` 但前端又调用 `getCompileTrace` API |
| S5 | **快速切换文档时竞态** | 低 | `subscribe` 多次调用 | 前一个编译的 SSE 事件可能更新到后一个文档的视图 |

### 12.3 优化建议

| 优先级 | 建议 |
|--------|------|
| P0 | 客户端断开时取消后台编译任务（通过 `is_cancelled` 回调） |
| P1 | 添加事件 sequence number，前端检测丢失 |
| P1 | 消除重复 traceData 加载：`done` 事件包含 pipeline_trace 时跳过 `loadTraceData` |
| P2 | 添加文档 ID 校验：SSE 事件中的 docId 与当前视图的 docId 不匹配时忽略 |

---

## 十三、优先级汇总

### P0（立即修复 — 影响功能正确性或性能严重劣化）

| # | 问题 | 模块 |
|---|------|------|
| 1 | 实体编译串行 → 并行化 (asyncio.gather) | 文档摄入 |
| 2 | 向量检索 N 次 embedding API → 批量 embedding | Wiki 查询 |
| 3 | Neo4j 不可用静默失败 → 告警 | 图谱同步 |
| 4 | 客户端断开后继续编译 → 取消任务 | SSE 交互 |
| 5 | 矛盾检测 O(n²) → 按类型分组 | Wiki Lint |
| 6 | 语义矛盾逐对 LLM → 批量调用 | Wiki Lint |
| 7 | 401 竞态 → 去重锁 | 前端路由 |
| 8 | 文档上传后无自动漂移检测 | Wiki Drift |

### P1（近期优化 — 提升性能与用户体验）

| # | 问题 | 模块 |
|---|------|------|
| 1 | 每个实体 slug 冲突检测 → 合并查询 | 文档摄入 |
| 2 | 图谱逐个写入 → 批量 UNWIND | 图谱同步 |
| 3 | 索引全量重建 → 增量更新 | 文档摄入 |
| 4 | 查询结果无缓存 → TTL 5min | Wiki 查询 |
| 5 | 增量 lint → 仅检查变更页面 | Wiki Lint |
| 6 | 页面加载时一次性获取所有页面 | Wiki Lint |
| 7 | API Token → JWT 格式 | 认证 |
| 8 | 添加 refresh token | 认证 |
| 9 | 组件卸载时自动取消请求 | 前端 |
| 10 | 消除重复 traceData 加载 | SSE 交互 |
| 11 | 增量编译（仅变更段落） | 文档摄入 |
| 12 | 导入冲突处理 | OKF |

### P2（持续改进 — 增强健壮性与可维护性）

| # | 问题 | 模块 |
|---|------|------|
| 1 | 跨编译周期 LLM 缓存持久化 | 文档摄入 |
| 2 | 版本数量限制 + 增量存储 | 版本控制 |
| 3 | 事件 sequence number | SSE 交互 |
| 4 | 导入预览 | OKF |
| 5 | 忽略表 TTL | 审查队列 |
| 6 | 批量 approve/reject | 审查队列 |
| 7 | 分词器升级 (jieba) | Wiki 查询 |
| 8 | OIDC state 持久化 | 认证 |
| 9 | 审计日志可配置记录读操作 | 认证 |
| 10 | 全局 loading bar | 前端 |

---

## 附录：流程健康度评分

| 流程 | 完整性 | 性能 | 健壮性 | 可观测性 | 综合 |
|------|--------|------|--------|---------|------|
| 文档摄入 | 85% | 65% | 70% | 80% | **75%** |
| Wiki 查询 | 80% | 55% | 75% | 70% | **70%** |
| Wiki Lint | 85% | 50% | 80% | 75% | **72%** |
| Wiki Drift | 70% | 80% | 75% | 70% | **74%** |
| 认证授权 | 80% | 85% | 70% | 75% | **77%** |
| 前端路由 | 75% | 75% | 65% | 70% | **71%** |
| 审查队列 | 80% | 85% | 75% | 70% | **77%** |
| 版本控制 | 85% | 80% | 85% | 75% | **81%** |
| Runbook | 70% | 80% | 75% | 70% | **74%** |
| OKF | 80% | 80% | 75% | 75% | **77%** |
| 图谱同步 | 75% | 60% | 60% | 65% | **65%** |
| SSE 交互 | 80% | 75% | 65% | 70% | **72%** |
| **整体平均** | **79%** | **72%** | **72%** | **72%** | **74%** |