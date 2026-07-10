<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { NSplit, NTree, NCard, NTag, NSpace, NSpin, NEmpty, NThing, NButton, NTooltip } from 'naive-ui'
import type { TreeOption } from 'naive-ui'
import { listWikiPages, getWikiPage, getWikiBacklinks } from '@/api/wiki'
import { renderWikiMarkdown, parseSlugFromHash } from '@/utils/wikiRender'
import { parseFrontmatter } from '@/utils/frontmatter'
import type { WikiPage, BacklinkItem } from '@/types/api'
// S16-1：协作面板（实时在线用户 + 编辑锁状态）
import CollabPanel from '@/components/collab/CollabPanel.vue'
// S16-2：Wiki 页面编辑器
import WikiEditor from '@/components/wiki/WikiEditor.vue'
// P1-6：页面目录大纲
import WikiToc from '@/components/wiki/WikiToc.vue'
// P1-11：版本历史抽屉
import WikiVersionHistory from '@/components/wiki/WikiVersionHistory.vue'

const treeLoading = ref(true)
const contentLoading = ref(false)
const backlinksLoading = ref(false)
const pages = ref<WikiPage[]>([])
const currentPage = ref<WikiPage | null>(null)
const backlinks = ref<BacklinkItem[]>([])
const selectedKey = ref<string | null>(null)

/** P1-12a: 读取 ?slug= query 支持外部跳转（如 WikiHealthView 的"编辑"按钮、WikiQueryView 的引用来源） */
const route = useRoute()

// S16-2：编辑模式状态
const isEditing = ref(false)
const hasLock = ref(false)
const lockHolder = ref<string | null>(null)

// P1-6：页面内容 DOM 引用（供 TOC 提取标题）
const pageContentRef = ref<HTMLElement | null>(null)

// P1-11：版本历史抽屉
const showVersionHistory = ref(false)

/** P1-11：回滚后刷新当前页面 */
async function handleVersionRollback() {
  if (selectedKey.value) {
    await loadPage(selectedKey.value)
  }
}

// S16-2：CollabPanel 锁状态变化回调
function handleLockChange(payload: { hasLock: boolean; lockHolder: string | null }) {
  hasLock.value = payload.hasLock
  lockHolder.value = payload.lockHolder
}

// S16-2：进入编辑模式
function startEditing() {
  isEditing.value = true
}

// S16-2：退出编辑模式
function cancelEditing() {
  isEditing.value = false
}

// S16-2：保存成功后刷新页面内容
async function handleSaved() {
  isEditing.value = false
  if (selectedKey.value) {
    await loadPage(selectedKey.value)
  }
}

const typeLabelMap: Record<string, string> = {
  entity: '实体',
  concept: '概念',
  incident: '事件',
  runbook: '运行手册',
  service: '服务',
  host: '主机',
}

const typeTagTypeMap: Record<
  string,
  'default' | 'info' | 'success' | 'warning' | 'error' | 'primary'
> = {
  entity: 'primary',
  concept: 'info',
  incident: 'error',
  runbook: 'success',
  service: 'warning',
  host: 'default',
}

const treeData = computed<TreeOption[]>(() => {
  const grouped: Record<string, WikiPage[]> = {}
  pages.value.forEach((page) => {
    if (!grouped[page.type]) {
      grouped[page.type] = []
    }
    grouped[page.type].push(page)
  })

  return Object.keys(grouped).map((type) => ({
    key: `type-${type}`,
    label: typeLabelMap[type] || type,
    isLeaf: false,
    children: grouped[type].map((page) => ({
      key: page.slug,
      label: page.title,
      isLeaf: true,
    })),
  }))
})

function renderSimpleMarkdown(text: string): string {
  return renderWikiMarkdown(text)
}

const renderedContent = computed(() => {
  if (!currentPage.value) return ''
  return renderSimpleMarkdown(currentPage.value.content)
})

// P1-6：页面元信息（从 frontmatter 解析 review_status / sources）
const reviewStatusLabelMap: Record<string, string> = {
  auto: '自动审查',
  review_needed: '待审查',
  approved: '已审查',
}

const pageMeta = computed(() => {
  if (!currentPage.value) return null
  const parsed = parseFrontmatter(currentPage.value.content)
  const reviewStatus = typeof parsed.rest.review_status === 'string'
    ? parsed.rest.review_status
    : 'auto'
  const sources = Array.isArray(parsed.rest.sources) ? parsed.rest.sources : []
  return {
    updatedAt: currentPage.value.updated_at,
    version: currentPage.value.version,
    reviewStatus,
    reviewStatusLabel: reviewStatusLabelMap[reviewStatus] || reviewStatus,
    sourcesCount: sources.length,
  }
})

/** P1-6: 格式化日期为 YYYY-MM-DD */
function formatDate(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  } catch {
    return iso
  }
}

async function loadPages() {
  treeLoading.value = true
  try {
    const res = await listWikiPages()
    pages.value = res.pages
    if (pages.value.length > 0 && !selectedKey.value) {
      // P1-12a: 优先选中 ?slug= query 指定的页面（支持外部跳转），否则首个
      const querySlug = typeof route.query.slug === 'string' ? route.query.slug : null
      const targetSlug =
        querySlug && pages.value.some((p) => p.slug === querySlug)
          ? querySlug
          : pages.value[0].slug
      selectedKey.value = targetSlug
      await loadPage(targetSlug)
    }
  } finally {
    treeLoading.value = false
  }
}

async function loadPage(slug: string) {
  contentLoading.value = true
  backlinksLoading.value = true
  try {
    const [page, bl] = await Promise.all([getWikiPage(slug), getWikiBacklinks(slug)])
    currentPage.value = page
    backlinks.value = bl
  } finally {
    contentLoading.value = false
    backlinksLoading.value = false
  }
}

function handleSelect(key: string) {
  if (key.startsWith('type-')) return
  selectedKey.value = key
  loadPage(key)
}

function handleBacklinkClick(slug: string) {
  selectedKey.value = slug
  loadPage(slug)
}

function handleContentClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (target.tagName === 'A') {
    const href = target.getAttribute('href') || ''
    const slug = parseSlugFromHash(href)
    if (slug) {
      e.preventDefault()
      selectedKey.value = slug
      loadPage(slug)
    }
  }
}

onMounted(() => {
  loadPages()
})
</script>

<template>
  <div class="wiki-view">
    <NSplit :default-size="280" :min-size="200" :max-size="400">
      <template #1>
        <NCard class="tree-panel" size="small">
          <div class="tree-header">
            <span class="tree-title">Wiki 页面</span>
          </div>
          <NSpin v-if="treeLoading" class="tree-loading" />
          <NTree
            v-else
            :data="treeData"
            :selected-keys="selectedKey ? [selectedKey] : []"
            :default-expand-all="true"
            block-line
            class="wiki-tree"
            @update:selected-keys="(keys) => handleSelect(keys[0] as string)"
          />
          <NEmpty v-if="!treeLoading && pages.length === 0" description="暂无页面" />
        </NCard>
      </template>
      <template #2>
        <NCard class="content-panel" size="large">
          <div v-if="contentLoading" class="content-loading">
            <NSpin size="large" />
          </div>
          <template v-else-if="currentPage">
            <div class="page-header">
              <h1 class="page-title">{{ currentPage.title }}</h1>
              <NSpace :size="12" class="page-meta">
                <NTag :type="typeTagTypeMap[currentPage.type] || 'default'" size="medium">
                  {{ typeLabelMap[currentPage.type] || currentPage.type }}
                </NTag>
                <template v-if="currentPage.tags && currentPage.tags.length > 0">
                  <NTag v-for="tag in currentPage.tags" :key="tag" type="info" size="medium">
                    #{{ tag }}
                  </NTag>
                </template>
              </NSpace>
            </div>
            <!-- P1-6：页面元信息条（更新时间 · 版本 · 审查状态 · 来源数） -->
            <div v-if="pageMeta" class="page-meta-bar">
              <NTooltip trigger="hover">
                <template #trigger>
                  <span class="meta-item">更新于 {{ formatDate(pageMeta.updatedAt) }}</span>
                </template>
                页面最后更新时间
              </NTooltip>
              <span class="meta-sep">·</span>
              <NTooltip trigger="hover">
                <template #trigger>
                  <span class="meta-item">v{{ pageMeta.version ?? 1 }}</span>
                </template>
                页面版本号
              </NTooltip>
              <span class="meta-sep">·</span>
              <NTooltip trigger="hover">
                <template #trigger>
                  <span class="meta-item">{{ pageMeta.reviewStatusLabel }}</span>
                </template>
                审查状态
              </NTooltip>
              <span class="meta-sep">·</span>
              <NTooltip trigger="hover">
                <template #trigger>
                  <span class="meta-item">来源 {{ pageMeta.sourcesCount }}</span>
                </template>
                引用的原始文档数量
              </NTooltip>
            </div>
            <!-- S16-1：协作面板（随 selectedKey 变化重建，触发 useCollab 重连） -->
            <CollabPanel
              v-if="selectedKey"
              :key="selectedKey"
              :slug="selectedKey"
              class="collab-panel-wrapper"
              @lock-change="handleLockChange"
            />
            <!-- S16-2：编辑模式切换 -->
            <div v-if="!isEditing" class="page-toolbar">
              <NButton
                size="small"
                type="primary"
                :disabled="!hasLock"
                @click="startEditing"
              >
                {{ hasLock ? '编辑' : '需先申请编辑锁' }}
              </NButton>
              <!-- P1-11：版本历史入口 -->
              <NButton
                size="small"
                quaternary
                @click="showVersionHistory = true"
              >
                历史记录
              </NButton>
            </div>
            <!-- S16-2：WikiEditor 替代只读内容区 -->
            <WikiEditor
              v-if="isEditing && currentPage"
              :slug="currentPage.slug"
              :content="currentPage.content"
              :version="currentPage.version"
              :can-edit="hasLock"
              @saved="handleSaved"
              @cancel="cancelEditing"
              class="editor-wrapper"
            />
            <!-- P1-6：只读内容区 + TOC 目录（右侧） -->
            <div v-else class="content-body">
              <div class="content-main">
                <div
                  ref="pageContentRef"
                  class="page-content"
                  @click="handleContentClick"
                  v-html="renderedContent"
                ></div>
                <div v-if="backlinks.length > 0" class="backlinks-section">
                  <div class="backlinks-title">反向链接</div>
                  <div class="backlinks-list">
                    <NThing
                      v-for="bl in backlinks"
                      :key="bl.slug"
                      class="backlink-item"
                      :title="bl.title"
                      :description="bl.context"
                      @click="handleBacklinkClick(bl.slug)"
                    />
                  </div>
                </div>
              </div>
              <WikiToc
                :content-el="pageContentRef"
                :page-key="currentPage.slug"
                class="content-toc"
              />
            </div>
          </template>
          <NEmpty v-else description="请选择一个页面" />
        </NCard>
      </template>
    </NSplit>
    <!-- P1-11：版本历史抽屉 -->
    <WikiVersionHistory
      v-model:show="showVersionHistory"
      :slug="selectedKey || ''"
      @rollback="handleVersionRollback"
    />
  </div>
</template>

<style scoped>
.wiki-view {
  height: 100%;
  width: 100%;
}

.tree-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}

.tree-panel :deep(.n-card__content) {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  padding: 12px;
}

.tree-header {
  padding: 8px 4px 12px;
  border-bottom: 1px solid var(--n-border-color, #e5e7eb);
  margin-bottom: 8px;
}

.tree-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
}

.wiki-tree {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
}

.tree-loading {
  display: flex;
  justify-content: center;
  padding: 40px 0;
}

.content-panel {
  height: 100%;
  box-sizing: border-box;
  overflow-y: auto;
}

.content-panel :deep(.n-card__content) {
  padding: 32px 40px;
}

.content-loading {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 300px;
}

.page-header {
  margin-bottom: 28px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--n-border-color, #e5e7eb);
}

.page-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--n-text-color, #111827);
  margin: 0 0 16px 0;
  line-height: 1.3;
}

.page-meta {
  flex-wrap: wrap;
}

/* P1-6：页面元信息条 */
.page-meta-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-bottom: 24px;
  padding: 8px 0;
  font-size: 13px;
  color: var(--n-text-color-3, #6b7280);
}

.meta-item {
  display: inline-flex;
  align-items: center;
  cursor: help;
}

.meta-sep {
  color: var(--n-text-color-3, #d1d5db);
  margin: 0 2px;
}

/* P1-6：内容主体 + TOC 双栏布局 */
.content-body {
  display: flex;
  gap: 24px;
  align-items: flex-start;
}

.content-main {
  flex: 1;
  min-width: 0;
}

.content-toc {
  flex: 0 0 220px;
  width: 220px;
}

@media (max-width: 1024px) {
  .content-body {
    flex-direction: column;
  }
  .content-toc {
    display: none;
  }
}

.page-content {
  font-size: 15px;
  line-height: 1.8;
  color: var(--n-text-color, #1f2937);
}

.collab-panel-wrapper {
  margin-bottom: 24px;
}

.page-toolbar {
  margin-bottom: 16px;
}

.editor-wrapper {
  margin-bottom: 24px;
}

.page-content :deep(h1) {
  font-size: 24px;
  font-weight: 600;
  margin: 24px 0 16px;
  color: var(--n-text-color, #111827);
}

.page-content :deep(h2) {
  font-size: 20px;
  font-weight: 600;
  margin: 20px 0 12px;
  color: var(--n-text-color, #111827);
}

.page-content :deep(h3) {
  font-size: 17px;
  font-weight: 600;
  margin: 16px 0 10px;
  color: var(--n-text-color, #111827);
}

.page-content :deep(p) {
  margin: 12px 0;
}

.page-content :deep(code) {
  background: var(--n-color-info-weak, #eff6ff);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
  color: var(--n-text-color, #1f2937);
}

.page-content :deep(ul) {
  margin: 12px 0;
  padding-left: 24px;
}

.page-content :deep(li) {
  margin: 6px 0;
}

.page-content :deep(a) {
  color: var(--n-primary-color, #3b82f6);
  text-decoration: none;
}

.page-content :deep(a:hover) {
  text-decoration: underline;
}

.backlinks-section {
  margin-top: 40px;
  padding-top: 24px;
  border-top: 1px solid var(--n-border-color, #e5e7eb);
}

.backlinks-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
  margin-bottom: 16px;
}

.backlinks-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.backlink-item {
  padding: 12px 16px;
  border-radius: 8px;
  cursor: pointer;
  transition: background-color 0.2s;
  border: 1px solid var(--n-border-color, #e5e7eb);
}

.backlink-item:hover {
  background-color: var(--n-item-color-hover, #f3f4f6);
}
</style>
