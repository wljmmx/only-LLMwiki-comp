# OpsKG × OKF 对齐演进路线图

> 分支：`okf/okf-v0.1-pre-order` | 起始：2026-07-10 | 状态：**已完成 P0-P3 全部阶段**
> 目标：将 OpsKG 升级为 OKF v0.1 一等公民 + 运维增强平台
> 依据：[OKF v0.1 SPEC](https://openknowledgeformat.com/what-is-okf) + [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
> 验证：S17-1(82) + S17-2(38) + S17-3(35) + S17-4(19) + S12-2(25) = **199 assertions 全过**

## 一、设计原则

1. **平台增强 + 格式中立**：保留 OpsKG 现有 DB 存储 / 版本控制 / backlink / RBAC 等增强能力，在平台之上新增 OKF 适配层，使知识可双向流转（导入/导出）。
2. **双轨链接**：内部存储沿用 `[[wikilink]]`（性能优），所有渲染/导出出口统一经 `wikilink_to_okf()` 转换为标准 MD 链接（互操作优）。
3. **producer/consumer 独立**：导出的 bundle 须可被任意 OKF 消费者（`okf validate` / Obsidian / 其他 agent）解析，无 SDK 依赖。
4. **permissive consumption**：导入侧容忍未知 type / 缺失字段 / 断链，不拒绝 bundle。

## 二、OKF 三硬性约束对齐

| OKF 约束 | OpsKG 现状 | 对齐方式 |
|---------|-----------|---------|
| 每个概念文件含可解析 YAML frontmatter | ✅ 已满足 | — |
| frontmatter 含非空 `type` | ✅ 已满足（6 类） | — |
| 保留文件 `index.md` / `log.md` 守职责 | 🟡 index ✅ / log ❌ | P1 新增 `log.md` 持续维护 |

## 三、推荐字段对齐

| 字段 | 现状 | 对齐 |
|------|------|------|
| `title` | ✅ | — |
| `description` | ❌ | P1 编译时生成 + 导出时抽取 |
| `resource` | ⚠️ 仅 `sources.doc_id` | P1 映射本体属性为 URI |
| `tags` | ✅ | — |
| `timestamp` | ⚠️ 用 `updated_at` | P1 增加 `timestamp` 别名 |

## 四、互操作三支柱（P0 阻断解除）

1. **文件化分发**：新增 `okf_adapter.export_bundle(out_dir)` 把 wiki 目录树导出为标准 OKF bundle
2. **标准链接**：`[[slug]]` → `[display](/{type_dir}/{slug}.md)`（bundle-relative）
3. **Bundle 单位**：导出 tarball / 目录树 / git-friendly 结构

## 五、分阶段路线图

### 阶段 P0 — 互操作阻断解除（高优先）✅

| 编号 | 工作项 | 文件 | 验收 | 状态 |
|------|--------|------|------|------|
| P0-1 | OKF 适配层核心 | `backend/app/knowledge/okf_adapter.py` | 导出/导入 + 双链转换 + frontmatter 规范化 | ✅ 64edc4c |
| P0-2 | API 端点 + Router | `backend/app/routers/okf_router.py` + `main.py` | 6 端点（export/import/validate/preview/version/import-dir） | ✅ e5a2416 |

### 阶段 P1 — 格式规范对齐（中优先）✅

| 编号 | 工作项 | 文件 | 验收 | 状态 |
|------|--------|------|------|------|
| P1-1 | frontmatter 扩展 | `wiki_compiler.py` `_build_frontmatter_meta` | description/resource/timestamp 字段编译期生成 | ✅ f7f0c16 |
| P1-2 | log.md 持续维护 | `wiki_log.py`（新）+ `wiki_compiler.py` | `wiki:log` 特殊页面 + 导出为 `log.md` + FIFO 截断 | ✅ 207d834 |

### 阶段 P2 — 质量与生态（中优先）✅

| 编号 | 工作项 | 文件 | 验收 | 状态 |
|------|--------|------|------|------|
| P2-1 | OKF Validator + Lint 扩展 | `okf_validator.py`（新）+ `wiki_lint.py` | `TYPE_OKF_VIOLATION` + JSON 输出兼容 `okf validate` | ✅ 7d34683 |
| P2-2 | 消费侧容错强化 | `wiki_query.py` | 召回为空时降级 raw 文档检索 + 容忍未知 type | ✅ 90b298e |

### 阶段 P3 — 生态对齐（低优先）✅

| 编号 | 工作项 | 文件 | 验收 | 状态 |
|------|--------|------|------|------|
| P3-1 | AGENTS.md §9.1 演进 | `AGENTS.md` | "与 OKF 的关系" 小节 + 扩展字段语义 | ✅ d314edc |
| P3-2 | type 词表声明 | `okf_types.yaml`（新） | 6 概念类型 + 2 保留类型 + resource_scheme | ✅ d314edc |
| P3-3 | Citations 规范化 | `wiki_compiler.py` | `## Citations` 章节 + `[n] [text](uri)` 格式 | ✅ d314edc |

## 六、Bundle 目录结构约定

```
okf-bundle/
├── index.md                    # 根目录导航（渐进披露）
├── log.md                      # 变更审计日志
├── types.md                    # type 词表声明（P3）
├── incidents/{slug}.md         # incident 类型
├── runbooks/{slug}.md          # runbook 类型
├── services/{slug}.md          # service 类型
├── hosts/{slug}.md             # host 类型
├── concepts/{slug}.md          # concept 类型
└── entities/{slug}.md          # entity 类型
```

## 七、关键权衡

1. **不迁移存储**：DB 存储 → 适配层导出为文件，保留版本/协作/RBAC 增强
2. **双轨链接**：内部 `[[wikilink]]` 不变，导出统一转换
3. **`sources.doc_id` 不可移植**：导出时映射为 `resource` URI + Citations

## 八、验证策略

- 每个 P0/P1/P2 工作项配套 `verify_*.py` 验证脚本（沿用项目惯例）
- 新增 `tests/test_okf_adapter.py` 单元测试
- 端到端：导出 → 用纯 Python 解析校验 OKF 三约束 → 导回 → 比对内容一致

## 九、提交节奏

按工作项粒度提交，每个 commit 对应一个 P 编号工作项，遵循 Conventional Commits：

```
feat(okf): P0-1 implement OKF adapter (export/import/link-transform)
feat(okf): P0-2 add OKF export/import/validate API endpoints
feat(okf): P1-1 extend frontmatter with description/resource/timestamp
feat(okf): P1-2 maintain log.md via wiki:log special page
feat(okf): P2-1 add OKF validator and lint extension
feat(okf): P2-2 make wiki_query permissive consumer
docs(okf): P3-1 evolve AGENTS.md with OKF relationship
...
```
