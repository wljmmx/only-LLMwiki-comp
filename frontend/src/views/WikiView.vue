<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  NSplit,
  NTree,
  NCard,
  NTag,
  NSpace,
  NSpin,
  NEmpty,
  NThing,
} from 'naive-ui'
import type { TreeOption } from 'naive-ui'
import { listWikiPages, getWikiPage, getWikiBacklinks } from '@/api/wiki'
import { renderWikiMarkdown, parseSlugFromHash } from '@/utils/wikiRender'
import type { WikiPage, BacklinkItem } from '@/types/api'

const treeLoading = ref(true)
const contentLoading = ref(false)
const backlinksLoading = ref(false)
const pages = ref<WikiPage[]>([])
const currentPage = ref<WikiPage | null>(null)
const backlinks = ref<BacklinkItem[]>([])
const selectedKey = ref<string | null>(null)

const typeLabelMap: Record<string, string> = {
  entity: '实体',
  concept: '概念',
  incident: '事件',
  runbook: '运行手册',
  service: '服务',
  host: '主机',
}

const typeTagTypeMap: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error' | 'primary'> = {
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

async function loadPages() {
  treeLoading.value = true
  try {
    const res = await listWikiPages()
    pages.value = res.pages
    if (pages.value.length > 0 && !selectedKey.value) {
      selectedKey.value = pages.value[0].slug
      await loadPage(pages.value[0].slug)
    }
  } finally {
    treeLoading.value = false
  }
}

async function loadPage(slug: string) {
  contentLoading.value = true
  backlinksLoading.value = true
  try {
    const [page, bl] = await Promise.all([
      getWikiPage(slug),
      getWikiBacklinks(slug),
    ])
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
            @update:selected-keys="(keys) => handleSelect(keys[0] as string)"
            class="wiki-tree"
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
            <div class="page-content" v-html="renderedContent" @click="handleContentClick"></div>
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
          </template>
          <NEmpty v-else description="请选择一个页面" />
        </NCard>
      </template>
    </NSplit>
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

.page-content {
  font-size: 15px;
  line-height: 1.8;
  color: var(--n-text-color, #1f2937);
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
