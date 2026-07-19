<script setup lang="ts">
import { computed } from 'vue'
import { NInput, NTree, NCard, NSkeleton, NEmpty } from 'naive-ui'
import type { TreeOption } from 'naive-ui'
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

const treeData = computed<TreeOption[]>(() => {
  const grouped: Record<string, WikiPage[]> = {}
  props.pages.forEach((page) => {
    if (!grouped[page.type]) {
      grouped[page.type] = []
    }
    grouped[page.type].push(page)
  })

  const filter = props.treeSearchText.trim().toLowerCase()

  return Object.keys(grouped).map((type) => {
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
  }).filter(Boolean) as TreeOption[]
})

function handleSelect(keys: string[]) {
  const key = keys[0] as string
  if (key && !key.startsWith('type-')) {
    emit('select', key)
  }
}
</script>

<template>
  <NCard class="tree-panel" size="small">
    <div class="tree-header">
      <span class="tree-title">Wiki 页面</span>
    </div>
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
      :default-expand-all="true"
      block-line
      class="wiki-tree"
      @update:selected-keys="handleSelect"
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