# OpsKG 架构重构计划

> 编译日期: 2026-07-20
> 状态: ✅ 全部完成
>
> 本文档为 OpsKG 知识库系统的完整重构计划，涵盖从原始文档上传到标准化输出文档的全链路。

---

## 一、四层架构总览

```
L0: Raw Documents      原始上传文件（不可变，仅触发编译）
L1: Compiled Sections  章节编译产物 .md（LLM 编译，版本控制，可 diff）
L2: Wiki Pages + Graph 知识产物（Wiki 阅读页面 + 知识图谱，从 L1 确定性派生 + LLM 增强）
L3: Search Index       FTS5 + 向量索引
```

---

## 二、实施计划

### Phase 1: 增强预处理 + 章节拆分 ✅

- [x] P1-1: 增强 `text_cleaner.py` — 新增附件提取、段落重建、代码块分类
- [x] P1-2: `app/attachments/extractor.py` — 附件提取器
- [x] P1-3: `app/attachments/store.py` — 附件存储 + attachment_index 表
- [x] P1-4: `app/sections/splitter.py` — 章节拆分器
- [x] P1-5: `app/sections/store.py` — 章节存储 + section_contributions 表

### Phase 2: LLM 并发控制 ✅

- [x] P2-1: `app/core/llm/concurrency.py` — 全局并发控制器
- [x] P2-2: 修改 `app/config.py` — 新增 llm_concurrency 配置

### Phase 3: 章节编译 + Wiki 生成 + 图谱抽取 ✅

- [x] P3-1: `app/sections/compiler.py` — LLM 章节编译
- [x] P3-2: 修改 `app/knowledge/wiki_compiler.py` — 新增 `compile_from_sections()`
- [x] P3-3: `app/extraction/compiled_extractor.py` — 从注释标签解析实体 + LLM 消歧

### Phase 4: 目录树 + 经验蒸馏 + 文档生成 ✅

- [x] P4-1: `app/knowledge/index_generator.py` — LLM 生成 Wiki 目录树
- [x] P4-2: `app/knowledge/experience_distiller.py` — LLM 批量蒸馏经验
- [x] P4-3: `app/output/templates/` — 文档模板库（YAML）
- [x] P4-4: `app/output/generator.py` — 按模板生成标准化输出文档

### Phase 5: 旧代码清理 + 漂移更新 ✅

- [x] P5-1: 删除 `app/extraction/rule_extractor.py`
- [x] P5-2: 修改 `app/knowledge/wiki_drift.py` — 章节级漂移检测
- [x] P5-3: 修改前端路由 — 新增文档生成界面

---

## 三、关键数据结构

### CompiledSection

```python
@dataclass
class CompiledSection:
    section_id: str
    source_doc_id: str
    title: str
    semantic_role: str
    content: str
    entities: list[dict]      # 从 <!-- entities: --> 解析
    relations: list[dict]     # 从 <!-- relations: --> 解析
    attachment_refs: list[dict]  # 从 <!-- attachment_refs: --> 解析
    wikilinks: list[str]
    version: int
    compiled_at: str
```

### SectionContribution

```python
@dataclass
class SectionContribution:
    section_id: str
    source_doc_id: str
    target_type: str         # 'wiki_page' | 'graph_entity' | 'graph_relation' | 'output_doc'
    target_slug: str
    contribution_type: str   # 'primary' | 'supplementary' | 'reference'
    compiled_version: int
    compiled_at: str
```

---

## 四、LLM 调用分布

| 阶段 | 调用次数 | 可否并行 | 备注 |
|------|---------|---------|------|
| 章节编译 | N 次（每章节） | 全部并行（受全局限制） | 核心 LLM 调用 |
| Wiki 生成 | M 次（每 Wiki 页面） | 页面级并行 | 多章节合成 |
| 图谱消歧 | 1 次（批量） | 不可拆 | 实体消歧合并 |
| 目录树生成 | 1 次 | 不可拆 | 全量 Wiki 分析 |
| 经验蒸馏 | 1 次（批量触发） | 不可拆 | 非实时 |
| 文档生成 | 1 次（按需） | 不可拆 | 用户触发 |

---

## 五、目录结构

```
/workspace/data/
├── uploads/                        # L0: 原始上传文件
├── sections/                       # 章节元数据
│   └── {section_id}.meta.json
├── compiled/                       # L1: 章节编译产物
│   ├── {section_id}.md             # 当前版本
│   └── {section_id}.v{version}.md  # 历史版本
├── attachments/                    # 附件存储
│   └── {doc_id}_{ref_id}.{ext}
├── wiki/                           # L2: Wiki 页面（VersionControl）
├── graph/                          # L2': 知识图谱（Neo4j）
├── output/                         # 输出文档
│   └── {doc_id}.md
├── events.db                       # 追踪数据库
│   ├── section_contributions
│   ├── attachment_index
│   ├── output_document_sources
│   ├── wiki_doc_checksums
│   ├── graph_entity_snapshots
│   └── lint_ignores
└── search/                         # L3: 搜索索引
```

---

## 六、并发控制配置

```yaml
llm_concurrency:
  max_global: 4
  stages:
    section_compile:
      max_concurrent: 3
      priority: high
      timeout_per_task: 120
    wiki_generate:
      max_concurrent: 1
      priority: medium
      timeout_per_task: 180
    entity_resolve:
      max_concurrent: 1
      priority: medium
      timeout_per_task: 60
    experience_distill:
      max_concurrent: 1
      priority: low
      timeout_per_task: 300
```