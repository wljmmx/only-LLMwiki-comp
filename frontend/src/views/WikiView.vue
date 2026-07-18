<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { NSplit } from 'naive-ui'
import { listWikiPages, getWikiPage, getWikiBacklinks } from '@/api/wiki'
import { renderWikiMarkdown } from '@/utils/wikiRender'
import { parseFrontmatter } from '@/utils/frontmatter'
import type { WikiPage, BacklinkItem } from '@/types/api'
import WikiSidebar from '@/components/wiki/WikiSidebar.vue'
import WikiContent from '@/components/wiki/WikiContent.vue'
import WikiVersionHistory from '@/components/wiki/WikiVersionHistory.vue'
import { useRecentPages } from '@/composables/useRecentPages'

const { trackPage } = useRecentPages()

const treeLoading = ref(true)
const contentLoading = ref(false)
const backlinksLoading = ref(false)
const pages = ref<WikiPage[]>([])
const currentPage = ref<WikiPage | null>(null)
const backlinks = ref<BacklinkItem[]>([])
const selectedKey = ref<string | null>(null)

const route = useRoute()

// P2-10: Wiki 树搜索过滤
const treeSearchText = ref('')

// S16-2：编辑模式状态
const isEditing = ref(false)
const hasLock = ref(false)
const lockHolder = ref<string | null>(null)

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
    // P2-6: 记录最近访问
    trackPage(page.slug, page.title, page.type)
  } finally {
    contentLoading.value = false
    backlinksLoading.value = false
  }
}

function handleSelect(key: string) {
  selectedKey.value = key
  loadPage(key)
}

function handleBacklinkClick(slug: string) {
  selectedKey.value = slug
  loadPage(slug)
}

function handleContentClick(slug: string) {
  selectedKey.value = slug
  loadPage(slug)
}

onMounted(() => {
  loadPages()
})
</script>

<template>
  <div class="wiki-view">
    <NSplit :default-size="280" :min-size="200" :max-size="400">
      <template #1>
        <WikiSidebar
          :pages="pages"
          :tree-loading="treeLoading"
          :selected-key="selectedKey"
          :tree-search-text="treeSearchText"
          @update:tree-search-text="(val: string) => treeSearchText = val"
          @select="handleSelect"
        />
      </template>
      <template #2>
        <WikiContent
          :current-page="currentPage"
          :content-loading="contentLoading"
          :backlinks="backlinks"
          :is-editing="isEditing"
          :has-lock="hasLock"
          :selected-key="selectedKey"
          :rendered-content="renderedContent"
          :page-meta="pageMeta"
          @start-editing="startEditing"
          @cancel-editing="cancelEditing"
          @saved="handleSaved"
          @backlink-click="handleBacklinkClick"
          @content-click="handleContentClick"
          @toggle-version-history="showVersionHistory = true"
          @lock-change="handleLockChange"
        />
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
</style>