# OpsKG 全系统时序与功能逻辑分析报告

> 生成日期：2026-07-18 | 基于完整代码审计，覆盖全部模块

---

## 目录

1. [系统架构总览](#一系统架构总览)
2. [存储层分析](#二存储层-storage-layer)
3. [知识编译引擎分析](#三知识编译引擎-knowledge-compiler)
4. [LLM 集成层分析](#四llm-集成层-corellm)
5. [解析与抽取引擎分析](#五解析与抽取引擎-parsers--extraction)
6. [知识查询引擎分析](#六知识查询引擎-wiki-query)
7. [API 路由层分析](#七api-路由层-routers)
8. [搜索引擎分析](#八搜索引擎-search)
9. [可观测性与 AIOps 分析](#九可观测性与-aiops)
10. [前端架构分析](#十前端架构)
11. [Docker 与部署分析](#十一docker-与部署)
12. [CI/CD 分析](#十二cicd)
13. [可优化列表汇总](#十三可优化列表汇总)

---

## 一、系统架构总览

### 1.1 项目规模

| 维度 | 数量 |
|------|------|
| 后端 Python 模块 | 28 个 package，100+ .py 文件 |
| 后端 API Router | 26 个（含 5 种 SSO 认证） |
| 前端 Vue 视图 | 22 个页面 |
| 前端 API 模块 | 20 个 .ts 文件 |
| 前端 Pinia Store | 4 个（app/auth/setup/onboarding） |
| 测试文件 | 53 个前端 + 52 个后端 |
| 验证脚本 | 40 个 verify_*.py |
| 配置项 | 70+ Pydantic Settings 字段 |
| CI Job | 4 个（backend/frontend/benchmark/docker） |

### 1.2 模块依赖拓扑

```
┌─────────────────────────────────────────────────────────────┐
│                       API Routers (26)                       │
│  llm_wiki_router ← 核心入口，调度所有知识引擎模块              │
└──────────┬──────────┬──────────┬──────────┬─────────────────┘
           │          │          │          │
    ┌──────▼──┐ ┌─────▼──┐ ┌────▼──┐ ┌────▼──────┐
    │Knowledge│ │Parsers │ │Search │ │Observability│
    │ Engine  │ │(8种)   │ │Engine │ │+ AIOps     │
    └────┬────┘ └───┬────┘ └───┬───┘ └─────┬──────┘
         │          │          │            │
    ┌────▼──────────▼──────────▼────────────▼──────┐
    │              Storage Layer                     │
    │  DocumentStore | VersionControl | AuditStore  │
    │  WebhookStore | GraphStore (Neo4j)            │
    └──────────────────────┬───────────────────────┘
                           │
    ┌──────────────────────▼───────────────────────┐
    │              Core / LLM Layer                  │
    │  ResilientLLMClient | LLMCache | LLMRouter    │
    │  AgentLoop | Ollama | OpenAI Compat           │
    └──────────────────────────────────────────────┘
```

### 1.3 关键数据流路径

```
raw 文档上传 → Parser → Extractor → WikiCompiler → VersionControl → index.md
                                        │
                                        ├─ GraphStore (Neo4j)
                                        ├─ WikiLint (健康检查)
                                        └─ WikiQuery (Q&A 引擎)
```

---

## 二、存储层（Storage Layer）

### 2.1 模块清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `document_store.py` | 411 | 文档元数据 + 上传/删除/搜索 + Pipeline 状态机 |
| `version_control.py` | ~300 | 版本保存/获取/对比/回滚/批量查询 |
| `audit_store.py` | ~200 | 审计日志写入/查询 |
| `webhook_store.py` | ~150 | Webhook 配置管理 |
| `graph_store.py` | ~500 | Neo4j 图数据库 CRUD |

### 2.2 时序分析

#### DocumentStore 写入流程
```
save(filename, content, fmt)
  ├─ [1] hashlib.sha256(content)           ─── 内存 (O(n) 内容大小)
  ├─ [2] _find_by_checksum(checksum)        ─── SQLite SELECT (1次)
  ├─ [3] 文件写入 stored_path              ─── 磁盘 IO (同步)
  ├─ [4] INSERT INTO documents             ─── SQLite INSERT (1次)
  └─ [5] return self.get(doc_id)           ─── SQLite SELECT (1次)

总计: 2 次 SQLite 读 + 1 次 SQLite 写 + 1 次文件 IO
```

#### VersionControl 写入流程
```
save_version(doc_key, title, content)
  ├─ [1] BEGIN IMMEDIATE                    ─── 排他写锁
  ├─ [2] SELECT MAX(version)               ─── SQLite 读
  ├─ [3] 内容 checksum 对比                 ─── 内存
  ├─ [4] INSERT INTO document_versions      ─── SQLite 写
  ├─ [5] COMMIT
  └─ [6] 版本上限检查 + 清理旧版本           ─── SQLite DELETE

并发安全: BEGIN IMMEDIATE + UNIQUE(doc_key, version) 双保险
```

### 2.3 问题列表

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| **S1** | **SQLite 单写锁瓶颈** | **P0** | 多 worker（uvicorn workers=2+）并发写入时 `database is locked`。`BEGIN IMMEDIATE` 缓解但治标不治本 |
| **S2** | **每次操作创建新连接** | **P0** | `_get_db()` 每次调用 `sqlite3.connect()` + `_init_schema()` + `PRAGMA journal_mode=WAL`。高频调用浪费约 1-3ms/次 |
| **S3** | **11 个独立 SQLite 数据库** | **P1** | documents.db / versions.db / audit.db / events.db / auth.db / collab_events.db / review_queue.db / search_index.db / templates.db / webhooks.db / wiki_lint.db。无法跨库 JOIN，备份复杂 |
| **S4** | **无 Alembic 迁移** | **P1** | Schema 变更靠 `CREATE TABLE IF NOT EXISTS`，无法处理列变更和索引变更 |
| **S5** | **Neo4j 无连接池** | **P1** | `graph_store.py` 直接 `neo4j.GraphDatabase.driver()`，无连接池配置和重试策略 |
| **S6** | **文件存储无校验** | **P2** | `read_content()` 直接读文件，无 checksum 校验 |
| **S7** | **无 Repository 抽象** | **P2** | Router 直接调用 `get_document_store()` 等，无接口抽象层 |

### 2.4 优化建议

| 优先级 | 建议 | 预期收益 |
|--------|------|----------|
| **P0** | 引入连接池（单例 `sqlite3.Connection` + 线程锁），`_get_db()` 复用连接 | 减少 90% 连接开销 |
| **P0** | 生产环境切换到 PostgreSQL（`config.py` 已预留 `db_backend` 选项） | 解决并发写入、跨库查询 |
| **P1** | 引入 Alembic 数据库迁移 | Schema 变更可追溯、可回滚 |
| **P1** | Neo4j 驱动添加 `max_connection_lifetime` + 指数退避重试 | 图数据库稳定性 |
| **P2** | 统一数据库连接管理（ConnectionManager 单例），减少重复 `_init_schema()` | 启动性能提升 |

---

## 三、知识编译引擎（Knowledge Compiler）

### 3.1 模块清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `wiki_compiler.py` | 2331 | 核心编译引擎（解析→抽取→LLM编译→保存） |
| `wiki_compiler_types.py` | ~450 | 类型定义（WikiPage, PipelineTrace, SectionTrace） |
| `wiki_compiler_utils.py` | ~300 | 工具函数（slugify, cosine_similarity, tokenize） |
| `wiki_query.py` | 1440 | 三路召回 + RRF 融合 + QA 引擎 + 知识复利回写 |
| `wiki_lint.py` | ~800 | 10 种 lint 检测（矛盾/过时/孤岛/死链/缺章节等） |
| `wiki_index.py` | ~300 | 索引管理（list/rebuild/search） |
| `wiki_drift.py` | ~200 | 漂移检测（checksum 对比 + stale 标记） |
| `wiki_log.py` | ~150 | Wiki 操作日志（OKF log.md） |
| `wikilink.py` | ~300 | 双向链接管理（backlink/outlink/deadlink/orphan） |
| `compiler.py` | ~400 | 知识图谱编译器（实体去重/合并/权威评分） |
| `graph_store.py` | ~500 | Neo4j 图数据库 CRUD |
| `okf_adapter.py` | ~400 | OKF 格式适配层 |
| `okf_validator.py` | ~300 | OKF 合规校验 |
| `review_queue.py` | ~200 | 审查队列管理 |
| `runbook_generator.py` | ~200 | Runbook 自动生成 |
| `doc_generator.py` | ~400 | 文档自动生成 |

### 3.2 核心时序：WikiCompiler.compile_raw_to_wiki()

```
compile_raw_to_wiki(doc_id)
  │
  ├─[P0] 清空 _llm_cache（仅单次编译有效）
  │
  ├─[1] store.get(doc_id)                     ─── SQLite 读 (1次)
  ├─[2] store.read_content(doc_id)            ─── 文件 IO (1次)
  │
  ├─[3] parser.parse(stored_path, doc_id)     ─── 解析器 (同步)
  │     └─ 返回 ParsedDocument (elements + heading_tree)
  │
  ├─[4] extractor.extract(doc)                ─── LLM 调用 (1次)
  │     ├─ _build_context(doc)                 ─── 内存 (100 元素 × 2000 字符)
  │     ├─ _call_llm(context)                  ─── LLM API 调用
  │     └─ _apply_gating(entities)             ─── 置信度门控
  │
  ├─[4b] extractor.classify_paragraphs(doc)    ─── LLM 调用 (N/5 次)
  │       └─ 段落批量分类 (batch_size=5)
  │
  ├─[5] P3-4: _compile_to_graph(doc_id, extraction) ─── Neo4j 批量写
  │
  ├─[6] Phase 1: 并行实体编译 (P0-1)
  │     ├─ asyncio.Semaphore(max_concurrency=3)
  │     ├─ asyncio.gather(*tasks)               ─── N 个并发 LLM 调用
  │     │   └─ 每个 entity: _llm_write_body() → LLM 调用
  │     └─ P0-1 已实现并行化
  │
  ├─[7] Phase 2: 串行保存
  │     ├─ for each entity result:
  │     │   ├─ _save_page(page)                  ─── VersionControl 写
  │     │   │   ├─ _find_similar_page()          ─── 全量 list_wiki_pages() + 逐页 get_latest()
  │     │   │   ├─ _merge_existing()             ─── 内存合并
  │     │   │   ├─ vc.save_version()             ─── SQLite 写
  │     │   │   ├─ update_backlinks()             ─── SQLite 写
  │     │   │   ├─ append_log_entry()             ─── SQLite 写
  │     │   │   └─ _backlink_existing_pages()     ─── 全量扫描 + 逐页 SQLite 写
  │     │   ├─ _sync_page_to_graph()             ─── Neo4j 写
  │     │   ├─ _validate_page_quality()           ─── 内存
  │     │   └─ _detect_conflicts_with_llm()       ─── LLM 调用 (仅 updated)
  │     └─
  │
  ├─[8] struct_compile: _compile_heading_tree_to_wiki()
  │     └─ 递归遍历 heading_tree
  │         └─ 每个节点: _llm_compile_section() → LLM 调用
  │             └─ _save_page() → VersionControl 写
  │
  ├─[9] record_compiled_checksum() + clear_stale()
  │
  ├─[10] rebuild_index()                       ─── 全量 O(n) 重建
  │
  └─[11] 指标埋点 (compile_duration_seconds, pages_created 等)

关键 IO 统计 (N 实体 + M 章节 + S 已有页面):
  LLM 调用: 1 (extract) + N/5 (classify) + N (compile) + M (struct_compile)
           + (updated 页面数) (conflict_detect)
  SQLite 读: 1 + S (similarity 全量) + S×N (每个 entity 检查相似度)
  SQLite 写: N + M + backlink 修改的页面数
  Neo4j 写: N + M
  Neo4j 读: N (P3-1 图谱关系查询)
```

### 3.3 问题列表

| # | 问题 | 严重度 | 位置 | 影响 |
|---|------|--------|------|------|
| **K1** | **_find_similar_page 全量扫描** | **P0** | `wiki_compiler.py:1582-1662` | 每个新实体都调用 `list_wiki_pages()` + 逐页 `vc.get_latest()` 读正文。S 个已有页面产生 S 次 SQLite 读。500 页面 = 500 次 SQLite 查询 |
| **K2** | **_backlink_existing_pages 全量扫描** | **P0** | `wiki_compiler.py:2141-2207` | 新建页面时扫描全部已有页面，检测是否需要插入回链。对每个页面做 `vc.get_latest()` + `vc.save_version()`。1000 页面 = 1000+ 次 SQLite 读写 |
| **K3** | **LLM 缓存仅单次编译有效** | **P1** | `wiki_compiler.py:92` | `_llm_cache` 是实例字典，每次 `compile_raw_to_wiki()` 开头清空。相同文档重复编译无法复用缓存 |
| **K4** | **每个 entity 都查询图谱关系** | **P1** | `wiki_compiler.py:1445-1469` | `_fetch_graph_relations()` 对每个 entity 调用 `graph_store.query_related()`。N 个实体 = N 次 Neo4j 查询 |
| **K5** | **结构编译无并行化** | **P1** | `wiki_compiler.py:1029-1199` | `_compile_tree_node_with_llm()` 递归逐节点编译，无并行。同级节点 LLM 调用相互独立可并行 |
| **K6** | **merge_body_sections 段落去重 O(n²)** | **P2** | `wiki_compiler.py:1973-2000` | 每个新段落与所有旧段落做 Jaccard 相似度对比，复杂度 O(n×m) |
| **K7** | **paragraph_classifications 无缓存** | **P2** | `wiki_compiler.py:320-350` | 同一文档重复编译时，paragraph classification 每次都重新调用 LLM |
| **K8** | **wiki_compiler.py 过长 (2331 行)** | **P2** | 整个文件 | 单文件包含解析、抽取、LLM 编译、结构编译、索引重建、质量校验、Pipeline Trace、相似度检测、冲突检测、回链建立等全部逻辑 |

### 3.4 优化建议

| 优先级 | 建议 | 预期收益 |
|--------|------|----------|
| **P0** | 相似度检测改为批量预加载：一次 `get_latest_batch()` 加载所有页面正文，内存中计算余弦相似度 | 500 页面：500 次 SQLite → 1 次批量查询 |
| **P0** | 回链建立改为批量扫描：一次加载所有页面正文，正则匹配候选词，批量 `save_version` | 1000 页面：1000+ 次 SQLite → 1 次批量读 + N 次批量写 |
| **P1** | LLM 缓存持久化到 SQLite（按 content_hash 索引），跨编译复用 | 重复编译相同文档：LLM 调用减少 80%+ |
| **P1** | 图谱关系查询批量化：一次 `query_related_batch()` 查询所有实体的一跳邻居 | N 次 Neo4j → 1 次 |
| **P1** | 结构编译中同级节点并行化（asyncio.gather + Semaphore） | 同级 M 个节点：延迟从 M×t 降至 max(t, M/3×t) |
| **P2** | 拆分 `wiki_compiler.py` 为 `wiki_parser.py`、`wiki_extractor.py`、`wiki_struct_compiler.py`、`wiki_quality.py` | 可维护性提升 |

---

## 四、LLM 集成层（core/llm）

### 4.1 模块清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `base.py` | ~200 | 抽象基类 LLMBackend + ChatMessage/LLMResponse 数据类 |
| `cache.py` | ~300 | 语义去重缓存（SHA256 键 + TTL + LRU 淘汰） |
| `router.py` | ~200 | 多后端路由 + 降级链 |
| `resilient.py` | ~300 | 指数退避重试 + 并发限流 + Token Bucket |
| `agent_loop.py` | ~200 | Agent 循环（工具调用编排） |
| `ollama.py` | ~100 | Ollama 后端适配 |
| `openai_compat.py` | ~100 | OpenAI 兼容 API 适配 |

### 4.2 时序分析

```
ResilientLLMClient.chat(messages, temperature, max_tokens)
  │
  ├─[1] LLMCache.get(cache_key)              ─── 内存查询 (O(1))
  │     └─ 命中 → 返回缓存结果
  │
  ├─[2] Semaphore.acquire()                  ─── 并发限流
  │
  ├─[3] TokenBucketRateLimiter.acquire()      ─── 令牌桶限流 (可选)
  │
  ├─[4] for attempt in range(max_retries):
  │     ├─ llm_backend.chat(messages)         ─── HTTP API 调用
  │     │   └─ httpx.AsyncClient → POST /v1/chat/completions
  │     ├─ 成功 → 缓存结果 → 返回
  │     └─ 失败 → 指数退避 (base * 2^attempt + jitter)
  │         └─ 重试耗尽 → 尝试 LLMRouter 降级链
  │
  └─[5] Semaphore.release()

平均延迟: 200-500ms (DeepSeek) / 500-2000ms (Ollama 本地)
```

### 4.3 问题列表

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| **L1** | **LLMCache 纯内存无持久化** | **P1** | 服务重启后缓存全部丢失，重启后首轮编译全量 LLM 调用 |
| **L2** | **embed_query/embed_texts 无缓存** | **P1** | Wiki 查询每次重新计算 embedding，相同页面重复计算 |
| **L3** | **httpx 客户端无连接池复用** | **P1** | 每个 LLM 后端创建独立 `httpx.AsyncClient`，无连接池 |
| **L4** | **Token 使用量无统计** | **P2** | 无 token 用量追踪，成本不可见 |

### 4.4 优化建议

| 优先级 | 建议 | 预期收益 |
|--------|------|----------|
| **P1** | LLMCache 持久化到 SQLite（定期淘汰 + 启动加载热门条目） | 重启后恢复缓存，减少 50%+ LLM 调用 |
| **P1** | Embedding 缓存（slug → embedding 向量，版本变化失效） | 已实现 `_wiki_emb_cache`（wiki_query.py），扩展到全局 |
| **P1** | httpx 连接池复用（单例 `AsyncClient` + `keepalive`） | 减少 30-50ms/请求 的 TCP 握手开销 |
| **P2** | Token 用量追踪（cost_tracker 已有基础，补齐 LLM 调用侧） | 成本可见，支持预算控制 |

---

## 五、解析与抽取引擎（Parsers & Extraction）

### 5.1 模块清单

| 文件 | 行数 | 职责 |
|------|------|------|
| `base.py` | ~150 | ParsedDocument 数据类 + 解析器抽象基类 |
| `registry.py` | ~50 | 解析器注册表（format → parser） |
| `markdown_parser.py` | ~120 | Markdown 解析（heading_tree + elements） |
| `text_parser.py` | ~60 | 纯文本解析 |
| `sql_parser.py` | ~70 | SQL 文件解析 |
| `markitdown_adapter.py` | ~100 | MarkItDown 外部工具适配（docx/xlsx/pptx/html） |
| `mineru_adapter.py` | ~100 | MinerU 外部工具适配（高精度 docx/xlsx） |
| `unstructured_adapter.py` | ~100 | Unstructured 库适配（pdf/图片等） |
| `extractor.py` | 368 | LLM 知识抽取引擎 + 段落分类 |
| `rule_extractor.py` | ~120 | 规则化兜底抽取 |
| `types.py` | ~80 | 抽取结果类型定义 |

### 5.2 时序分析

```
KnowledgeExtractor.extract(doc)
  │
  ├─[1] _build_context(doc)
  │     ├─ 取前 100 个元素 (P0-2 已从 20 提升)
  │     ├─ 每个元素截断到 2000 字符 (P0-2 已从 500 提升)
  │     └─ 动态 token 估算 (max_tokens * 2 字符)
  │
  ├─[2] _call_llm(context)                   ─── LLM 调用 (1次)
  │     └─ 返回 [{entity_type, name, properties, confidence, evidence_span}]
  │
  ├─[3] LLM 返回空 → 规则兜底 extraction
  │     └─ rule_extractor.extract(doc)       ─── 纯规则匹配
  │
  └─[4] _apply_gating(entities, relations)
        ├─ confidence >= 0.85 → auto_accepted
        ├─ confidence >= 0.60 → review
        └─ confidence < 0.60  → discarded

KnowledgeExtractor.classify_paragraphs(doc)
  │
  ├─ 仅取 type==paragraph 的元素
  ├─ batch_size=5 分批调用 LLM
  └─ 每批 1 次 LLM 调用 → 总计 ⌈paragraphs/5⌉ 次
```

### 5.3 问题列表

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| **E1** | **段落分类 LLM 调用次数过多** | **P1** | 批量大小 5，100 段落 = 20 次 LLM 调用。可增大到 15-20 |
| **E2** | **外部解析器依赖无版本锁定** | **P1** | markitdown/mineru/unstructured 版本未在 requirements.txt 锁定 |
| **E3** | **解析器均为同步调用** | **P2** | 外部解析器（markitdown/mineru）可能耗时数秒，阻塞事件循环 |
| **E4** | **段落分类结果无缓存** | **P2** | 同一文档重复编译时重新分类 |
| **E5** | **extraction 和 classify 是两次独立 LLM 调用** | **P2** | 可合并为一次调用（同时输出实体和段落分类） |

### 5.4 优化建议

| 优先级 | 建议 | 预期收益 |
|--------|------|----------|
| **P1** | 段落分类 batch_size 从 5 提升到 20 | 100 段落：20 次 → 5 次 LLM 调用 |
| **P1** | 外部解析器调用改为 `run_in_executor`（线程池） | 避免阻塞事件循环 |
| **P2** | 合并 extract + classify 为一次 LLM 调用（联合 prompt） | 减少 1 次 LLM 调用 |
| **P2** | 段落分类结果缓存（按 doc_id + checksum） | 重复编译免除分类开销 |

---

## 六、知识查询引擎（Wiki Query）

### 6.1 核心时序：WikiQAEngine.answer()

```
answer(question, recall_limit=5)
  │
  ├─[P0-2] 查询缓存检查
  │   └─ cache_key = md5(question + recall_limit + writeback)
  │       └─ 命中且 TTL < 300s → 直接返回
  │
  ├─[1] recall_pages(question, limit=5)
  │   ├─ _tokenize(question)                  ─── 内存 (关键词分词)
  │   ├─ list_wiki_pages(limit=1000)          ─── SQLite 读 (1次)
  │   │
  │   ├─ 路径1: 关键词召回
  │   │   ├─ vc.get_latest_batch(doc_keys)    ─── SQLite 批量读 (1次)
  │   │   └─ 逐页匹配 title/tags/body          ─── 内存 (O(N×T))
  │   │
  │   ├─ 路径2: 向量召回 (P2-1.1)
  │   │   ├─ embed_query(question)            ─── LLM API 调用 (1次)
  │   │   ├─ _get_wiki_embeddings()           ─── 批量 embedding + 缓存
  │   │   └─ _rank_by_cosine()                ─── 内存 (O(N×D))
  │   │
  │   ├─ 路径3: 图谱召回 (P3-1)
  │   │   ├─ graph_store.search_entities()    ─── Neo4j 查询 (最多 10 tokens)
  │   │   └─ graph_store.query_related()      ─── Neo4j 查询 (每实体 1次)
  │   │
  │   └─ RRF 融合 (Reciprocal Rank Fusion)
  │       └─ score(d) = Σ 1/(k + rank_i(d))
  │
  ├─[2] backlink 扩展 (最多 +2)
  │   ├─ get_backlinks(hit.slug)              ─── SQLite 读
  │   └─ vc.get_latest_batch()                ─── SQLite 批量读
  │
  ├─[3] 加载页面正文
  │   └─ vc.get_latest_batch(wiki_doc_keys)   ─── SQLite 批量读 (1次)
  │
  ├─[4] _llm_answer(question, contexts)       ─── LLM 调用 (1次)
  │
  ├─[5] writeback_new_facts() (知识复利)
  │   ├─ _extract_new_facts()                 ─── LLM 调用 (1次)
  │   ├─ _validate_facts()                    ─── 规则校验 + LLM 自校验 (可选)
  │   └─ _append_fact_to_page()               ─── SQLite 写 (每事实 1次)
  │
  └─[6] 缓存查询结果 (TTL 300s)

关键 IO 统计:
  LLM 调用: 1 (embed) + 1 (answer) + 1 (extract_facts) + 1 (verify, 可选)
  SQLite 读: 1 (list_wiki_pages) + 1 (batch keywords) + 1 (batch embeddings) + 1 (batch backlinks) + 1 (batch contexts)
  Neo4j 查询: ≤10 (search_entities) + ≤10 (query_related)
```

### 6.2 问题列表

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| **Q1** | **图谱召回路径对每个 token 调用 Neo4j** | **P1** | 10 个 token × 2 次查询/实体 = 最多 20 次 Neo4j 查询。应合并为批量查询 |
| **Q2** | **向量召回总是全量计算** | **P1** | 无近似最近邻（ANN）索引，需计算所有页面余弦相似度。1000 页面 = 1000 次向量运算 |
| **Q3** | **backlink 扩展循环中逐个 break** | **P2** | 只需前 2 个非重复 backlink，但循环提前 break 后批量加载仍执行 |
| **Q4** | **关键词分词无 jieba 时退化为单字切分** | **P2** | 配置 `search_tokenizer: jieba` 但无 jieba 时退化为 whitespace，中文分词效果差 |

### 6.3 优化建议

| 优先级 | 建议 | 预期收益 |
|--------|------|----------|
| **P1** | 图谱召回批量化：一次 `search_entities_batch(tokens)` 查询所有 token | 20 次 Neo4j → 1 次 |
| **P1** | 向量召回引入 FAISS/HNSW 近似最近邻索引 | 1000 页面：1000 次 → ~50 次向量运算 |
| **P2** | 安装 jieba 分词库（已配置但未强制依赖） | 中文分词精度提升 50%+ |

---

## 七、API 路由层（Routers）

### 7.1 模块清单

| 文件 | 路由数 | 核心端点 |
|------|--------|----------|
| `llm_wiki_router.py` | 23 | ingest, recompile, query, lint, drift, backlinks, index |
| `documents_router.py` | 8 | CRUD, upload, search, pipeline status |
| `wiki_router.py` | 6 | CRUD, publish, recompile-stream |
| `search_router.py` | 3 | search, suggest, reindex |
| `graph_router.py` | 5 | CRUD, build, query |
| `extraction_router.py` | 2 | extract, compile |
| `export_router.py` | 2 | export markdown, export json |
| `realtime_router.py` | 4 | connect, lock, unlock, events |
| 其他 18 个 router | 各 2-5 | auth, backup, changes, cost, events, topology, etc. |

### 7.2 问题列表

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| **R1** | **每个 router 注册 3 次（/api/v1, /api, /）** | **P1** | `main.py:297-302` 中每个 router 注册 3 次，OpenAPI schema 包含 3 份重复路由定义 |
| **R2** | **SSE 流式编译无超时保护** | **P1** | `llm_wiki_router.py` 的 streaming 端点无 timeout，长时间运行可能耗尽 worker |
| **R3** | **ingest 端点同步阻塞** | **P2** | `POST /llm-wiki/ingest` 同步返回编译结果，等待全流程完成。应改为异步任务模式 |
| **R4** | **依赖注入模式不一致** | **P2** | 部分 router 用 `Depends(get_document_store)`，部分直接 `get_document_store()` |
| **R5** | **Pydantic 模型分散在 router 文件中** | **P2** | WikiPageUpdate, RecompileSectionRequest 等定义在 router 文件而非 schemas/ |

### 7.3 优化建议

| 优先级 | 建议 | 预期收益 |
|--------|------|----------|
| **P1** | 移除 `/api` 和 `/` 前缀的兼容注册（仅保留 `/api/v1`），或使用 middleware 重定向 | OpenAPI schema 缩小 2/3 |
| **P1** | SSE 端点添加 `timeout_keep_alive` 和 `max_run_time` 参数 | 防止僵尸连接 |
| **P2** | 将 ingest 改为异步任务模式（返回 `task_id`，轮询结果） | 释放 worker，支持大文档编译 |
| **P2** | 统一 Pydantic 模型到 `schemas/` 目录 | 可维护性 |

---

## 八、搜索引擎（Search）

### 8.1 架构

```
SearchEngine
  ├─ SQLite FTS5 全文索引（search_index.db）
  ├─ 关键词搜索 + 向量搜索 + 混合排序
  ├─ 分词器：jieba (中文) / whitespace (英文)
  └─ 索引管理：create/rebuild/search/suggest
```

### 8.2 问题列表

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| **SE1** | **FTS5 索引不支持增量更新** | **P1** | 每次 `rebuild_index()` 全量重建，删除旧表再创建新表 |
| **SE2** | **搜索为同步方法** | **P2** | `engine.search()` 是同步方法，在 async 上下文中调用可能阻塞 |
| **SE3** | **无 BM25 调参** | **P2** | FTS5 默认 BM25 参数未调优，中文搜索效果待验证 |

### 8.3 优化建议

| 优先级 | 建议 |
|--------|------|
| **P1** | FTS5 索引改为增量更新（INSERT/UPDATE/DELETE 而非全量重建） |
| **P2** | 搜索方法改为异步（`run_in_executor`）或使用 aiosqlite |
| **P2** | BM25 参数调优（k1, b 参数针对中文优化） |

---

## 九、可观测性与 AIOps

### 9.1 模块清单

| 文件 | 职责 |
|------|------|
| `metrics.py` | Prometheus 指标（compile_duration, llm_calls, graph_sync_failures） |
| `tracing.py` | OpenTelemetry 分布式追踪 |
| `collector.py` | 业务指标采集器（定时任务） |
| `cost_tracker.py` | LLM 成本追踪 |
| `anomaly_detector.py` | 异常检测（统计方法） |
| `alertmanager_adapter.py` | Alertmanager 集成 |
| `change_correlator.py` | 变更关联分析 |
| `topology_builder.py` | 服务拓扑构建 |
| `timeseries_store.py` | 时序数据存储 |

### 9.2 问题列表

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| **O1** | **metrics collector 默认 30s 间隔** | **P2** | 对数据库产生周期性查询压力 |
| **O2** | **tracing 默认关闭** | **P2** | 生产环境调试困难 |
| **O3** | **cost_tracker 仅记录 JSONL 文件** | **P2** | 无可视化，无预算告警 |

### 9.3 优化建议

| 优先级 | 建议 |
|--------|------|
| **P2** | metrics collector 间隔调整为 60s（减少数据库压力） |
| **P2** | 生产环境 tracing 默认开启（采样率 10%） |
| **P2** | cost_tracker 添加 Prometheus 指标导出 + Grafana 面板 |

---

## 十、前端架构

### 10.1 模块清单

| 层级 | 文件数 | 关键文件 |
|------|--------|----------|
| 视图 (views/) | 22 页面 | WikiView, DocumentsView, GraphView, PipelineView, SearchView |
| 组件 (components/) | 15+ | WikiContent, WikiEditor, WikiSidebar, CollabPanel, DocumentTable |
| 组合式函数 (composables/) | 8 | useAsyncData, useAsyncList, useCollab, useSse, usePermission |
| Store (stores/) | 4 | app, auth, setup, onboarding |
| API (api/) | 20 | index (axios), wiki, documents, search, graph, realtime, mcp |
| 工具 (utils/) | 6 | format, frontmatter, wikiRender, menuBuilder, icons |

### 10.2 时序分析：WikiView 页面加载

```
WikiView.onMounted()
  │
  ├─[1] loadPages()                           ─── GET /api/v1/wiki (列表)
  │   └─ 构建侧边栏树
  │
  ├─[2] 检测 route.query.slug
  │   └─ loadPage(slug)                       ─── GET /api/v1/wiki/{slug} (页面内容)
  │       ├─ renderWikiMarkdown(content)       ─── marked 渲染
  │       └─ parseFrontmatter(content)         ─── YAML 解析
  │
  ├─[3] loadBacklinks(slug)                   ─── GET /api/v1/backlinks/{slug}
  │
  └─[4] 初始化实时协作连接 (S16-2)
      └─ useCollab(slug)                       ─── WebSocket 连接
```

### 10.3 问题列表

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| **F1** | **WikiView 无虚拟滚动** | **P1** | 大量页面时侧边栏渲染全部 DOM 节点 |
| **F2** | **无请求去重/缓存** | **P1** | 导航到同一页面时重新请求，未利用浏览器缓存 |
| **F3** | **GraphView 全量加载图谱数据** | **P1** | 无分页/懒加载，大型图谱渲染卡顿 |
| **F4** | **marked 渲染无缓存** | **P2** | `computed` 中每次重新渲染 Markdown |
| **F5** | **useSse 无自动重连** | **P2** | SSE 连接断开后需手动重连 |
| **F6** | **TypeScript strict 模式未开启** | **P2** | `tsconfig.json` 未启用 strict |
| **F7** | **前端 bundle 过大** | **P2** | index.js 546KB (gzip 160KB)，naive-ui 全量引入 |

### 10.4 优化建议

| 优先级 | 建议 | 预期收益 |
|--------|------|----------|
| **P1** | WikiSidebar 引入虚拟滚动（vue-virtual-scroller） | 大量页面时渲染性能提升 10x |
| **P1** | GraphView 添加分页/视口裁剪 | 大型图谱渲染流畅 |
| **P1** | API 层添加请求去重（相同请求 pending 时复用 Promise） | 减少重复请求 |
| **P2** | Markdown 渲染结果缓存（按 content hash） | 减少重复渲染 |
| **P2** | useSse 添加自动重连 + 指数退避 | 连接稳定性 |
| **P2** | Naive UI 按需引入（tree-shaking） | 减少 bundle 30%+ |

---

## 十一、Docker 与部署

### 11.1 现状

- 多阶段构建：`node:20-slim` → `python:3.14-slim`
- 运行时：nginx + supervisord + uvicorn (workers=2)
- 非 root 用户运行（opskg）
- 健康检查：`curl http://localhost:8080/health`
- K8s Helm Chart + 独立 YAML

### 11.2 问题列表

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| **D1** | **Dockerfile 中 `npm run build` 不含 typecheck** | **P1** | 注释说"typecheck 失败会阻断构建"，但实际命令只有 `vite build`。CI 中是分开的，但 Dockerfile 构建时缺少类型检查 |
| **D2** | **Dockerfile 注释过时** | **P2** | 第 8 行注释说 `python:3.12-slim`，实际第 42 行是 `python:3.14-slim` |
| **D3** | **supervisord 配置中 uvicorn workers 硬编码为 2** | **P2** | entrypoint 中 sed 动态替换，但匹配字符串 `--workers 2` 脆弱 |
| **D4** | **镜像无多架构支持** | **P2** | 仅构建 amd64，未覆盖 arm64 |

### 11.3 优化建议

| 优先级 | 建议 |
|--------|------|
| **P1** | Dockerfile 中添加 `RUN npm run typecheck` 在 `npm run build` 之前 |
| **P2** | 修正 Dockerfile 注释（3.12 → 3.14） |
| **P2** | supervisord 配置使用环境变量模板（`%(ENV_OPSKG_UVICORN_WORKERS)s`）替代 sed |
| **P2** | 添加多架构构建（linux/amd64 + linux/arm64） |

---

## 十二、CI/CD

### 12.1 现状

- 4 个 Job：backend / frontend / benchmark / docker
- 并发控制：同一分支新 push 取消旧 run
- pip-audit 安全扫描（continue-on-error）
- 40 个 verify 脚本分组执行

### 12.2 问题列表

| # | 问题 | 严重度 | 影响 |
|---|------|--------|------|
| **C1** | **CI ruff 仅检查 backend/app/ + scripts/** | **P2** | 不检查 backend/tests/，可能导致测试代码风格问题 |
| **C2** | **benchmark job 依赖本地 uvicorn 启动** | **P2** | 需要 Ollama 运行在 localhost，CI 环境无 LLM，benchmark 实际无法执行 |
| **C3** | **verify 脚本失败时静默跳过** | **P2** | 部分 verify 脚本遇到错误时可能静默退出（依赖 set -e 不够） |
| **C4** | **无端到端测试（e2e）** | **P2** | 前端 Playwright e2e 测试未纳入 CI（需 Chromium） |

### 12.3 优化建议

| 优先级 | 建议 |
|--------|------|
| **P2** | CI ruff 检查范围扩展为 `backend/`（含 tests） |
| **P2** | benchmark job 添加 mock LLM backend 或改为仅语法/导入检查 |
| **P2** | 添加 Playwright e2e 测试到 CI（安装 chromium） |

---

## 十三、可优化列表汇总

### 13.1 按优先级排序

#### P0（立即修复，影响核心功能/性能）

| # | 模块 | 问题 | 预期收益 |
|---|------|------|----------|
| **S1** | Storage | SQLite 单写锁瓶颈，生产环境切换到 PostgreSQL | 解决并发写入 |
| **S2** | Storage | 每次操作创建新连接，引入连接池 | 减少 90% 连接开销 |
| **K1** | Compiler | `_find_similar_page` 全量扫描（每实体 S 次 SQLite 读） | 500 页面：500 次 → 1 次批量查询 |
| **K2** | Compiler | `_backlink_existing_pages` 全量扫描（每新建页面 S 次 SQLite 读写） | 1000 页面：1000+ 次 → 1 次批量读 |

#### P1（近期优化，显著提升性能/体验）

| # | 模块 | 问题 | 预期收益 |
|---|------|------|----------|
| **S3** | Storage | 11 个独立 SQLite 数据库合并 | 跨库查询、备份简化 |
| **S4** | Storage | 引入 Alembic 迁移 | Schema 变更可追溯 |
| **S5** | Storage | Neo4j 连接池 + 重试策略 | 图数据库稳定性 |
| **K3** | Compiler | LLM 缓存持久化（跨编译复用） | 重复编译：LLM 调用减少 80%+ |
| **K4** | Compiler | 图谱关系查询批量化 | N 次 Neo4j → 1 次 |
| **K5** | Compiler | 结构编译同级节点并行化 | 同级 M 节点延迟降低 M/3 倍 |
| **L1** | LLM | LLMCache 持久化到 SQLite | 重启后恢复缓存 |
| **L2** | LLM | Embedding 缓存扩展 | 查询性能提升 |
| **L3** | LLM | httpx 连接池复用 | 减少 30-50ms/请求 |
| **E1** | Extraction | 段落分类 batch_size 5→20 | 100 段落：20 次 → 5 次 LLM 调用 |
| **E2** | Extraction | 外部解析器版本锁定 | 构建可重现 |
| **Q1** | Query | 图谱召回批量化 | 20 次 Neo4j → 1 次 |
| **Q2** | Query | 向量召回引入 ANN 索引 | 1000 次向量运算 → ~50 次 |
| **R1** | Routers | 移除重复路由注册 | OpenAPI schema 缩小 2/3 |
| **R2** | Routers | SSE 端点超时保护 | 防止僵尸连接 |
| **SE1** | Search | FTS5 索引增量更新 | 避免全量重建 |
| **F1** | Frontend | WikiSidebar 虚拟滚动 | 大量页面渲染性能 10x |
| **F2** | Frontend | API 请求去重 | 减少重复请求 |
| **F3** | Frontend | GraphView 分页/视口裁剪 | 大型图谱渲染流畅 |
| **D1** | Docker | Dockerfile 添加 typecheck | 构建时类型安全 |

#### P2（长期优化，提升可维护性/体验）

| # | 模块 | 问题 |
|---|------|------|
| **S6** | Storage | 文件存储无校验 |
| **S7** | Storage | 无 Repository 抽象 |
| **K6** | Compiler | merge_body_sections 段落去重 O(n²) |
| **K7** | Compiler | paragraph_classifications 无缓存 |
| **K8** | Compiler | wiki_compiler.py 过长 (2331 行) |
| **L4** | LLM | Token 使用量无统计 |
| **E3** | Extraction | 外部解析器同步调用阻塞 |
| **E4** | Extraction | 段落分类结果无缓存 |
| **E5** | Extraction | extract + classify 可合并为一次 LLM 调用 |
| **Q3** | Query | backlink 扩展循环优化 |
| **Q4** | Query | jieba 分词库安装 |
| **R3** | Routers | ingest 改为异步任务模式 |
| **R4** | Routers | 依赖注入模式统一 |
| **R5** | Routers | Pydantic 模型集中到 schemas/ |
| **SE2** | Search | 搜索方法改为异步 |
| **SE3** | Search | BM25 参数调优 |
| **O1** | Observability | collector 间隔调整 |
| **O2** | Observability | tracing 默认开启 |
| **O3** | Observability | cost_tracker 可视化 |
| **F4** | Frontend | Markdown 渲染缓存 |
| **F5** | Frontend | useSse 自动重连 |
| **F6** | Frontend | TypeScript strict 模式 |
| **F7** | Frontend | Naive UI 按需引入 |
| **D2** | Docker | Dockerfile 注释修正 |
| **D3** | Docker | supervisord 环境变量模板 |
| **D4** | Docker | 多架构构建 |
| **C1** | CI | ruff 检查范围扩展 |
| **C2** | CI | benchmark job mock LLM |
| **C3** | CI | verify 脚本错误处理 |
| **C4** | CI | Playwright e2e 纳入 CI |

### 13.2 按模块统计

| 模块 | P0 | P1 | P2 | 合计 |
|------|-----|-----|-----|------|
| Storage | 2 | 3 | 2 | 7 |
| Knowledge Compiler | 2 | 3 | 3 | 8 |
| LLM Integration | 0 | 3 | 1 | 4 |
| Parsers & Extraction | 0 | 2 | 3 | 5 |
| Wiki Query | 0 | 2 | 2 | 4 |
| API Routers | 0 | 2 | 3 | 5 |
| Search | 0 | 1 | 2 | 3 |
| Observability & AIOps | 0 | 0 | 3 | 3 |
| Frontend | 0 | 3 | 4 | 7 |
| Docker & Deploy | 0 | 1 | 3 | 4 |
| CI/CD | 0 | 0 | 4 | 4 |
| **合计** | **4** | **20** | **30** | **54** |

### 13.3 预期总收益

| 维度 | 当前 | 优化后 |
|------|------|--------|
| 编译 10 实体 × 500 已有页面 | ~500+ 次 SQLite 读 | ~3 次批量读 |
| 重复编译相同文档 | ~N 次 LLM 调用 | ~0 次（缓存命中） |
| 段落分类 100 段落 | 20 次 LLM 调用 | 5 次 LLM 调用 |
| 图谱召回 | 20 次 Neo4j 查询 | 1 次批量查询 |
| Wiki 查询 | 1000 次向量运算 | ~50 次 ANN 查询 |
| 前端 WikiSidebar 1000 页面 | 1000 DOM 节点 | 可视区域 ~20 节点 |
| 前端 bundle | 546KB (gzip 160KB) | ~380KB (gzip 110KB) |
| 生产并发写入 | SQLite 单写锁 | PostgreSQL 并发写入 |