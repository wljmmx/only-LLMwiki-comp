<script setup lang="ts">
import { computed, ref } from 'vue'
import { NInput, NTree, NCard, NSkeleton, NEmpty, NButton, NSpace, NSelect } from 'naive-ui'
import type { TreeOption, SelectOption } from 'naive-ui'
import type { WikiPage } from '@/types/api'

const props = defineProps<{
  pages: WikiPage[]
  treeLoading: boolean
  selectedKey: string | null
  treeSearchText: string
}>()

const emit = defineEmits<{
  'update:treeSearchText': [value: string]
  select: [key: string]
}>()

const typeLabelMap: Record<string, string> = {
  entity: '实体',
  concept: '概念',
  incident: '事件',
  runbook: '运行手册',
  service: '服务',
  host: '主机',
}

// P0: 排序选项
const sortBy = ref<'type' | 'date'>('type')

const sortOptions: SelectOption[] = [
  { label: '按类型分组', value: 'type' },
  { label: '按更新时间', value: 'date' },
]

// P0: 展开/折叠控制
const expandedKeys = ref<string[]>([])
const allExpanded = ref(true)

// P0: 初始化展开所有
function initExpandAll() {
  const keys: string[] = []
  Object.keys(typeLabelMap).forEach((type) => {
    keys.push(`type-${type}`)
  })
  expandedKeys.value = keys
  allExpanded.value = true
}

function toggleExpandAll() {
  if (allExpanded.value) {
    expandedKeys.value = []
    allExpanded.value = false
  } else {
    initExpandAll()
  }
}

const treeData = computed<TreeOption[]>(() => {
  const filter = props.treeSearchText.trim().toLowerCase()

  // P0: 排序逻辑
  const sorted = [...props.pages]
  if (sortBy.value === 'date') {
    sorted.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
  }

  if (sortBy.value === 'type') {
    // 按类型分组
    const grouped: Record<string, WikiPage[]> = {}
    sorted.forEach((page) => {
      if (!grouped[page.type]) {
        grouped[page.type] = []
      }
      grouped[page.type].push(page)
    })

    return Object.keys(grouped)
      .map((type) => {
        const children = grouped[type]
          .filter((page) => {
            if (!filter) return true
            return (
              page.title.toLowerCase().includes(filter) ||
              page.slug.toLowerCase().includes(filter) ||
              (page.tags || []).some((t) => t.toLowerCase().includes(filter))
            )
          })
          .map((page) => ({
            key: page.slug,
            label: page.title,
            isLeaf: true,
          }))

        if (children.length === 0) return null

        return {
          key: `type-${type}`,
          label: `${typeLabelMap[type] || type} (${children.length})`,
          isLeaf: false,
          children,
        }
      })
      .filter(Boolean) as TreeOption[]
  }

  // 按日期排序（扁平列表）
  const filtered = sorted.filter((page) => {
    if (!filter) return true
    return (
      page.title.toLowerCase().includes(filter) ||
      page.slug.toLowerCase().includes(filter) ||
      (page.tags || []).some((t) => t.toLowerCase().includes(filter))
    )
  })

  if (filtered.length === 0) return []

  return filtered.map((page) => ({
    key: page.slug,
    label: page.title,
    isLeaf: true,
  }))
})

// P0: 页面总数
const totalPages = computed(() => props.pages.length)

// P0: 各类型页面数量
const typeCounts = computed(() => {
  const counts: Record<string, number> = {}
  props.pages.forEach((p) => {
    counts[p.type] = (counts[p.type] || 0) + 1
  })
  return counts
})

function handleSelect(keys: string[]) {
  const key = keys[0] as string
  if (key && !key.startsWith('type-')) {
    emit('select', key)
  }
}

// 初始化展开
initExpandAll()
</script>

<template>
  <NCard class="tree-panel" size="small">
    <!-- P0: 页面标题栏，含计数与操作 -->
    <div class="tree-header">
      <NSpace justify="space-between" align="center">
        <span class="tree-title">Wiki 页面</span>
        <NSpace size="small">
          <NButton
            size="tiny"
            quaternary
            @click="toggleExpandAll"
          >
            {{ allExpanded ? '收起全部' : '展开全部' }}
          </NButton>
        </NSpace>
      </NSpace>
      <!-- P0: 页面计数 -->
      <div class="tree-counts">
        <span class="count-total">{{ totalPages }} 页</span>
        <span v-if="typeCounts.entity" class="count-type">实体 {{ typeCounts.entity }}</span>
        <span v-if="typeCounts.concept" class="count-type">概念 {{ typeCounts.concept }}</span>
        <span v-if="typeCounts.incident" class="count-type">事件 {{ typeCounts.incident }}</span>
        <span v-if="typeCounts.runbook" class="count-type">手册 {{ typeCounts.runbook }}</span>
        <span v-if="typeCounts.service" class="count-type">服务 {{ typeCounts.service }}</span>
        <span v-if="typeCounts.host" class="count-type">主机 {{ typeCounts.host }}</span>
      </div>
    </div>

    <!-- P0: 排序选择器 -->
    <NSelect
      v-model:value="sortBy"
      :options="sortOptions"
      size="small"
      class="tree-sort"
      placeholder="排序方式"
    />

    <NInput
      :value="treeSearchText"
      placeholder="搜索页面标题或标签..."
      clearable
      size="small"
      class="tree-search"
      @update:value="(val: string) => emit('update:treeSearchText', val)"
    />

    <div v-if="treeLoading" class="tree-skeleton">
      <NSkeleton text :repeat="8" :height="20" />
    </div>
    <NTree
      v-else
      :data="treeData"
      :selected-keys="selectedKey ? [selectedKey] : []"
      :expanded-keys="sortBy === 'type' ? expandedKeys : undefined"
      :default-expand-all="sortBy !== 'type'"
      block-line
      class="wiki-tree"
      @update:selected-keys="handleSelect"
      @update:expanded-keys="(keys: string[]) => expandedKeys = keys"
    />
    <NEmpty v-if="!treeLoading && pages.length === 0" description="暂无页面" />
  </NCard>
</template>

<style scoped>
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

/* P0: 页面计数 */
.tree-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.count-total {
  font-size: 12px;
  font-weight: 600;
  color: var(--n-primary-color, #3b82f6);
  padding: 2px 6px;
  background: var(--n-primary-color-suppl, #eff6ff);
  border-radius: 4px;
}

.count-type {
  font-size: 11px;
  color: var(--n-text-color-3, #9ca3af);
  padding: 2px 6px;
  background: var(--n-base-color, #f3f4f6);
  border-radius: 4px;
}

/* P0: 排序选择器 */
.tree-sort {
  margin-bottom: 8px;
}

.tree-search {
  margin-bottom: 8px;
}

.wiki-tree {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
  /* P1: CSS containment 实现浏览器级虚拟滚动，大列表时性能优化 */
  contain: layout style;
  content-visibility: auto;
}

/* P1: 树节点项启用 CSS containment，减少布局/绘制开销 */
/* contain-intrinsic-size 提供预估高度，配合 content-visibility 实现虚拟滚动效果 */
.wiki-tree :deep(.n-tree-node) {
  contain: layout style;
  content-visibility: auto;
  contain-intrinsic-size: auto 40px;
}

.tree-skeleton {
  padding: 8px 0;
}
</style>