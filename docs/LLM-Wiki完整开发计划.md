# OpsKG LLM Wiki 完整开发计划

> 生成日期: 2026-07-05 | 范式对照: Karpathy LLM Wiki (知识编译与结构化沉淀)
> 仓库: https://github.com/wljmmx/only-LLMwiki-comp

## 〇、范式定位（重要澄清）

**"LLM Wiki" = Karpathy 路线的"知识编译与结构化沉淀"统一范式**，不是 "LLM 能力 + Wiki 能力" 两个独立能力的并列。

- **L1 Raw / L2 Wiki / L3 Schema** 是同一知识体的三个编译层级
- **Ingest / Query / Lint / Maintain** 是同一知识体的四个生命周期阶段
- LLM 在此范式中扮演 **wiki compiler / wiki admin**（编译器 + 管理员），而非独立的对话 agent
- 所有 P0/P1 任务都服务于"raw → LLM 编译 → wiki 沉淀 → drift 防护"这一条主线

实施严格按统一范式推进，不做能力拆分。

## 一、范式对照与核心结论

### 1.1 Karpathy LLM Wiki 范式核心

| 层级/阶段 | 范式要求 | 当前实现 | 差距 |
|-----------|---------|---------|------|
| **L1 Raw** | 原始文档 immutable，LLM 只读 | DocumentStore 持久化，但 delete() 会删物理文件 | 部分实现 |
| **L2 Wiki** | LLM 编译 raw → Markdown + `[[wikilink]]` + index.md | 图谱写 Neo4j，Wiki 手动 POST，无双向链接 | **范式错位** |
| **L3 Schema** | AGENTS.md 教 LLM 做 wiki 管理员 | 完全缺失 | **0→1** |
| **Ingest** | LLM 阅读 → 写 wiki 页面 → 建 cross-reference | 抽取写图谱，Runbook 明确"无需 LLM" | **终点错位** |
| **Query** | 从 index.md 导航编译 wiki 回答 | RAG 检索原文 snippet（向量未启用） | **范式错配** |
| **Lint** | 矛盾/stale/orphan/missing concept 检测 | 完全缺失 | **0→1** |
| **Maintain** | 漂移修正、孤岛清理、index 维护 | 仅版本控制，无漂移检测 | **缺失** |

### 1.2 核心结论

项目自称"LLM Wiki"但实际是"LLM 图谱 + 模板化 Runbook 生成器"。最致命的三个 gap：
1. **无 `[[wikilink]]` 双向链接** — Karpathy 范式的核心结构化机制
2. **无 LLM-as-wiki-compiler 路径** — LLM 抽取终点是图谱而非 Markdown wiki
3. **无 Lint 防漂移机制** — 知识漂移致命缺陷无防护

---

## 二、完整开发计划

### 阶段 1: LLM Wiki 范式基建（P0，阻断成立）

#### P0-1 L3 Schema 层建立
- 在仓库根新增 `AGENTS.md`，定义：
  - Wiki 页面骨架（front-matter 字段、章节顺序、`## 来源`反链区）
  - `[[wikilink]]` 语法规范（`[[slug]]` / `[[slug|显示文本]]`）
  - Ingest workflow（raw → 查重 → 编译 → cross-reference → 更新 index）
  - Lint workflow（矛盾/orphan/stale 检测规则）
  - 命名约定（slug 规则、实体页/概念页/故障页模板）
- 验收：AGENTS.md 存在且 LLM 能据此规范生成 wiki 页面

#### P0-2 `[[wikilink]]` 双向链接引擎
- 新建 `app/knowledge/wikilink.py`：
  - `parse_wikilinks(md) -> list[WikiLink]` 解析 `[[slug]]` 语法
  - `render_wikilinks(md, slug_map) -> str` 渲染为 `<a>` 或纯文本
  - `build_backlink_index() -> dict[slug, list[slug]]` 反向索引
  - `validate_links(md) -> list[DeadLink]` 死链检测
- 在 version_control 中维护 backlink 表
- 验收：wiki 保存时自动校验死链，backlink 可查询

#### P0-3 index.md 自动维护
- 新建 `app/knowledge/wiki_index.py`：
  - `rebuild_index() -> str` 按实体类型分组列出所有 wiki 页面 + 最近变更 + orphan 候选
  - `update_index(slug, action)` 增量更新
- 保存为 `wiki:index` 特殊 slug
- 验收：Ingest/Lint 后自动刷新 index.md

#### P0-4 Wiki 编译器（LLM-as-compiler）
- 新建 `app/knowledge/wiki_compiler.py`：
  - `compile_raw_to_wiki(doc_id) -> list[WikiPage]` LLM 阅读 raw 文档 → 编译实体页/概念页/故障页
  - 复用 `doc_generator.py` 的 LangGraph 能力，但终点是 Markdown wiki 页面
  - 自动插入 `[[wikilink]]` cross-reference
  - 增量更新（已存在页面则合并新事实 + 标注 stale）
- 验收：上传文档后自动生成 3-10 个 wiki 页面，含双向链接

### 阶段 2: 生命周期闭环（P1）

#### P1-1 Ingest 流水线改造
- `/graph/upload` 之后增加 wiki 编译阶段
- 扩展 `extractor._build_context` 为全文分块（map-reduce），避免长文档截断
- raw 文档 checksum 变化 → 标记关联 wiki 页面 stale
- 验收：长文档（>10KB）抽取覆盖率提升，raw 更新触发 wiki 重编译

#### P1-2 Query 改造为 wiki-based Q&A
- 新增 `/wiki/query` 端点：LLM 从 index.md 导航 → 读相关 wiki 页面 → 回答
- 回答中提取的新事实回写 wiki（实现"知识复利"）
- 新增 MCP 工具 `query_wiki_knowledge`
- 验收：Q&A 基于 wiki 而非原文检索，回答含 `[[来源]]` 引用

#### P1-3 Lint / Health Check 引擎
- 新建 `app/knowledge/linter.py`：
  - `detect_contradictions()` 同概念多页冲突检测
  - `detect_stale()` 页面 updated_at 早于其引用的 raw 文档
  - `detect_orphans()` 无入链的页面
  - `detect_missing_concepts()` 图谱有但 wiki 无的概念
- 新增 `/wiki/lint` 端点 + 定时任务
- 验收：lint 报告 4 类问题，可触发自动或人工修复

#### P1-4 漂移检测与重编译
- raw 文档 checksum 变化 → 标记关联 wiki 页面 stale → 触发 LLM 重编译 → diff 前后版本 → 进 ReviewQueue
- 验收：raw 更新后 wiki 自动标记 stale 并重编译

### 阶段 3: P2 各模块完善（与 LLM Wiki 并行）

#### P2-1 Runbook 生成器
- P2-1.1 向量检索 + RRF 融合（hybrid score = 0.4*keyword + 0.6*vector）
- P2-1.2 文档实体抽取缓存（key=doc_id+checksum）
- P2-1.3 Runbook 持久化 + 版本（自动发布到 wiki:runbook-*）
- P2-1.4 接入 LLM（从"规则模板"升级为"规则召回 + LLM 编译"）

#### P2-2 事件关联引擎
- P2-2.1 告警指纹去重 + 抑制
- P2-2.2 incident 状态机（open→ack→investigating→mitigated→resolved）
- P2-2.3 修复 noise_filtered 恒 0 bug
- P2-2.4 基于真实拓扑做根因推断（调用 topology_builder）

#### P2-3 变更关联引擎
- P2-3.1 修复 type_weight 权重稀释 bug（直接贡献而非 *0.1）
- P2-3.2 关联算法索引预过滤（避免 N×M 遍历）
- P2-3.3 变更内容 diff 分析（config key 变更、镜像/commit）

#### P2-4 服务拓扑
- P2-4.1 关系推断改为上下文共现（剔除全连接假阳性）
- P2-4.2 节点别名合并（db1.example.com ↔ db1）
- P2-4.3 拓扑 diff 检测（新增/消失/变更边）
- P2-4.4 Mermaid/Cytoscape 导出

#### P2-5 MCP 协议
- P2-5.1 升级协议版本（2025-06-18）+ 补全 capabilities
- P2-5.2 token auth（/mcp 端点走 verify_token）
- P2-5.3 resources 实现（知识库文档暴露为 resources）
- P2-5.4 prompts 实现（Runbook 模板暴露为 prompts）

### 阶段 4: 前端（暂缓，待后端 LLM Wiki 闭环后启动）

18 个模块（原 14 + 新增 F15 Wiki 编辑器 / F16 知识抽取工作台 / F17 知识问答 / F18 抽取配置），分 3 批交付。前端选型：Vue 3 + Naive UI + vue-flow + md-editor-v3。

---

## 三、执行顺序

### Sprint 1: LLM Wiki 范式基建（P0-1 ~ P0-4）
- AGENTS.md → wikilink 引擎 → index.md 维护 → wiki 编译器
- 完成后：上传文档能自动编译出带双向链接的 wiki 页面

### Sprint 2: 生命周期闭环（P1-1 ~ P1-4）
- Ingest 改造 → Query Q&A → Lint 引擎 → 漂移检测
- 完成后：四阶段生命周期闭环，防漂移

### Sprint 3: P2 关键 bug 修复（P2-2.3 / P2-3.1）
- noise_filtered 恒 0、type_weight 权重稀释
- 完成后：现有 AIOps 功能正确性保障

### Sprint 4: P2 工程化完善（按模块纵切）
- P2-1 全部 → P2-2 全部 → P2-3 全部 → P2-4 全部 → P2-5 全部

### Sprint 5: 全流程审计复盘
- 端到端冒烟测试 + bug 修复 + 功能验收

### Sprint 6: 前端启动（暂缓）

---

## 四、验收标准

| 阶段 | 验收项 |
|------|--------|
| P0 | 上传 1 文档 → 自动生成 ≥3 wiki 页面，含 `[[wikilink]]`，index.md 自动更新 |
| P1 | wiki Q&A 基于编译产物回答；lint 报告 4 类问题；raw 更新触发 wiki 重编译 |
| P2 | 38+59 测试全通过；冒烟测试覆盖 LLM Wiki 全流程 |
| 审计 | 端到端冒烟通过，无阻断 bug |

---

## 五、已完成进度

| Sprint | 内容 | 状态 | 验收 |
|--------|------|------|------|
| Sprint 1 | P0-1 L3 Schema (AGENTS.md) / P0-2 wikilink 双向链接引擎 / P0-3 index.md 自动维护 / P0-4 Wiki 编译器 (LLM-as-compiler) | ✅ 完成 | AGENTS.md 落地；wikilink.py / wiki_index.py / wiki_compiler.py 全部交付 |
| Sprint 2 | P1-1 Ingest 改造 + drift 检测 / P1-2 Wiki Q&A / P1-3 Lint 引擎 / P1-4 自动重编译闭环 | ✅ 完成 | wiki_drift.py / wiki_query.py / wiki_lint.py 全部交付；16 个 `/llm-wiki/*` API 上线 |
| Sprint 3 | P2-2.3 noise_filtered 恒 0 修复 / P2-3.1 type_weight 权重稀释修复 | ✅ 完成 | event_correlator.py / change_correlator.py bug 修复 |
| Sprint 5 | 全流程审计复盘 + bug 修复 + 功能验收 | ✅ 完成 | smoke_e2e 38/38、smoke_llm_wiki 40/40（共 78/78）全通过 |
| Sprint 4 | P2 工程化完善（P2-1/2/3/4/5 各子项） | ⏳ 待启动 | — |
| Sprint 6 | 前端（Vue 3 + Naive UI + 14 模块 | ⏳ 待启动 | — |

### 审计结论（Sprint 5）

- **范式一致性**：实现严格遵守 Karpathy 统一范式（L1/L2/L3 + Ingest/Query/Lint/Maintain），未拆分为独立能力
- **生命周期闭环验证**：raw 上传 → LLM 编译 → wiki 页面 → frontmatter + `[[wikilink]]` → index.md → Q&A 引用 → drift 检测 → stale 标注 → 自动重编译 → ReviewQueue → 全链路打通
- **防漂移机制生效**：checksum 变化触发 stale，重编译产生 diff 并入队 ReviewQueue，stale 自动清空
- **Lint 6 类检查生效**：contradiction / stale / orphan / missing_concept / missing_type_section / empty_section 全部产出
- **回归无破坏**：P2-2.3 / P2-3.1 修复后，原有 38 步 e2e 测试无回归

---

## 六、Backlog（待办任务池）

### Sprint 4：P2 工程化完善（后端）
| 子任务 | 模块 | 优先级 | 依赖 |
|--------|------|--------|------|
| P2-1 Runbook 生成器（向量检索+缓存+持久化+LLM 编译 | runbook_generator.py | P1 | P1-1 |
| P2-2 事件关联引擎（指纹去重+状态机+根因推断 | event_correlator.py | P1 | P1-1 |
| P2-3 变更关联引擎（索引预过滤+diff 分析 | change_correlator.py | P2 | P2-2 |
| P2-4 服务拓扑（上下文共现+别名合并+diff 检测+导出 | topology_builder.py | P2 | — |
| P2-5 MCP 协议（升级+token auth+resources+prompts | mcp/ | P2 | P0-2 |

### Sprint 6：前端（Vue 3 + Naive UI）
| 批次 | 模块 | 优先级 | 说明 |
|------|------|--------|------|
| 第一批 MVP | F1 仪表盘 / F2 文档管理 / F3 知识搜索 / F4 审查队列 / F5 Wiki 浏览 | P0 | 核心闭环 5 模块 |
| 第二批 AIOps | F6 Runbook 工作台 / F7 Incident 管理 / F8 变更关联 / F9 服务拓扑 | P1 | 运维场景 4 模块 |
| 第三批增强 | F10 模板管理 / F11 版本控制 / F12 导出中心 / F13 MCP 工具浏览器 / F14 知识图谱可视化 | P2 | 增强能力 5 模块 |

### Sprint 8 遗留（前端体验优化
| 任务 | 优先级 | 说明 |
|------|--------|------|
| S8-3 Onboarding 引导（5 步 tour） | P2 | 新用户首次使用引导 |
| S8-5 前端 Vitest 基础测试（30+ 用例） | P2 | 组件/工具函数单元测试 |
