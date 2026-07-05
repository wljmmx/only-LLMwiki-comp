# Sprint 6: 前端 MVP 实施计划

> **Goal**: 构建 OpsKG 前端 MVP，覆盖 5 个核心模块（仪表盘/文档管理/知识搜索/审查队列/Wiki 浏览）
> **Tech Stack**: Vue 3.5 + Vite 8 + Naive UI + Pinia + Vue Router + TypeScript + axios
> **Backend**: FastAPI 80 endpoints (REST)

---

## 一、项目结构

```
frontend/
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── src/
│   ├── main.ts                # 入口
│   ├── App.vue                # 根组件
│   ├── router/index.ts        # 路由配置（5+ 模块）
│   ├── stores/                # Pinia stores
│   │   └── app.ts             # 全局 app store（侧栏折叠/主题）
│   ├── api/                   # API 封装（按模块）
│   │   ├── index.ts           # axios 实例 + 拦截器
│   │   ├── documents.ts
│   │   ├── search.ts
│   │   ├── review.ts
│   │   └── wiki.ts
│   ├── types/                 # TypeScript 类型
│   │   └── api.ts
│   ├── utils/                 # 工具函数
│   │   └── format.ts
│   ├── components/            # 通用组件
│   │   └── layout/
│   │       ├── AppLayout.vue  # 主布局（侧栏+顶栏+内容区）
│   │       └── AppSidebar.vue # 侧栏导航
│   └── views/                 # 页面组件
│       ├── DashboardView.vue  # F1 仪表盘
│       ├── DocumentsView.vue  # F2 文档管理
│       ├── SearchView.vue     # F3 知识搜索
│       ├── ReviewView.vue     # F4 审查队列
│       └── WikiView.vue       # F5 Wiki 浏览
```

---

## 二、任务分解

### Task 1: 项目脚手架初始化
- `npm create vite@latest` + Vue 3 + TypeScript
- 安装依赖：naive-ui, pinia, vue-router, axios, @vueuse/core, md-editor-v3, vue-flow
- 配置 vite.config.ts（代理到后端 /api）
- 配置路由 + Pinia + Naive UI

### Task 2: 布局组件（侧栏+顶栏）
- AppLayout.vue: 三栏布局（侧栏 240px + 顶栏 56px + 内容区）
- AppSidebar.vue: 菜单导航（按模块分组，折叠功能）
- 侧栏菜单项：仪表盘 / 文档管理 / 知识搜索 / 审查队列 / Wiki 浏览

### Task 3: F1 仪表盘 (DashboardView)
- 4 个统计卡片：文档数 / 知识实体数 / 审查待办 / 事件数
- 调用 API: /documents/stats, /graph/stats, /review/stats, /search/stats
- 最近活动列表（最近文档/最近审查）

### Task 4: F2 文档管理 (DocumentsView)
- 文档列表表格（文件名/格式/大小/上传时间/状态）
- 上传按钮（拖拽上传 + 点击上传）
- 搜索/筛选（按格式/状态）
- 文档详情抽屉（解析结果预览）
- 删除操作

### Task 5: F3 知识搜索 (SearchView)
- 搜索框 + 结果列表
- 支持关键字搜索
- 结果卡片：标题/摘要/匹配度
- 点击查看详情

### Task 6: F4 审查队列 (ReviewView)
- 待审查列表
- 批量批准/拒绝
- 详情面板（审查项内容 + 审批操作）
- 统计卡片（待审/已批准/已拒绝）

### Task 7: F5 Wiki 浏览 (WikiView)
- 左侧页面树（按类型分组）
- 右侧 Markdown 内容渲染
- 双向链接支持（wikilink 解析）
- 版本历史入口

---

## 三、API 对接清单

| 模块 | API 端点 | 用途 |
|------|----------|------|
| 仪表盘 | GET /documents/stats | 文档统计 |
| 仪表盘 | GET /graph/stats | 图谱统计 |
| 仪表盘 | GET /review/stats | 审查统计 |
| 仪表盘 | GET /search/stats | 搜索统计 |
| 文档 | GET /documents | 文档列表 |
| 文档 | GET /documents/{id} | 文档详情 |
| 文档 | GET /documents/{id}/content | 文档内容 |
| 文档 | DELETE /documents/{id} | 删除文档 |
| 文档 | POST /parsers/parse/{fmt} | 解析文档 |
| 搜索 | GET /search | 搜索 |
| 搜索 | GET /search/stats | 搜索统计 |
| 审查 | GET /review/queue | 审查队列 |
| 审查 | GET /review/stats | 审查统计 |
| 审查 | POST /review/{id}/approve | 批准 |
| 审查 | POST /review/{id}/reject | 拒绝 |
| 审查 | POST /review/batch-approve | 批量批准 |
| Wiki | GET /wiki | Wiki 页面列表 |
| Wiki | GET /wiki/{slug} | Wiki 页面详情 |
| Wiki | GET /llm-wiki/index | Wiki 索引 |
| Wiki | GET /llm-wiki/backlinks/{slug} | 反向链接 |

---

## 四、验收标准

1. ✅ 项目可启动：`npm run dev` 正常运行
2. ✅ 5 个页面可访问（路由正常）
3. ✅ 布局完整（侧栏导航 + 顶栏 + 内容区）
4. ✅ 每个页面至少调用 1 个后端 API
5. ✅ TypeScript 类型检查通过：`vue-tsc --noEmit`
6. ✅ 构建通过：`npm run build`
