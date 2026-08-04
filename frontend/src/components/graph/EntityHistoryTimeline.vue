<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import {
  NTimeline,
  NTimelineItem,
  NTag,
  NDescriptions,
  NDescriptionsItem,
  NSpin,
  NEmpty,
  NButton,
  NIcon,
  useMessage,
} from 'naive-ui'
import { TimeOutline, RefreshOutline, InformationCircleOutline } from '@vicons/ionicons5'
import { getEntityHistory } from '@/api/graph'
import type { EntityHistoryResult } from '@/api/graph'

const props = defineProps<{
  entityName: string
}>()

const message = useMessage()

const loading = ref(false)
const result = ref<EntityHistoryResult | null>(null)

const historyItems = computed(() => {
  if (!result.value?.history) return []
  return [...result.value.history].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime(),
  )
})

const actionTagType = (action: string) => {
  if (action === 'upsert') return 'success'
  if (action === 'delete') return 'error'
  return 'default'
}

const actionLabel = (action: string) => {
  if (action === 'upsert') return '更新/插入'
  if (action === 'delete') return '删除'
  return action
}

const confidencePercent = (confidence: number) => {
  return `${Math.round(confidence * 100)}%`
}

const confidenceTagType = (confidence: number) => {
  if (confidence >= 0.9) return 'success'
  if (confidence >= 0.7) return 'warning'
  return 'error'
}

const fetchHistory = async () => {
  if (!props.entityName) {
    result.value = null
    return
  }
  loading.value = true
  try {
    result.value = await getEntityHistory(props.entityName)
  } catch (err: any) {
    message.error(err?.message || '加载实体历史失败')
    result.value = null
  } finally {
    loading.value = false
  }
}

const handleRefresh = () => {
  fetchHistory()
}

onMounted(() => {
  fetchHistory()
})

watch(() => props.entityName, () => {
  fetchHistory()
})
</script>

<template>
  <div class="entity-history-timeline">
    <div class="header">
      <div class="header-left">
        <NIcon :size="16" :component="TimeOutline" />
        <span class="title">实体历史演变</span>
        <NTag
          v-if="result && historyItems.length > 0"
          :bordered="false"
          round
          size="small"
          type="info"
          class="count-tag"
        >
          {{ historyItems.length }}
        </NTag>
      </div>
      <NButton
        size="tiny"
        :loading="loading"
        :disabled="!entityName"
        @click="handleRefresh"
      >
        <template #icon>
          <NIcon :size="14" :component="RefreshOutline" />
        </template>
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

      <template v-else-if="result">
        <NDescriptions
          v-if="result.entity_name"
          :column="2"
          size="small"
          label-placement="left"
          bordered
          class="summary-desc"
        >
          <NDescriptionsItem label="实体名称">
            <NTag :bordered="false" round size="small">
              {{ result.entity_name }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem label="实体类型">
            <NTag
              v-if="result.entity_type"
              :bordered="false"
              round
              size="small"
              type="info"
            >
              {{ result.entity_type }}
            </NTag>
            <span v-else class="muted">-</span>
          </NDescriptionsItem>
          <NDescriptionsItem label="变更次数">
            <span class="value">
              {{ historyItems.length }}
            </span>
          </NDescriptionsItem>
          <NDescriptionsItem label="最新时间">
            <span class="value">
              {{ historyItems[0]?.timestamp || '-' }}
            </span>
          </NDescriptionsItem>
        </NDescriptions>

        <div v-if="result.note" class="note-row">
          <NIcon :size="14" :component="InformationCircleOutline" class="note-icon" />
          <span class="note-text">{{ result.note }}</span>
        </div>

        <NTimeline v-if="historyItems.length > 0" class="history-timeline">
          <NTimelineItem
            v-for="(item, idx) in historyItems"
            :key="idx"
            :time="item.timestamp"
            :type="item.action === 'delete' ? 'error' : 'success'"
          >
            <div class="timeline-content">
              <div class="timeline-header">
                <NTag
                  :type="actionTagType(item.action) as any"
                  :bordered="false"
                  round
                  size="small"
                >
                  {{ actionLabel(item.action) }}
                </NTag>
                <NTag
                  :type="confidenceTagType(item.confidence) as any"
                  :bordered="false"
                  round
                  size="tiny"
                >
                  置信度: {{ confidencePercent(item.confidence) }}
                </NTag>
              </div>
              <div class="timeline-body">
                <div class="doc-row">
                  <span class="doc-label">来源文档:</span>
                  <code class="doc-value">{{ item.source_doc_id }}</code>
                </div>
              </div>
            </div>
          </NTimelineItem>
        </NTimeline>

        <NEmpty
          v-else
          description="暂无变更历史"
          size="small"
        />
      </template>
    </div>
  </div>
</template>

<style scoped>
.entity-history-timeline {
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

.summary-desc {
  margin-bottom: var(--opskg-sp-2, 8px);
}

.summary-desc :deep(.n-descriptions-table) {
  font-size: var(--opskg-fs-xs, 12px);
}

.summary-desc :deep(.n-descriptions-label) {
  color: var(--opskg-text-3, #888);
  font-size: var(--opskg-fs-xs, 12px);
}

.value {
  font-size: var(--opskg-fs-xs, 12px);
  color: var(--opskg-text-1, #333);
}

.muted {
  color: var(--opskg-text-3, #999);
}

.note-row {
  display: flex;
  align-items: flex-start;
  gap: var(--opskg-sp-1, 4px);
  padding: var(--opskg-sp-1, 4px) var(--opskg-sp-2, 8px);
  background: var(--opskg-color-embedded-highlight, rgba(24, 160, 88, 0.04));
  border-radius: 4px;
  margin-bottom: var(--opskg-sp-2, 8px);
}

.note-icon {
  color: var(--opskg-color-warning, #f0a020);
  flex-shrink: 0;
  margin-top: 1px;
}

.note-text {
  font-size: var(--opskg-fs-xs, 12px);
  color: var(--opskg-text-2, #666);
  line-height: 1.5;
}

.history-timeline {
  padding-top: var(--opskg-sp-1, 4px);
}

.timeline-content {
  display: flex;
  flex-direction: column;
  gap: var(--opskg-sp-1, 4px);
}

.timeline-header {
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-1, 4px);
  flex-wrap: wrap;
}

.timeline-body {
  display: flex;
  flex-direction: column;
  gap: var(--opskg-sp-1, 4px);
}

.doc-row {
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-1, 4px);
  font-size: var(--opskg-fs-xs, 12px);
}

.doc-label {
  color: var(--opskg-text-3, #888);
  flex-shrink: 0;
}

.doc-value {
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
