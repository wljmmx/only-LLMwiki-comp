<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { NSplit, NButton, NIcon, NResult, NBreadcrumb, NBreadcrumbItem, NDrawer, NDrawerContent } from 'naive-ui'
import { MenuOutline, HomeOutline, ChevronForwardOutline } from '@vicons/ionicons5'
import { listWikiPages, getWikiPage, getWikiBacklinks } from '@/api/wiki'
import { renderWikiMarkdown } from '@/utils/wikiRender'
import { parseFrontmatter } from '@/utils/frontmatter'
import { getTypeLabel } from '@/utils/format'
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

// P0: 响应式侧边栏状态
const sidebarVisible = ref(true)
const mobileDrawerVisible = ref(false)

// P0: 页面加载错误状态
const pageError = ref<string | null>(null)

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

// P0: 重试加载页面
async function retryLoadPage() {
  if (selectedKey.value) {
    pageError.value = null
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
    version: currentPage.value.version ?? 0,
    reviewStatus,
    reviewStatusLabel: reviewStatusLabelMap[reviewStatus] || reviewStatus,
    sourcesCount: sources.length,
  }
})

// P0: 面包屑数据
const breadcrumbItems = computed(() => {
  const items = [{ label: 'Wiki', key: 'wiki-root' }]
  if (currentPage.value) {
    items.push({
      label: getTypeLabel(currentPage.value.type),
      key: `type-${currentPage.value.type}`,
    })
    items.push({
      label: currentPage.value.title,
      key: currentPage.value.slug,
    })
  }
  return items
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
  pageError.value = null
  try {
    const [page, bl] = await Promise.all([getWikiPage(slug), getWikiBacklinks(slug)])
    currentPage.value = page
    backlinks.value = bl
    // P2-6: 记录最近访问
    trackPage(page.slug, page.title, page.type)
  } catch (err: any) {
    pageError.value = err?.response?.data?.detail || err?.message || '加载页面失败'
    console.error(err)
  } finally {
    contentLoading.value = false
    backlinksLoading.value = false
  }
}

function handleSelect(key: string) {
  selectedKey.value = key
  // P0: 移动端选择后关闭抽屉
  mobileDrawerVisible.value = false
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

// P0: 切换侧边栏
function toggleSidebar() {
  sidebarVisible.value = !sidebarVisible.value
}

// P0: 移动端打开侧边栏抽屉
function openMobileDrawer() {
  mobileDrawerVisible.value = true
}

onMounted(() => {
  loadPages()
})
</script>

<template>
  <div class="wiki-view">
    <!-- P0: 面包屑导航 -->
    <div class="wiki-breadcrumb">
      <NBreadcrumb>
        <NBreadcrumbItem>
          <NIcon size="16" :component="HomeOutline" />
        </NBreadcrumbItem>
        <NBreadcrumbItem
          v-for="item in breadcrumbItems"
          :key="item.key"
        >
          {{ item.label }}
        </NBreadcrumbItem>
      </NBreadcrumb>
    </div>

    <!-- P0: 移动端侧边栏切换按钮 -->
    <div class="mobile-sidebar-toggle">
      <NButton quaternary size="small" @click="openMobileDrawer">
        <template #icon>
          <NIcon :component="MenuOutline" />
        </template>
        Wiki 页面
      </NButton>
    </div>

    <div class="wiki-layout">
      <!-- P0: 桌面端可折叠侧边栏 -->
      <div v-show="sidebarVisible" class="wiki-sidebar-desktop">
        <WikiSidebar
          :pages="pages"
          :tree-loading="treeLoading"
          :selected-key="selectedKey"
          :tree-search-text="treeSearchText"
          @update:tree-search-text="(val: string) => treeSearchText = val"
          @select="handleSelect"
        />
      </div>

      <!-- P0: 侧边栏折叠按钮 -->
      <div class="sidebar-toggle-btn">
        <NButton
          quaternary
          size="tiny"
          @click="toggleSidebar"
        >
          <template #icon>
            <NIcon
              :component="ChevronForwardOutline"
              :style="{ transform: sidebarVisible ? 'rotate(0deg)' : 'rotate(180deg)' }"
            />
          </template>
        </NButton>
      </div>

      <!-- P0: 错误状态 -->
      <div v-if="pageError" class="wiki-error">
        <NResult
          status="error"
          title="页面加载失败"
          :description="pageError"
        >
          <template #footer>
            <NButton type="primary" @click="retryLoadPage">
              重试
            </NButton>
          </template>
        </NResult>
      </div>

      <!-- 正常内容 -->
      <WikiContent
        v-else
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
    </div>

    <!-- P0: 移动端侧边栏抽屉 -->
    <NDrawer v-model:show="mobileDrawerVisible" :width="280" placement="left">
      <NDrawerContent title="Wiki 页面" :closable="true">
        <WikiSidebar
          :pages="pages"
          :tree-loading="treeLoading"
          :selected-key="selectedKey"
          :tree-search-text="treeSearchText"
          @update:tree-search-text="(val: string) => treeSearchText = val"
          @select="handleSelect"
        />
      </NDrawerContent>
    </NDrawer>

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
  display: flex;
  flex-direction: column;
}

/* P0: 面包屑 */
.wiki-breadcrumb {
  padding: 8px 16px;
  background: var(--n-card-color, #fff);
  border-bottom: 1px solid var(--n-border-color, #e5e7eb);
  flex-shrink: 0;
}

/* P0: 移动端侧边栏切换按钮 */
.mobile-sidebar-toggle {
  display: none;
  padding: 8px 16px;
  background: var(--n-card-color, #fff);
  border-bottom: 1px solid var(--n-border-color, #e5e7eb);
  flex-shrink: 0;
}

.wiki-layout {
  flex: 1;
  display: flex;
  min-height: 0;
  position: relative;
}

/* P0: 内容区域填充剩余空间 */
.wiki-layout > :deep(.n-card) {
  flex: 1;
  min-width: 0;
}

/* P0: 桌面端侧边栏 */
.wiki-sidebar-desktop {
  width: 280px;
  flex-shrink: 0;
  border-right: 1px solid var(--n-border-color, #e5e7eb);
  background: var(--n-card-color, #fff);
}

/* P0: 侧边栏折叠按钮 */
.sidebar-toggle-btn {
  position: absolute;
  left: 280px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 10;
  transition: left 0.2s ease;
}

/* P0: 错误状态 */
.wiki-error {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* P0: 响应式布局 */
@media (max-width: 768px) {
  .wiki-breadcrumb {
    display: none;
  }
  .mobile-sidebar-toggle {
    display: flex;
  }
  .wiki-sidebar-desktop {
    display: none;
  }
  .sidebar-toggle-btn {
    display: none;
  }
  .wiki-layout {
    flex-direction: column;
  }
}
</style>