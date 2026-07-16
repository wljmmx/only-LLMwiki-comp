<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { NInput, NModal, NScrollbar } from 'naive-ui'
import { useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'

interface SearchResult {
  title: string
  path: string
  group: string
}

const appStore = useAppStore()
const router = useRouter()

const showModal = ref(false)
const searchQuery = ref('')
const collapsed = computed(() => appStore.sidebarCollapsed)

// 从路由表构建搜索索引
const searchIndex = computed<SearchResult[]>(() => {
  const routes = router.getRoutes()
  const results: SearchResult[] = []

  for (const r of routes) {
    if (r.meta?.title && r.path && !r.path.includes(':') && r.path !== '/') {
      // 获取菜单组名
      const group = (r.meta.menuGroup as string) || ''
      results.push({
        title: r.meta.title as string,
        path: r.path,
        group,
      })
    }
  }
  return results
})

const filteredResults = computed(() => {
  if (!searchQuery.value.trim()) return searchIndex.value
  const q = searchQuery.value.toLowerCase()
  return searchIndex.value.filter(
    (r) =>
      r.title.toLowerCase().includes(q) ||
      r.group.toLowerCase().includes(q) ||
      r.path.toLowerCase().includes(q),
  )
})

function open() {
  showModal.value = true
  searchQuery.value = ''
  // 聚焦输入框
  setTimeout(() => {
    const input = document.querySelector('.global-search-input input') as HTMLInputElement
    input?.focus()
  }, 50)
}

function close() {
  showModal.value = false
}

function navigateTo(path: string) {
  router.push(path)
  close()
}

// 全局快捷键 Cmd+K / Ctrl+K
function handleKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault()
    open()
  }
  if (e.key === 'Escape' && showModal.value) {
    close()
  }
}

watch(showModal, (val) => {
  if (val) {
    document.addEventListener('keydown', handleKeydown)
  } else {
    document.removeEventListener('keydown', handleKeydown)
  }
})

// 组件挂载时注册全局快捷键
if (typeof document !== 'undefined') {
  document.addEventListener('keydown', handleKeydown)
}
</script>

<template>
  <!-- 搜索输入框 -->
  <div class="search-bar" :class="{ collapsed }">
    <NInput
      v-if="!collapsed"
      placeholder="搜索页面… (Cmd+K)"
      readonly
      size="small"
      class="global-search-input"
      @click="open"
    >
      <template #prefix>
        <span class="search-icon">🔍</span>
      </template>
      <template #suffix>
        <kbd class="kbd-hint">⌘K</kbd>
      </template>
    </NInput>
    <div v-else class="search-icon-collapsed" @click="open" title="搜索页面 (Cmd+K)">
      🔍
    </div>
  </div>

  <!-- 搜索弹窗 -->
  <NModal
    :show="showModal"
    :mask-closable="true"
    @update:show="(v: boolean) => { if (!v) close() }"
    transform-origin="center"
    :auto-focus="true"
  >
    <div class="search-modal" role="dialog" aria-label="全局搜索">
      <NInput
        v-model:value="searchQuery"
        placeholder="搜索页面…"
        size="large"
        class="search-modal-input"
        :autofocus="true"
        clearable
        @keydown.enter="filteredResults[0] && navigateTo(filteredResults[0].path)"
      >
        <template #prefix>
          <span class="search-icon">🔍</span>
        </template>
      </NInput>
      <NScrollbar v-if="filteredResults.length > 0" style="max-height: 360px">
        <div class="search-results">
          <div
            v-for="result in filteredResults"
            :key="result.path"
            class="search-result-item"
            @click="navigateTo(result.path)"
          >
            <span class="result-title">{{ result.title }}</span>
            <span class="result-group">{{ result.group }}</span>
          </div>
        </div>
      </NScrollbar>
      <div v-else-if="searchQuery" class="search-empty">
        未找到匹配 "{{ searchQuery }}" 的页面
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.search-bar {
  padding: 8px 12px;
  border-bottom: 1px solid var(--opskg-border-color);
}
.search-bar.collapsed {
  padding: 8px 0;
  display: flex;
  justify-content: center;
}
.search-icon-collapsed {
  cursor: pointer;
  font-size: 18px;
  padding: 4px;
  border-radius: 4px;
  transition: background 0.2s;
}
.search-icon-collapsed:hover {
  background: var(--opskg-bg-hover);
}
.kbd-hint {
  font-size: 11px;
  padding: 1px 5px;
  border: 1px solid var(--opskg-border-color);
  border-radius: 3px;
  opacity: 0.6;
  font-family: inherit;
}
.search-modal {
  width: 480px;
  max-width: 90vw;
  background: var(--opskg-bg-card);
  border-radius: 12px;
  padding: 16px;
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.2);
}
.search-modal-input {
  margin-bottom: 12px;
}
.search-results {
  display: flex;
  flex-direction: column;
}
.search-result-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}
.search-result-item:hover {
  background: var(--opskg-bg-hover);
}
.result-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--opskg-text-1);
}
.result-group {
  font-size: 12px;
  color: var(--opskg-text-3);
  margin-left: 12px;
  white-space: nowrap;
}
.search-empty {
  text-align: center;
  padding: 24px;
  color: var(--opskg-text-3);
  font-size: 14px;
}
.search-icon {
  opacity: 0.5;
}
</style>