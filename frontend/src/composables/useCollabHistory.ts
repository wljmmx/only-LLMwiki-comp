/**
 * 协作历史事件回放 composable（S16-6）
 *
 * 职责：
 * 1. 加载某 slug 的历史协作事件（分页 / 增量同步）
 * 2. 与实时事件流（useCollab 的 events）合并去重，输出统一时间线
 * 3. 提供"加载更多"分页能力（基于 before_id 游标）
 *
 * 设计要点：
 * - 历史事件按 id 倒序加载（最新在前），实时事件按时间正序追加
 * - 合并时去重：用 (timestamp, eventType, userId) 作为去重键，
 *   避免实时事件在历史中已存在时重复显示
 * - timestamp 统一为毫秒：后端秒级 ×1000，前端 Date.now() 已是毫秒
 * - 加载状态 loading / 错误 error 暴露给 UI
 *
 * 用法：
 *   const { historyEvents, mergedEvents, loadHistory, loadMore, hasMore, loading, error } =
 *     useCollabHistory(slug, realtimeEvents)
 */
import { ref, computed, type Ref, watch } from 'vue'
import {
  listCollabEvents,
  type CollabHistoryEvent,
  type CollabEventListResult,
} from '@/api/realtime'
import type { CollabEvent } from '@/api/realtime'

/** 历史事件单页默认条数 */
const DEFAULT_PAGE_SIZE = 50

/** 去重键：timestamp(秒,保留3位小数) + type + userId */
function dedupKey(tsMs: number, type: string, userId: string): string {
  // 历史事件 timestamp 秒级浮点，×1000 后取整毫秒；实时事件已是毫秒
  // 用秒级精度（除以 1000 取 3 位小数）作为去重粒度，避免毫秒误差导致漏去重
  const tsSec = (tsMs / 1000).toFixed(3)
  return `${tsSec}|${type}|${userId}`
}

/**
 * 将后端 CollabHistoryEvent 转换为前端 CollabEvent（用于与实时事件流合并）
 *
 * 主要差异：
 * - timestamp: 秒 → 毫秒（×1000）
 * - event_type → type
 * - user_id → userId
 * - display_name → displayName
 */
export function historyEventToCollabEvent(h: CollabHistoryEvent): CollabEvent {
  return {
    timestamp: h.timestamp * 1000,
    type: h.event_type,
    userId: h.user_id,
    displayName: h.display_name || h.user_id,
    message: h.message,
  }
}

export interface UseCollabHistoryReturn {
  /** 历史事件（按 id 倒序，最新在前） */
  historyEvents: Ref<CollabHistoryEvent[]>
  /** 合并后的统一时间线（历史 + 实时，去重，按时间倒序） */
  mergedEvents: Ref<CollabEvent[]>
  /** 是否还有更多历史可加载 */
  hasMore: Ref<boolean>
  /** 该 slug 事件总数（来自后端 total 字段） */
  totalCount: Ref<number>
  /** 加载状态 */
  loading: Ref<boolean>
  /** 错误信息 */
  error: Ref<string | null>
  /** 加载首页历史事件 */
  loadHistory: (limit?: number) => Promise<void>
  /** 加载更多（基于当前最早历史事件的 id 游标） */
  loadMore: () => Promise<void>
  /** 重置状态（slug 切换时调用） */
  reset: () => void
}

export function useCollabHistory(
  slug: Ref<string> | string,
  realtimeEvents: Readonly<Ref<readonly CollabEvent[]>>,
): UseCollabHistoryReturn {
  const historyEvents = ref<CollabHistoryEvent[]>([])
  const hasMore = ref(false)
  const totalCount = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const slugRef = computed(() =>
    typeof slug === 'string' ? slug : slug.value
  )

  /** 当前已加载历史中最早的 id（用于 loadMore 游标） */
  const oldestLoadedId = computed(() => {
    if (historyEvents.value.length === 0) return null
    // historyEvents 按 id 倒序（最新在前），最后一个是最早
    return historyEvents.value[historyEvents.value.length - 1].id
  })

  /** 合并历史 + 实时事件，去重，按时间倒序 */
  const mergedEvents = computed<CollabEvent[]>(() => {
    const seen = new Set<string>()
    const all: CollabEvent[] = []

    // 历史事件转 CollabEvent
    for (const h of historyEvents.value) {
      const e = historyEventToCollabEvent(h)
      const key = dedupKey(e.timestamp, e.type, e.userId)
      if (!seen.has(key)) {
        seen.add(key)
        all.push(e)
      }
    }

    // 实时事件（按到达顺序追加）
    for (const e of realtimeEvents.value) {
      const key = dedupKey(e.timestamp, e.type, e.userId)
      if (!seen.has(key)) {
        seen.add(key)
        all.push(e)
      }
    }

    // 按时间倒序（最新在前）
    all.sort((a, b) => b.timestamp - a.timestamp)
    return all
  })

  async function loadHistory(limit: number = DEFAULT_PAGE_SIZE): Promise<void> {
    if (loading.value) return
    const s = slugRef.value
    if (!s) return

    loading.value = true
    error.value = null
    try {
      const result: CollabEventListResult = await listCollabEvents(s, { limit })
      historyEvents.value = result.events
      hasMore.value = result.has_more
      totalCount.value = result.total
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : String(e)
      // 加载失败保留空列表，不污染已有数据
    } finally {
      loading.value = false
    }
  }

  async function loadMore(): Promise<void> {
    if (loading.value || !hasMore.value) return
    const s = slugRef.value
    if (!s) return
    const beforeId = oldestLoadedId.value
    if (beforeId === null) return

    loading.value = true
    error.value = null
    try {
      const result: CollabEventListResult = await listCollabEvents(s, {
        limit: DEFAULT_PAGE_SIZE,
        before_id: beforeId,
      })
      // 追加到末尾（历史更早的事件）
      historyEvents.value = [...historyEvents.value, ...result.events]
      hasMore.value = result.has_more
      // total 不变（仍是该 slug 全量计数）
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  function reset(): void {
    historyEvents.value = []
    hasMore.value = false
    totalCount.value = 0
    loading.value = false
    error.value = null
  }

  // slug 变化时自动重置 + 重新加载
  watch(
    slugRef,
    () => {
      reset()
      void loadHistory()
    },
    { immediate: false }
  )

  return {
    historyEvents,
    mergedEvents,
    hasMore,
    totalCount,
    loading,
    error,
    loadHistory,
    loadMore,
    reset,
  }
}
