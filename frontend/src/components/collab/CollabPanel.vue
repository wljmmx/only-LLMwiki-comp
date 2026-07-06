<script setup lang="ts">
/**
 * 协作面板组件（S16-1 WikiView 协作集成）
 *
 * 用法：
 *   <CollabPanel :slug="currentPageSlug" :key="currentPageSlug" />
 *
 * 通过 :key 随 slug 变化重建组件，触发 useCollab 重新连接。
 *
 * 功能：
 * - 显示在线用户列表（头像 + 用户名 + 角色）
 * - 显示当前编辑锁持有者
 * - 申请/释放编辑锁按钮（hasLock 状态切换）
 * - 连接状态指示器（disconnected/connecting/connected/reconnecting/error）
 * - 错误提示
 */
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { NAvatar, NButton, NTag, NSpace, NTooltip, NText, NCollapse, NCollapseItem } from 'naive-ui'
import { useCollab } from '@/composables/useCollab'
import type { ConnectionState } from '@/composables/useCollab'
import type { CollabEvent } from '@/api/realtime'

const props = defineProps<{
  slug: string
}>()

// S16-2：向上通知锁状态变化，便于 WikiView 控制 WikiEditor 显示
const emit = defineEmits<{
  (e: 'lock-change', payload: { hasLock: boolean; lockHolder: string | null }): void
}>()

const {
  onlineUsers,
  lockHolder,
  connectionState,
  lastError,
  events,
  hasLock,
  onlineCount,
  connect,
  disconnect,
  acquireLock,
  releaseLock,
} = useCollab(props.slug)

// S16-2：锁状态变化时通知父组件
// 用 getter 函数形式 watch，兼容 ref 与 mock 对象（测试时 mock 返回 {value: ...} 非真 ref）
watch(
  () => [hasLock.value, lockHolder.value] as [boolean, string | null],
  ([hl, lh]) => {
    emit('lock-change', { hasLock: hl, lockHolder: lh })
  },
  { immediate: true },
)

// 连接状态 → 标签类型
const stateTagType: Record<ConnectionState, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  disconnected: 'default',
  connecting: 'info',
  connected: 'success',
  reconnecting: 'warning',
  error: 'error',
}

const stateLabel: Record<ConnectionState, string> = {
  disconnected: '未连接',
  connecting: '连接中',
  connected: '已连接',
  reconnecting: '重连中',
  error: '错误',
}

// 角色 → 标签颜色
const roleTagType: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  admin: 'error',
  operator: 'warning',
  viewer: 'default',
}

// 锁持有者展示名（从 onlineUsers 查找）
const lockHolderName = computed(() => {
  if (!lockHolder.value) return null
  const user = onlineUsers.value.find((u) => u.user_id === lockHolder.value)
  return user?.display_name || user?.username || lockHolder.value
})

// 是否可申请锁（已连接 + 无人持锁 + 自己未持锁）
const canAcquire = computed(
  () => connectionState.value === 'connected' && !lockHolder.value && !hasLock.value,
)

// 是否可释放锁（自己持锁）
const canRelease = computed(
  () => connectionState.value === 'connected' && hasLock.value,
)

// S16-3：事件流
// 按时间倒序展示（最新在最上），最多展示 MAX_EVENTS 条
const reversedEvents = computed(() => [...events.value].reverse())

// 事件类型 → 标签颜色（用于左侧圆点）
const eventTypeColor: Record<CollabEvent['type'], string> = {
  user_joined: '#3b82f6',     // 蓝：进入
  user_left: '#9ca3af',        // 灰：离开
  lock_acquired: '#f59e0b',    // 橙：获锁
  lock_released: '#10b981',    // 绿：释锁
  lock_denied: '#ef4444',      // 红：拒锁
}

/** 把毫秒时间戳格式化为 HH:MM:SS */
function formatTime(ms: number): string {
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

onMounted(() => {
  connect()
})

onUnmounted(() => {
  disconnect()
})
</script>

<template>
  <div class="collab-panel">
    <div class="collab-header">
      <span class="collab-title">协作</span>
      <NTag :type="stateTagType[connectionState]" size="small" round>
        {{ stateLabel[connectionState] }}
      </NTag>
    </div>

    <div v-if="lastError" class="collab-error">
      <NText type="error" depth="2">{{ lastError }}</NText>
    </div>

    <div class="collab-section">
      <div class="section-label">
        在线用户
        <NText depth="3" style="margin-left: 4px">({{ onlineCount }})</NText>
      </div>
      <div v-if="onlineCount === 0" class="empty-hint">暂无其他用户</div>
      <NSpace v-else :size="8" class="user-list">
        <NTooltip v-for="u in onlineUsers" :key="u.user_id" placement="top">
          <template #trigger>
            <div class="user-chip" :class="{ 'is-self': false }">
              <NAvatar
                round
                size="small"
                :style="{ background: lockHolder === u.user_id ? '#f59e0b' : '#3b82f6' }"
              >
                {{ u.display_name?.charAt(0) || u.username?.charAt(0) || '?' }}
              </NAvatar>
              <span class="user-name">{{ u.display_name || u.username }}</span>
              <NTag
                v-if="u.role"
                :type="roleTagType[u.role] || 'default'"
                size="tiny"
                round
              >
                {{ u.role }}
              </NTag>
              <NTag v-if="lockHolder === u.user_id" type="warning" size="tiny" round>
                编辑中
              </NTag>
            </div>
          </template>
          {{ u.username }} ({{ u.role }})
        </NTooltip>
      </NSpace>
    </div>

    <div class="collab-section">
      <div class="section-label">编辑锁</div>
      <div v-if="hasLock" class="lock-status lock-self">
        <NTag type="success" size="small">你正在编辑此页面</NTag>
        <NButton size="tiny" type="warning" :disabled="!canRelease" @click="releaseLock">
          释放锁
        </NButton>
      </div>
      <div v-else-if="lockHolder" class="lock-status lock-other">
        <NText type="warning" depth="2">
          {{ lockHolderName }} 正在编辑
        </NText>
      </div>
      <div v-else class="lock-status lock-free">
        <NText depth="3">无人编辑，可申请锁</NText>
        <NButton
          size="tiny"
          type="primary"
          :disabled="!canAcquire"
          @click="acquireLock"
        >
          申请编辑锁
        </NButton>
      </div>
    </div>

    <!-- S16-3：事件流区域 -->
    <div class="collab-section">
      <div class="section-label">事件流</div>
      <NCollapse :default-expanded-names="['events']" display-directive="show">
        <NCollapseItem title="最近事件" name="events">
          <div v-if="reversedEvents.length === 0" class="empty-hint">暂无事件</div>
          <div v-else class="event-list">
            <div
              v-for="(ev, i) in reversedEvents"
              :key="`${ev.timestamp}-${i}`"
              class="event-item"
            >
              <span
                class="event-dot"
                :style="{ background: eventTypeColor[ev.type] }"
              ></span>
              <span class="event-time">{{ formatTime(ev.timestamp) }}</span>
              <span class="event-msg">{{ ev.message }}</span>
            </div>
          </div>
        </NCollapseItem>
      </NCollapse>
    </div>
  </div>
</template>

<style scoped>
.collab-panel {
  border: 1px solid var(--n-border-color, #e5e7eb);
  border-radius: 8px;
  padding: 12px 16px;
  background: var(--n-color, #fff);
  font-size: 13px;
}

.collab-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--n-border-color, #e5e7eb);
}

.collab-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
}

.collab-error {
  margin-bottom: 8px;
  padding: 6px 8px;
  background: var(--n-color-error-weak, #fef2f2);
  border-radius: 4px;
  font-size: 12px;
}

.collab-section {
  margin-bottom: 12px;
}

.collab-section:last-child {
  margin-bottom: 0;
}

.section-label {
  font-size: 12px;
  color: var(--n-text-color-3, #6b7280);
  margin-bottom: 6px;
  font-weight: 500;
}

.empty-hint {
  font-size: 12px;
  color: var(--n-text-color-3, #9ca3af);
  padding: 4px 0;
}

.user-list {
  flex-wrap: wrap;
}

.user-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  border-radius: 12px;
  background: var(--n-color-target, #f3f4f6);
}

.user-name {
  font-size: 12px;
  color: var(--n-text-color, #1f2937);
  max-width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lock-status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  flex-wrap: wrap;
}

.lock-self {
  color: var(--n-success-color, #10b981);
}

.lock-other {
  color: var(--n-warning-color, #f59e0b);
}

.lock-free {
  color: var(--n-text-color-3, #6b7280);
}

/* S16-3：事件流 */
.event-list {
  max-height: 200px;
  overflow-y: auto;
  padding-right: 4px;
}

.event-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  font-size: 12px;
  border-bottom: 1px solid var(--n-border-color, #f3f4f6);
}

.event-item:last-child {
  border-bottom: none;
}

.event-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.event-time {
  color: var(--n-text-color-3, #9ca3af);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}

.event-msg {
  color: var(--n-text-color, #1f2937);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
