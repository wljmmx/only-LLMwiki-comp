<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import {
  NCollapse,
  NCollapseItem,
  NTag,
  NButton,
  NSpin,
  NEmpty,
  NIcon,
  useMessage,
} from 'naive-ui'
import { LinkOutline, DocumentTextOutline } from '@vicons/ionicons5'
import { getEntityBacklinks } from '@/api/graph'
import type { BacklinkGraphResult } from '@/api/graph'

const props = defineProps<{
  entityName: string
}>()

const emit = defineEmits<{
  (e: 'navigate', slug: string): void
}>()

const message = useMessage()

const loading = ref(false)
const result = ref<BacklinkGraphResult | null>(null)

const backlinks = computed(() => {
  if (!result.value?.backlinks) return []
  return [...result.value.backlinks].sort((a, b) => b.count - a.count)
})

const totalCount = computed(() => result.value?.backlink_count ?? 0)

const fetchBacklinks = async () => {
  if (!props.entityName) {
    result.value = null
    return
  }
  loading.value = true
  try {
    result.value = await getEntityBacklinks(props.entityName)
  } catch (err: any) {
    message.error(err?.message || '加载 backlink 数据失败')
    result.value = null
  } finally {
    loading.value = false
  }
}

const handleNavigate = (slug: string) => {
  emit('navigate', slug)
}

const handleRefresh = () => {
  fetchBacklinks()
}

onMounted(() => {
  fetchBacklinks()
})

watch(() => props.entityName, () => {
  fetchBacklinks()
})
</script>

<template>
  <div class="entity-backlink-graph">
    <div class="header">
      <div class="header-left">
        <NIcon :size="16" :component="LinkOutline" />
        <span class="title">Backlink 引用</span>
        <NTag
          v-if="totalCount > 0"
          :bordered="false"
          round
          size="small"
          type="info"
          class="count-tag"
        >
          {{ totalCount }}
        </NTag>
      </div>
      <NButton
        size="tiny"
        :loading="loading"
        :disabled="!entityName"
        @click="handleRefresh"
      >
        刷新
      </NButton>
    </div>

    <div class="content">
      <NSpin v-if="loading && !result" :size="16">
        <template #description>加载中...</template>
      </NSpin>

      <NEmpty
        v-else-if="!entityName"
        description="未选择实体"
        size="small"
      />

      <NEmpty
        v-else-if="result && backlinks.length === 0"
        description="暂无引用该实体的 Wiki 页面"
        size="small"
      />

      <NCollapse
        v-else-if="result && backlinks.length > 0"
        :default-expanded-names="backlinks.map((b) => b.slug)"
        accordion
      >
        <NCollapseItem
          v-for="item in backlinks"
          :key="item.slug"
          :name="item.slug"
          class="backlink-item"
        >
          <template #header>
            <div class="item-header">
              <NIcon :size="14" :component="DocumentTextOutline" class="doc-icon" />
              <span class="item-title">{{ item.title }}</span>
              <NTag
                :bordered="false"
                round
                size="tiny"
                type="warning"
                class="count-badge"
              >
                ×{{ item.count }}
              </NTag>
            </div>
          </template>
          <div class="item-body">
            <div class="slug-row">
              <span class="slug-label">Slug:</span>
              <code class="slug-value">{{ item.slug }}</code>
            </div>
            <NButton
              size="tiny"
              type="primary"
              :bordered="false"
              @click="handleNavigate(item.slug)"
            >
              跳转到此页面
            </NButton>
          </div>
        </NCollapseItem>
      </NCollapse>
    </div>
  </div>
</template>

<style scoped>
.entity-backlink-graph {
  display: flex;
  flex-direction: column;
  gap: var(--opskg-sp-2, 8px);
  padding: var(--opskg-sp-2, 8px);
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: var(--opskg-sp-2, 8px);
  border-bottom: 1px solid var(--opskg-border-color, #e8e8e8);
}

.header-left {
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-1, 4px);
}

.title {
  font-size: var(--opskg-fs-sm, 13px);
  font-weight: 600;
  color: var(--opskg-text-1, #333);
}

.count-tag {
  font-weight: 500;
}

.content {
  min-height: 80px;
}

.backlink-item {
  margin-bottom: var(--opskg-sp-1, 4px);
  border: 1px solid var(--opskg-border-color, #e8e8e8);
  border-radius: 6px;
  overflow: hidden;
}

.item-header {
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-1, 4px);
  padding: 2px 0;
}

.doc-icon {
  color: var(--opskg-text-3, #999);
  flex-shrink: 0;
}

.item-title {
  font-size: var(--opskg-fs-sm, 13px);
  color: var(--opskg-text-1, #333);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.count-badge {
  flex-shrink: 0;
}

.item-body {
  display: flex;
  flex-direction: column;
  gap: var(--opskg-sp-2, 8px);
  padding: var(--opskg-sp-2, 8px);
  background: var(--opskg-color-embedded-highlight, rgba(24, 160, 88, 0.04));
}

.slug-row {
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-1, 4px);
  font-size: var(--opskg-fs-xs, 12px);
}

.slug-label {
  color: var(--opskg-text-3, #888);
  flex-shrink: 0;
}

.slug-value {
  font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Roboto Mono', monospace;
  font-size: var(--opskg-fs-xs, 12px);
  color: var(--opskg-text-2, #555);
  background: var(--opskg-color-embedded-highlight, rgba(24, 160, 88, 0.06));
  padding: 1px 6px;
  border-radius: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
</style>
