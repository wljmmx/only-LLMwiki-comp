<script setup lang="ts">
import { ref, computed } from 'vue'
import { NCard, NTag, NSpace, NSkeleton, NEmpty, NThing, NButton, NTooltip, NDivider } from 'naive-ui'
import CollabPanel from '@/components/collab/CollabPanel.vue'
import WikiEditor from '@/components/wiki/WikiEditor.vue'
import WikiToc from '@/components/wiki/WikiToc.vue'
import { parseSlugFromHash } from '@/utils/wikiRender'
import { formatDate } from '@/utils/format'
import type { WikiPage, BacklinkItem } from '@/types/api'

const props = defineProps<{
  currentPage: WikiPage | null
  contentLoading: boolean
  backlinks: BacklinkItem[]
  isEditing: boolean
  hasLock: boolean
  selectedKey: string | null
  renderedContent: string
  pageMeta: {
    updatedAt: string
    version: number
    reviewStatus: string
    reviewStatusLabel: string
    sourcesCount: number
  } | null
}>()

// P0: 估算阅读时间（中文约 400 字/分钟，英文约 200 词/分钟）
const readingTime = computed(() => {
  if (!props.currentPage?.content) return 0
  const text = props.currentPage.content
  // 粗略估算：按字符数 / 400 计算中文阅读分钟数
  const chars = text.replace(/\s/g, '').length
  const minutes = Math.max(1, Math.ceil(chars / 400))
  return minutes
})

const emit = defineEmits<{
  'start-editing': []
  'cancel-editing': []
  saved: []
  'backlink-click': [slug: string]
  'content-click': [slug: string]
  'toggle-version-history': []
  'lock-change': [payload: { hasLock: boolean; lockHolder: string | null }]
}>()

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

const pageContentRef = ref<HTMLElement | null>(null)

function handleContentClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (target.tagName === 'A') {
    const href = target.getAttribute('href') || ''
    const slug = parseSlugFromHash(href)
    if (slug) {
      e.preventDefault()
      emit('content-click', slug)
    }
  }
}
</script>

<template>
  <NCard class="content-panel" size="large">
    <div v-if="contentLoading" class="content-skeleton">
      <NSkeleton text :width="280" :height="32" style="margin-bottom: 16px" />
      <NSkeleton text :width="200" :height="14" style="margin-bottom: 24px" />
      <NSkeleton text :repeat="6" style="margin-bottom: 12px" />
      <NSkeleton text width="60%" />
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
      <div v-if="pageMeta" class="page-meta-bar">
        <!-- P0: 阅读时间 -->
        <NTooltip trigger="hover">
          <template #trigger>
            <span class="meta-item" tabindex="0" title="预计阅读时间">阅读 ~{{ readingTime }} 分钟</span>
          </template>
          预计阅读时间，基于内容字符数估算
        </NTooltip>
        <span class="meta-sep" aria-hidden="true">·</span>
        <NTooltip trigger="hover">
          <template #trigger>
            <span class="meta-item" tabindex="0" title="页面最后更新时间">更新于 {{ formatDate(pageMeta.updatedAt) }}</span>
          </template>
          页面最后更新时间
        </NTooltip>
        <span class="meta-sep" aria-hidden="true">·</span>
        <NTooltip trigger="hover">
          <template #trigger>
            <span class="meta-item" tabindex="0" title="页面版本号">v{{ pageMeta.version ?? 1 }}</span>
          </template>
          页面版本号
        </NTooltip>
        <span class="meta-sep" aria-hidden="true">·</span>
        <NTooltip trigger="hover">
          <template #trigger>
            <span class="meta-item" tabindex="0" title="审查状态">{{ pageMeta.reviewStatusLabel }}</span>
          </template>
          审查状态
        </NTooltip>
        <span class="meta-sep" aria-hidden="true">·</span>
        <NTooltip trigger="hover">
          <template #trigger>
            <span class="meta-item" tabindex="0" title="引用的原始文档数量">来源 {{ pageMeta.sourcesCount }}</span>
          </template>
          引用的原始文档数量
        </NTooltip>
      </div>
      <CollabPanel
        v-if="selectedKey"
        :key="selectedKey"
        :slug="selectedKey"
        class="collab-panel-wrapper"
        @lock-change="(payload: { hasLock: boolean; lockHolder: string | null }) => emit('lock-change', payload)"
      />
      <div v-if="!isEditing" class="page-toolbar">
        <NButton
          size="small"
          type="primary"
          :disabled="!hasLock"
          @click="emit('start-editing')"
        >
          {{ hasLock ? '编辑' : '需先申请编辑锁' }}
        </NButton>
        <NButton
          size="small"
          quaternary
          @click="emit('toggle-version-history')"
        >
          历史记录
        </NButton>
      </div>
      <WikiEditor
        v-if="isEditing && currentPage"
        :slug="currentPage.slug"
        :content="currentPage.content"
        :version="currentPage.version"
        :can-edit="hasLock"
        @saved="emit('saved')"
        @cancel="emit('cancel-editing')"
        class="editor-wrapper"
      />
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
                role="button"
                tabindex="0"
                :aria-label="`跳转到反向链接: ${bl.title}`"
                @click="emit('backlink-click', bl.slug)"
                @keydown.enter="emit('backlink-click', bl.slug)"
                @keydown.space.prevent="emit('backlink-click', bl.slug)"
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

<style scoped>
.content-panel {
  height: 100%;
  box-sizing: border-box;
  overflow-y: auto;
}

.content-panel :deep(.n-card__content) {
  padding: 32px 40px;
}

.content-skeleton {
  padding: 8px 0;
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

/* P0: 表格样式增强 */
.page-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
  font-size: 14px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--n-border-color, #e5e7eb);
}

.page-content :deep(th) {
  background: var(--n-color-target, #f0f5ff);
  color: var(--n-text-color, #111827);
  font-weight: 600;
  padding: 10px 14px;
  text-align: left;
  border-bottom: 2px solid var(--n-border-color, #d1d5db);
  font-size: 13px;
}

.page-content :deep(td) {
  padding: 10px 14px;
  border-bottom: 1px solid var(--n-border-color, #e5e7eb);
  color: var(--n-text-color, #1f2937);
  vertical-align: top;
}

.page-content :deep(tr:nth-child(even)) {
  background: var(--n-base-color, #f9fafb);
}

.page-content :deep(tr:hover) {
  background: var(--n-color-target, #f0f5ff);
}

/* P0: 代码块语法高亮样式 */
.page-content :deep(pre) {
  background: #1e1e2e;
  color: #cdd6f4;
  padding: 16px 20px;
  border-radius: 8px;
  overflow-x: auto;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  margin: 16px 0;
  border: 1px solid var(--n-border-color, #e5e7eb);
}

.page-content :deep(pre code) {
  background: transparent;
  padding: 0;
  color: inherit;
  font-size: inherit;
  border-radius: 0;
}

/* P0: 行内代码保持浅色 */
.page-content :deep(code) {
  background: var(--n-color-info-weak, #eff6ff);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
  color: var(--n-text-color, #1f2937);
}

/* P0: blockquote 样式 */
.page-content :deep(blockquote) {
  border-left: 4px solid var(--n-primary-color, #3b82f6);
  margin: 16px 0;
  padding: 8px 16px;
  background: var(--n-color-target, #f0f5ff);
  border-radius: 0 6px 6px 0;
  color: var(--n-text-color-2, #4b5563);
}

.page-content :deep(blockquote p) {
  margin: 6px 0;
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