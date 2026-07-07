<script setup lang="ts">
/**
 * 协作历史回放面板（S16-6）
 *
 * 功能：
 * - 加载某 slug 的历史协作事件（分页）
 * - 与实时事件流合并，输出统一时间线（去重）
 * - "加载更多"按钮（基于 before_id 游标）
 * - 加载状态 + 错误提示
 * - 事件类型彩色圆点 + 时间 + 用户 + 描述
 *
 * 用法：
 *   <CollabHistoryPanel :slug="slug" :realtime-events="events" />
 *
 * 其中 realtimeEvents 来自 useCollab 的 events ref。
 */
import { onMounted, watch, computed, toRef } from 'vue'
import { NButton, NSpin, NText, NEmpty } from 'naive-ui'
import { useCollabHistory } from '@/composables/useCollabHistory'
import type { CollabEvent } from '@/api/realtime'

const props = defineProps<{
  slug: string
  /** 实时事件流（来自 useCollab 的 events；Vue 模板会自动 unwrap ref，故接收数组） */
  realtimeEvents: readonly CollabEvent[]
}>()

const slugRef = computed(() => props.slug)
// useCollabHistory 内部按 ref.value 访问，用 toRef 包装保持响应性
const realtimeEventsRef = toRef(props, 'realtimeEvents')

const {
  mergedEvents,
  hasMore,
  totalCount,
  loading,
  error,
  loadHistory,
  loadMore,
} = useCollabHistory(slugRef, realtimeEventsRef)

// 事件类型 → 标签颜色（与 CollabPanel 保持一致）
const eventTypeColor: Record<CollabEvent['type'], string> = {
  user_joined: '#3b82f6',     // 蓝：进入
  user_left: '#9ca3af',        // 灰：离开
  lock_acquired: '#f59e0b',    // 橙：获锁
  lock_released: '#10b981',    // 绿：释锁
  lock_denied: '#ef4444',      // 红：拒锁
}

/** 把毫秒时间戳格式化为 MM-DD HH:MM:SS（历史事件需显示日期） */
function formatTime(ms: number): string {
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

onMounted(() => {
  void loadHistory()
})

// slug 变化时重新加载（composable 内部已 watch，但 immediate=false，这里显式触发首屏）
watch(
  () => props.slug,
  () => {
    void loadHistory()
  },
)
</script>

<template>
  <div class="history-panel">
    <div class="history-header">
      <span class="history-title">历史回放</span>
      <NText depth="3" class="history-count">共 {{ totalCount }} 条</NText>
    </div>

    <div v-if="error" class="history-error">
      <NText type="error" depth="2">{{ error }}</NText>
      <NButton size="tiny" @click="loadHistory()">重试</NButton>
    </div>

    <div v-if="loading && mergedEvents.length === 0" class="history-loading">
      <NSpin size="small" />
      <NText depth="3">加载中...</NText>
    </div>

    <NEmpty
      v-else-if="mergedEvents.length === 0"
      size="small"
      description="暂无历史事件"
    />

    <div v-else class="history-list">
      <div
        v-for="(ev, i) in mergedEvents"
        :key="`${ev.timestamp}-${i}`"
        class="history-item"
      >
        <span
          class="history-dot"
          :style="{ background: eventTypeColor[ev.type] }"
        ></span>
        <span class="history-time">{{ formatTime(ev.timestamp) }}</span>
        <span class="history-msg">{{ ev.message }}</span>
      </div>

      <div v-if="hasMore" class="history-more">
        <NButton
          size="small"
          :loading="loading"
          :disabled="loading"
          @click="loadMore()"
        >
          加载更多
        </NButton>
      </div>
      <div v-else-if="mergedEvents.length > 0" class="history-end">
        <NText depth="3">— 已全部加载 —</NText>
      </div>
    </div>
  </div>
</template>

<style scoped>
.history-panel {
  font-size: 13px;
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.history-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
}

.history-count {
  font-size: 12px;
}

.history-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  background: var(--n-color-error-weak, #fef2f2);
  border-radius: 4px;
  font-size: 12px;
  margin-bottom: 8px;
}

.history-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 0;
  justify-content: center;
}

.history-list {
  max-height: 320px;
  overflow-y: auto;
  padding-right: 4px;
}

.history-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  font-size: 12px;
  border-bottom: 1px solid var(--n-border-color, #f3f4f6);
}

.history-item:last-child {
  border-bottom: none;
}

.history-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.history-time {
  color: var(--n-text-color-3, #9ca3af);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
  font-size: 11px;
}

.history-msg {
  color: var(--n-text-color, #1f2937);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-more {
  display: flex;
  justify-content: center;
  padding: 8px 0;
}

.history-end {
  display: flex;
  justify-content: center;
  padding: 8px 0;
  font-size: 11px;
}
</style>
