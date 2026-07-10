# OpsKG 前端 UI/UX 审计评估与优化方案

> 审计日期：2026-07-10 | 审计基线：main 分支（含 OKF v0.1 对齐）
> 审计范围：27 个 view + 布局组件 + 设计系统 + 交互规范
> 审计方法：三维度并行深度审计（布局导航 / 核心业务视图 / 设计系统）

## 一、审计结论总览

OpsKG 前端 **以 Naive UI 为骨架，组件库选型合理，路由权限守卫扎实**，但存在三大系统性短板：

| 维度 | 成熟度 | 核心问题 |
|------|--------|---------|
| 设计系统 | 🔴 20% | 令牌层完全缺失，全量硬编码，无图标系统 |
| 响应式适配 | 🔴 0% | 源码零 `@media` 断点，移动端完全不可用 |
| 深色模式 | 🔴 30% | 开关存在但多处写死浅色，大面积失效 |
| 核心交互 | 🟡 55% | Wiki 问答无流式输出、编辑器无补全、搜索无高亮 |
| 信息架构 | 🟡 60% | 27 个 view 5 分组，但无面包屑、无快速通道 |
| 可访问性 | 🔴 25% | 无 skip-link、SVG 不可键盘访问、低对比度 |

**综合评估**：桌面宽屏下基础可用，但下沉到平板/手机或交付给键盘/读屏用户即暴露。设计系统层"几乎空白"，亟待从"无设计系统"跃迁到"基础设计系统可用"。

---

## 二、问题清单（按严重度分级）

### P0 — 阻断性（必须修复）

| 编号 | 问题 | 位置 | 影响 |
|------|------|------|------|
| P0-1 | **响应式完全缺失**：源码零 `@media` 断点，侧栏固定 240px/64px | [AppLayout.vue#L87-L99](file:///workspace/frontend/src/components/layout/AppLayout.vue#L87-L99) [style.css](file:///workspace/frontend/src/style.css) | 移动端/平板完全不可用 |
| P0-2 | **深色模式大面积失效**：body 写死浅色、图例写死白底、SVG 文字写死深色 | [style.css#L17-L18](file:///workspace/frontend/src/style.css#L17-L18) [GraphView.vue#L592](file:///workspace/frontend/src/views/GraphView.vue#L592) [TopologyView.vue#L328](file:///workspace/frontend/src/views/TopologyView.vue#L328) | 切深色后多处刺眼白块/不可见文字 |
| P0-3 | **Wiki v-html 潜在 XSS**：直接注入渲染 HTML，未确认 sanitize | [WikiView.vue#L227](file:///workspace/frontend/src/views/WikiView.vue#L227) | 恶意 wiki 内容可执行脚本 |
| P0-4 | **OnboardingTour 遮罩不可关闭**：`pointer-events:none` 导致点击无效 | [OnboardingTour.vue#L102](file:///workspace/frontend/src/components/onboarding/OnboardingTour.vue#L102) | 引导卡住无法退出 |
| P0-5 | **设计令牌层缺失**：无颜色/字号/间距/圆角/阴影变量，全量硬编码 | [style.css](file:///workspace/frontend/src/style.css) 全文 | 无单一事实源，维护成本极高 |

### P1 — 重要（强烈建议修复）

| 编号 | 问题 | 位置 | 影响 |
|------|------|------|------|
| P1-1 | 无面包屑，嵌套导航失去层级上下文 | [AppLayout.vue#L104](file:///workspace/frontend/src/components/layout/AppLayout.vue#L104) | 17 项菜单折叠后无法感知位置 |
| P1-2 | 路由级无加载反馈，首屏 chunk 下载空白 | [App.vue#L26-L28](file:///workspace/frontend/src/App.vue#L26-L28) | 用户感知"卡住" |
| P1-3 | 无骨架屏，加载态用 NSpin 导致 CLS | DashboardView/WikiView/ReviewView 等 | 布局跳动，体验不一致 |
| P1-4 | 可访问性：无 skip-link、图标按钮无 aria-label、焦点不管理 | [AppLayout.vue#L107-L109](file:///workspace/frontend/src/components/layout/AppLayout.vue#L107-L109) | 键盘/读屏用户每页需 Tab 穿越侧栏 |
| P1-5 | 菜单与路由双源，路径漂移静默 404 | [AppSidebar.vue#L20-L64](file:///workspace/frontend/src/components/layout/AppSidebar.vue#L20-L64) | meta.icon 定义但未被消费 |
| P1-6 | Wiki 问答无流式输出，等待焦虑高 | [WikiQueryView.vue#L25](file:///workspace/frontend/src/views/WikiQueryView.vue#L25) | LLM 生成期间只显示 spinner |
| P1-7 | Wiki 问答回答未渲染 Markdown | [WikiQueryView.vue#L133](file:///workspace/frontend/src/views/WikiQueryView.vue#L133) | 表格/代码块/列表丢失格式 |
| P1-8 | Wiki 编辑器无 `[[wikilink]]` 自动补全 | [WikiEditor.vue#L221](file:///workspace/frontend/src/components/wiki/WikiEditor.vue#L221) | 易产生死链，违背 AGENTS.md 核心规范 |
| P1-9 | Wiki 编辑器无 frontmatter 结构化编辑 | [WikiEditor.vue#L221-L228](file:///workspace/frontend/src/components/wiki/WikiEditor.vue#L221-L228) | YAML 格式错误无即时校验 |
| P1-10 | Wiki 浏览无 TOC 目录大纲 | [WikiView.vue](file:///workspace/frontend/src/views/WikiView.vue) | 长页面阅读体验差 |
| P1-11 | Wiki 浏览无版本历史/对比 | [WikiView.vue](file:///workspace/frontend/src/views/WikiView.vue) | VersionControl 能力未暴露 |
| P1-12 | Wiki 健康检查 Lint 表无快速修复入口 | [WikiHealthView.vue#L66-L102](file:///workspace/frontend/src/views/WikiHealthView.vue#L66-L102) | 发现问题后需手动搜索 slug 编辑 |
| P1-13 | 文档删除用 window.confirm，与 Naive UI 风格割裂 | [DocumentsView.vue#L263](file:///workspace/frontend/src/views/DocumentsView.vue#L263) | 阻塞主线程，无法自定义 |
| P1-14 | 文档无批量操作 | [DocumentsView.vue](file:///workspace/frontend/src/views/DocumentsView.vue) | 文档量大时效率低 |
| P1-15 | 搜索无关键词高亮、无结果跳转 | [SearchView.vue#L102](file:///workspace/frontend/src/views/SearchView.vue#L102) | 难判断相关性，结果不可点 |
| P1-16 | 图标系统完全缺失，全 emoji | [AppSidebar.vue#L18](file:///workspace/frontend/src/components/layout/AppSidebar.vue#L18) | 不可着色/不可访问/无专业感 |
| P1-17 | 5 处重复 page-header/loading-container CSS | IncidentsView/GraphView/TopologyView/ExportView/VersionsView | 无抽取，维护负担 |
| P1-18 | TopologyView SVG 节点不可键盘访问 | [TopologyView.vue#L316](file:///workspace/frontend/src/views/TopologyView.vue#L316) | 无 tabindex/role/keydown |
| P1-19 | GraphView 节点类型色 11 种硬编码，与 TopologyView 部分重复不一致 | [GraphView.vue#L68-L80](file:///workspace/frontend/src/views/GraphView.vue#L68-L80) | 无令牌，双源漂移 |
| P1-20 | ExportView 表单未用 NForm，NTag checkable 误用为选择按钮 | [ExportView.vue#L177-L208](file:///workspace/frontend/src/views/ExportView.vue#L177-L208) | 失去校验能力，语义错误 |

### P2 — 次要（建议优化）

| 编号 | 问题 | 位置 |
|------|------|------|
| P2-1 | 主内容区无 max-width，超宽屏阅读疲劳 | AppLayout.vue#L198 |
| P2-2 | 暗色模式/侧栏折叠态不持久化，刷新闪白 | stores/app.ts#L5-L6 |
| P2-3 | ErrorBoundary 默认暴露堆栈到生产 | ErrorBoundary.vue#L10 |
| P2-4 | 无统一 EmptyState 组件 | 全局 |
| P2-5 | 信息架构：知识入口冗余，版本控制归类不当 | AppSidebar.vue#L36-L50 |
| P2-6 | 无"最近访问/收藏"快速通道 | AppSidebar.vue |
| P2-7 | formatFileSize/formatDate 三处重复 | Dashboard/Documents/WikiHealth |
| P2-8 | 仪表盘无数据可视化，仅裸数字 | DashboardView.vue#L146 |
| P2-9 | 仪表盘错误静默 console.error，无重试 UI | DashboardView.vue#L131 |
| P2-10 | Wiki 树无搜索过滤，>50 页时不可用 | WikiView.vue#L71 |
| P2-11 | 文档搜索仅客户端过滤当前页 | DocumentsView.vue#L75 |
| P2-12 | formatFileSize 越界风险（无 TB） | DocumentsView.vue#L169 |
| P2-13 | Wiki 问答无多轮会话/无反馈机制 | WikiQueryView.vue |
| P2-14 | Wiki 健康检查 Lint 不自动加载 | WikiHealthView.vue#L293 |
| P2-15 | 按钮类型语义不统一（危险操作用 warning） | IncidentsView/TopologyView |
| P2-16 | Drawer/Modal 尺寸 4 种宽度不统一 | Incidents/Graph/Topology/Versions |
| P2-17 | OnboardingTour 无 Esc/方向键键盘支持 | OnboardingTour.vue#L133 |
| P2-18 | 路由切换无过渡动画 | App.vue |
| P2-19 | 滚动条仅 -webkit 前缀，Firefox 不生效 | style.css#L28 |
| P2-20 | type 字段本地化不一致（部分视图显示英文） | WikiQuery/Search/WikiHealth |

---

## 三、优化方案（分阶段）

### 阶段一：止血修复（P0，最高优先）

**目标**：修复阻断性问题，确保多端可用与基础安全。

#### 1.1 建立设计令牌层（P0-5）

在 [style.css](file:///workspace/frontend/src/style.css) 顶部新增 `:root` 令牌：

```css
:root {
  /* 品牌色 */
  --opskg-color-primary: #2080f0;
  --opskg-color-success: #18a058;
  --opskg-color-warning: #f0a020;
  --opskg-color-danger: #d03050;
  /* 文本（含对比度达标值） */
  --opskg-text-1: #1f2937;   /* 主文本 4.5:1+ */
  --opskg-text-2: #6b7280;   /* 次文本 4.6:1 */
  --opskg-text-3: #9ca3af;   /* 占位 */
  /* 背景 */
  --opskg-bg-body: #f9fafb;
  --opskg-bg-elevated: #ffffff;
  --opskg-bg-overlay: rgba(0,0,0,0.5);
  /* 字号（1.125 比例） */
  --opskg-fs-xs: 12px; --opskg-fs-sm: 13px; --opskg-fs-base: 14px;
  --opskg-fs-lg: 16px; --opskg-fs-xl: 20px; --opskg-fs-2xl: 24px;
  /* 间距（4 倍数） */
  --opskg-sp-1: 4px; --opskg-sp-2: 8px; --opskg-sp-3: 12px;
  --opskg-sp-4: 16px; --opskg-sp-6: 24px; --opskg-sp-8: 32px;
  /* 圆角 */
  --opskg-radius-sm: 4px; --opskg-radius-md: 8px; --opskg-radius-lg: 12px;
  /* 阴影 */
  --opskg-shadow-card: 0 1px 3px rgba(0,0,0,0.08);
  --opskg-shadow-popover: 0 8px 32px rgba(0,0,0,0.16);
  /* 焦点 */
  --opskg-focus-ring: 0 0 0 2px var(--opskg-color-primary);
}
[data-theme="dark"] {
  --opskg-text-1: #e5e7eb;
  --opskg-text-2: #9ca3af;
  --opskg-text-3: #6b7280;
  --opskg-bg-body: #18181c;
  --opskg-bg-elevated: #1f1f23;
}
```

#### 1.2 修复深色模式（P0-2）

- `body` 背景改 `var(--opskg-bg-body)`
- GraphView/TopologyView 图例 `rgba(255,255,255,*)` → `var(--opskg-bg-elevated)`
- TopologyView SVG 文字 `fill="#333"` → `fill="currentColor"` + 容器设 `color: var(--opskg-text-1)`
- VueFlow `Background pattern-color` 改响应式 `:pattern-color="themeVars.borderColor"`
- 在 AppSidebar 或顶栏暴露 `toggleDarkMode` 入口（store 有方法但无 UI）

#### 1.3 响应式适配（P0-1）

引入断点系统，[AppLayout.vue](file:///workspace/frontend/src/components/layout/AppLayout.vue) 侧栏改为响应式：

| 屏宽 | 侧栏行为 |
|------|---------|
| ≥1024px | 当前固定侧栏 240px |
| 768–1023px | 默认折叠 64px 图标栏 |
| <768px | drawer 抽屉模式 + 遮罩，header 加汉堡按钮 |

监听 `window.matchMedia` 自动设置 `sidebarCollapsed`。

#### 1.4 Wiki v-html 安全（P0-3）

在 [wikiRender.ts](file:///workspace/frontend/src/utils/wikiRender.ts) 确认使用 DOMPurify，或强制 sanitize；优先改用结构化渲染组件避免 v-html。

#### 1.5 OnboardingTour 遮罩修复（P0-4）

[OnboardingTour.vue#L241](file:///workspace/frontend/src/components/onboarding/OnboardingTour.vue#L241) 的 `pointer-events: none` 改为 `auto`，允许点击关闭；spotlight 外区域用 `inert` 阻止交互。

### 阶段二：核心体验（P1，高优先）

**目标**：补齐核心交互短板，提升专业感与一致性。

#### 2.1 导航与加载（P1-1~P1-5）

- **面包屑**：[AppLayout.vue](file:///workspace/frontend/src/components/layout/AppLayout.vue) header 增加 `BreadcrumbBar.vue`，基于 `route.matched` + 菜单分组映射
- **路由加载**：[App.vue](file:///workspace/frontend/src/App.vue) router-view 包 `<Suspense>` + `<Transition>`，fallback 用全屏骨架
- **骨架屏**：封装 `PageSkeleton.vue` 共享组件，替换各视图 NSpin
- **菜单单一事实源**：[AppSidebar.vue](file:///workspace/frontend/src/components/layout/AppSidebar.vue) 从 router meta 生成菜单，废弃硬编码 path/emoji
- **可访问性**：增加 skip-link、`aria-label`、路由 `afterEach` 焦点转移

#### 2.2 Wiki 体验升级（P1-6~P1-11）

- **流式问答**：[WikiQueryView.vue](file:///workspace/frontend/src/views/WikiQueryView.vue) 改 SSE 流式 + Markdown 渲染 + 多轮会话面板
- **编辑器增强**：[WikiEditor.vue](file:///workspace/frontend/src/components/wiki/WikiEditor.vue)
  - `[[` 触发 slug 补全浮层（查 `listWikiPages` 缓存）
  - frontmatter 结构化表单（slug/title/type/tags/sources 独立字段）
  - Markdown 工具栏（B I ` [] | table）
  - Ctrl+S 保存快捷键
  - 编辑/预览滚动同步
- **TOC 目录**：[WikiView.vue](file:///workspace/frontend/src/views/WikiView.vue) sticky 侧边栏，基于 h2/h3 自动生成
- **版本历史**：增加"历史版本"抽屉，支持版本间 diff 对比
- **元信息条**：页面头部下方显示 `更新于 · 版本 v3 · 审查状态 · 来源数`
- **树搜索**：树面板顶部增加搜索框，`default-expand-all` 改按需展开

#### 2.3 文档与搜索（P1-13~P1-15）

- **文档批量操作**：[DocumentsView.vue](file:///workspace/frontend/src/views/DocumentsView.vue) 增加多选 + 批量删除/编译；删除改 `NPopconfirm`
- **文档预览**：md 用 markdown 渲染，pdf 用 iframe
- **搜索高亮**：[SearchView.vue](file:///workspace/frontend/src/views/SearchView.vue) snippet 用 `<mark>` 高亮；结果可点击跳转；增加 type 筛选 + 排序

#### 2.4 设计系统补齐（P1-16~P1-20）

- **图标系统**：引入 `@vicons/ionicons5` + `NIcon`，替换全部 emoji（菜单/logo/统计卡圆点）
- **共享组件**：抽取 `PageHeader.vue` / `EmptyState.vue` / `LoadingState.vue` / `CodePreview.vue`
- **状态映射**：集中到 `@/utils/statusMap.ts`（severityTagType/statusTagType/nodeTypeColor）
- **SVG 可访问性**：[TopologyView.vue](file:///workspace/frontend/src/views/TopologyView.vue#L316) `<g>` 加 `tabindex="0"` `role="button"` `@keydown.enter`
- **表单规范**：[ExportView.vue](file:///workspace/frontend/src/views/ExportView.vue) 改用 NForm/NFormItem，NTag checkable 改 NRadioGroup/NCheckboxGroup
- **Naive UI themeOverrides**：[App.vue](file:///workspace/frontend/src/App.vue) 配置品牌色对齐令牌

### 阶段三：打磨优化（P2）

**目标**：提升细节品质与运维场景适配。

- 持久化：引入 `pinia-plugin-persistedstate`，暗色模式防 FOUC
- 仪表盘：统计卡加 sparkline 趋势 + 环比 delta + 点击下钻 + 错误重试 UI
- 健康检查：Lint 表加操作列（跳转编辑/忽略）、孤岛表加操作列（建立链接/归档）、批量操作、自动加载
- 交互统一：Drawer 宽度 `min(720px, 90vw)`、Modal `preset="card"`、危险操作一律 `type="error"` + NPopconfirm
- 路由过渡：`<Transition name="fade" mode="out-in">`
- 信息架构：知识入口合并、版本控制重新归类、增加"最近访问/收藏"
- 工具函数：抽取 `@/utils/format.ts`（formatFileSize 修 TB 越界、formatDate）
- OnboardingTour：Esc 关闭、方向键导航、`transition` 限定属性
- type 本地化：全视图统一 `typeLabelMap`

---

## 四、优先级矩阵

| 优先级 | 工作项 | 解除的痛点 | 复杂度 |
|--------|--------|-----------|--------|
| **P0** | 设计令牌层 + 深色模式修复 | 硬编码/主题失效 | 中 |
| **P0** | 响应式适配 | 移动端不可用 | 中 |
| **P0** | Wiki v-html sanitize + Tour 遮罩 | 安全/卡死 | 低 |
| **P1** | 图标系统 + 共享组件抽取 | 一致性/可访问性 | 中 |
| **P1** | 导航面包屑 + 骨架屏 + 路由加载 | 导航迷失/CLS | 中 |
| **P1** | Wiki 流式问答 + Markdown 渲染 | 核心交互体验 | 高 |
| **P1** | Wiki 编辑器补全 + 结构化 frontmatter | 双链质量/编辑效率 | 高 |
| **P1** | Wiki TOC + 版本历史 + 元信息 | 阅读体验 | 中 |
| **P1** | 文档批量操作 + 搜索高亮 | 效率/相关性 | 中 |
| **P1** | 表单规范 + SVG 可访问性 | 一致性/a11y | 低 |
| **P2** | 持久化 + 仪表盘可视化 + 交互统一 | 细节品质 | 中 |

---

## 五、关键权衡

1. **令牌层 vs 渐进迁移**：先建立令牌 + 修复 P0 深色模式（止血），再逐步迁移各视图硬编码（P1/P2），避免一次性大重构。
2. **流式问答 vs 实现成本**：SSE 需后端配合改造 `wiki_query`，但用户体验提升显著，建议作为 P1 重点。
3. **图标系统选型**：`@vicons/ionicons5` 与 Naive UI 同生态，迁移成本最低；若需更丰富图标可考虑 `lucide-vue-next`。
4. **Wiki 编辑器补全**：可基于 `NPopselect` + `listWikiPages` 缓存实现轻量补全，或引入 CodeMirror 6（功能强但依赖重）。建议先轻量方案。
5. **响应式 vs 运维场景**：OpsKG 主要面向桌面运维，但响应式是基础工程能力，至少需保证平板可用（768px+）。

---

## 六、参考资源

- [Naive UI 主题定制](https://www.naiveui.com/zh-CN/os-theme/docs/customize-theme)
- [WCAG 2.1 AA 对比度要求](https://www.w3.org/WAI/WCAG21/quickref/?showtechniques=141#contrast-minimum)
- [Vue 3 Suspense](https://vuejs.org/guide/built-ins/suspense.html)
- [DOMPurify](https://github.com/cure53/DOMPurify)

---

**总结**：OpsKG 前端组件库选型合理、路由权限扎实，但设计系统层"几乎空白"。建议按 P0 止血（令牌+深色+响应式+安全）→ P1 核心体验（图标+导航+Wiki 问答/编辑器/浏览）→ P2 打磨（持久化+可视化+交互统一）三阶段推进，优先解决响应式与深色模式短板，再补齐 Wiki 核心交互，最后打磨细节品质。
