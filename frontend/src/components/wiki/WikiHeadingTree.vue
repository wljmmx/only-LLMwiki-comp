<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NInput, NTree, NEmpty, NSkeleton } from 'naive-ui'
import type { TreeOption } from 'naive-ui'
import type { HeadingTreeNode } from '@/api/wiki'

const props = defineProps<{
  tree: HeadingTreeNode[]
  loading: boolean
}>()

const emit = defineEmits<{
  select: [slug: string]
}>()

const searchText = ref('')

const treeData = computed<TreeOption[]>(() => {
  const filter = searchText.value.trim().toLowerCase()
  return filterTreeNodes(props.tree, filter)
})

function filterTreeNodes(nodes: HeadingTreeNode[], filter: string): TreeOption[] {
  return nodes
    .map((node) => {
      const children = node.children.length > 0 ? filterTreeNodes(node.children, filter) : []
      const labelMatch = !filter || node.title.toLowerCase().includes(filter)

      if (labelMatch || children.length > 0) {
        return {
          key: node.slug,
          label: node.title,
          isLeaf: node.children.length === 0,
          children: children.length > 0 ? children : undefined,
        } as TreeOption
      }
      return null
    })
    .filter(Boolean) as TreeOption[]
}

const expandedKeys = ref<(string | number)[]>([])

watch(
  () => props.tree,
  () => {
    expandedKeys.value = collectAllKeys(props.tree)
  },
  { immediate: true },
)

function collectAllKeys(nodes: HeadingTreeNode[]): (string | number)[] {
  const keys: (string | number)[] = []
  for (const node of nodes) {
    if (node.children.length > 0) {
      keys.push(node.slug)
      keys.push(...collectAllKeys(node.children))
    }
  }
  return keys
}

function handleSelect(keys: (string | number)[]) {
  const key = keys[0] as string
  if (key) {
    scrollToHeading(key)
    emit('select', key)
  }
}

function scrollToHeading(slug: string) {
  const el = document.getElementById(slug)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

const totalNodesCount = computed(() => countNodes(props.tree))

function countNodes(nodes: HeadingTreeNode[]): number {
  let count = nodes.length
  for (const node of nodes) {
    count += countNodes(node.children)
  }
  return count
}
</script>

<template>
  <div class="wiki-heading-tree">
    <div class="tree-header">
      <span class="tree-title">章节目录</span>
      <span class="tree-count" v-if="totalNodesCount > 0">{{ totalNodesCount }} 节</span>
    </div>

    <NInput
      v-model:value="searchText"
      placeholder="搜索章节..."
      clearable
      size="small"
      class="tree-search"
    />

    <div v-if="loading" class="tree-skeleton">
      <NSkeleton text :repeat="5" :height="18" />
    </div>

    <NTree
      v-else-if="treeData.length > 0"
      :data="treeData"
      :expanded-keys="expandedKeys"
      :default-expand-all="false"
      block-line
      class="heading-tree"
      @update:selected-keys="handleSelect"
      @update:expanded-keys="(keys: (string | number)[]) => expandedKeys = keys"
    />

    <NEmpty
      v-else-if="!loading"
      description="暂无章节"
      size="small"
    />
  </div>
</template>

<style scoped>
.wiki-heading-tree {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 12px;
  box-sizing: border-box;
}

.tree-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 4px 10px;
  border-bottom: 1px solid var(--n-border-color, #e5e7eb);
  margin-bottom: 10px;
}

.tree-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
}

.tree-count {
  font-size: 11px;
  color: var(--n-text-color-3, #9ca3af);
  padding: 2px 6px;
  background: var(--n-base-color, #f3f4f6);
  border-radius: 4px;
}

.tree-search {
  margin-bottom: 8px;
}

.heading-tree {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
}

.heading-tree :deep(.n-tree-node) {
  font-size: 13px;
}

.heading-tree :deep(.n-tree-node-content) {
  line-height: 1.6;
  padding: 3px 4px;
  border-radius: 4px;
  transition: background 0.15s;
}

.heading-tree :deep(.n-tree-node-content:hover) {
  background: var(--n-color-hover, rgba(0, 0, 0, 0.04));
}

.heading-tree :deep(.n-tree-node-content-selected) {
  background: var(--n-color-target, rgba(32, 128, 240, 0.08));
  color: var(--n-primary-color, #2080f0);
}

.tree-skeleton {
  padding: 8px 0;
}
</style>
