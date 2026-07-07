/**
 * useCollabHistory composable 单元测试（S16-6 协作历史回放）
 *
 * 覆盖：
 * 1. 初始状态：historyEvents / hasMore / totalCount / loading / error
 * 2. loadHistory：加载首页历史事件
 * 3. loadMore：分页加载更多（before_id 游标）
 * 4. mergedEvents：历史 + 实时合并去重
 * 5. historyEventToCollabEvent：秒→毫秒转换
 * 6. reset：重置状态
 * 7. slug 变化自动重置 + 重新加载
 * 8. 错误处理：API 失败时 error 填充，不抛异常
 * 9. loading 状态：并发请求保护
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { ref, nextTick } from 'vue'
import { setActivePinia, createPinia } from 'pinia'

// ────────── mock @/api/realtime 的 listCollabEvents ──────────
const { mockListCollabEvents } = vi.hoisted(() => ({
  mockListCollabEvents: vi.fn(),
}))

vi.mock('@/api/realtime', async () => {
  const actual = await vi.importActual<typeof import('@/api/realtime')>('@/api/realtime')
  return {
    ...actual,
    listCollabEvents: mockListCollabEvents,
  }
})

import { useCollabHistory, historyEventToCollabEvent } from './useCollabHistory'
import type { CollabEvent, CollabHistoryEvent, CollabEventListResult } from '@/api/realtime'

// ────────── helper：构造 CollabHistoryEvent ──────────
function makeHistoryEvent(
  id: number,
  slug: string,
  timestampSec: number,
  eventType: CollabEvent['type'],
  userId: string,
  displayName: string,
  message: string,
): CollabHistoryEvent {
  return {
    id,
    slug,
    timestamp: timestampSec,
    event_type: eventType,
    user_id: userId,
    display_name: displayName,
    message,
    created_at: '2026-07-06T00:00:00Z',
  }
}

function makeResult(
  events: CollabHistoryEvent[],
  hasMore: boolean,
  total: number,
): CollabEventListResult {
  return {
    slug: 'test-slug',
    events,
    has_more: hasMore,
    count: events.length,
    total,
  }
}

describe('composables/useCollabHistory — S16-6 协作历史回放', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  // ────────── 初始状态 ──────────

  it('初始状态：historyEvents 空 / hasMore false / totalCount 0 / loading false / error null', () => {
    const realtimeEvents = ref<CollabEvent[]>([])
    const { historyEvents, hasMore, totalCount, loading, error } = useCollabHistory(
      'test-slug',
      realtimeEvents,
    )
    expect(historyEvents.value).toEqual([])
    expect(hasMore.value).toBe(false)
    expect(totalCount.value).toBe(0)
    expect(loading.value).toBe(false)
    expect(error.value).toBeNull()
  })

  // ────────── loadHistory ──────────

  it('loadHistory 成功：填充 historyEvents + hasMore + totalCount', async () => {
    const events = [
      makeHistoryEvent(3, 's', 300, 'user_joined', 'u1', 'Alice', 'Alice 加入了协作'),
      makeHistoryEvent(2, 's', 200, 'lock_acquired', 'u1', 'Alice', 'Alice 获取了编辑锁'),
      makeHistoryEvent(1, 's', 100, 'user_left', 'u1', 'Alice', 'Alice 离开了协作'),
    ]
    mockListCollabEvents.mockResolvedValue(makeResult(events, true, 100))

    const realtimeEvents = ref<CollabEvent[]>([])
    const { loadHistory, historyEvents, hasMore, totalCount, loading } = useCollabHistory(
      's',
      realtimeEvents,
    )
    await loadHistory()

    expect(historyEvents.value.length).toBe(3)
    expect(hasMore.value).toBe(true)
    expect(totalCount.value).toBe(100)
    expect(loading.value).toBe(false)
  })

  it('loadHistory 调用 listCollabEvents 传 slug 与 limit', async () => {
    mockListCollabEvents.mockResolvedValue(makeResult([], false, 0))

    const realtimeEvents = ref<CollabEvent[]>([])
    const { loadHistory } = useCollabHistory('my-slug', realtimeEvents)
    await loadHistory(50)

    expect(mockListCollabEvents).toHaveBeenCalledWith('my-slug', { limit: 50 })
  })

  it('loadHistory 失败：error 填充 + 不抛异常 + historyEvents 保持空', async () => {
    mockListCollabEvents.mockRejectedValue(new Error('Network error'))

    const realtimeEvents = ref<CollabEvent[]>([])
    const { loadHistory, historyEvents, error, loading } = useCollabHistory('s', realtimeEvents)
    await loadHistory()

    expect(error.value).toBe('Network error')
    expect(historyEvents.value).toEqual([])
    expect(loading.value).toBe(false)
  })

  // ────────── loadMore ──────────

  it('loadMore：使用 oldestLoadedId 作为 before_id 游标', async () => {
    const page1 = [
      makeHistoryEvent(5, 's', 500, 'user_joined', 'u1', 'A', 'm5'),
      makeHistoryEvent(4, 's', 400, 'user_joined', 'u1', 'A', 'm4'),
      makeHistoryEvent(3, 's', 300, 'user_joined', 'u1', 'A', 'm3'),
    ]
    const page2 = [
      makeHistoryEvent(2, 's', 200, 'user_joined', 'u1', 'A', 'm2'),
      makeHistoryEvent(1, 's', 100, 'user_joined', 'u1', 'A', 'm1'),
    ]

    mockListCollabEvents.mockResolvedValueOnce(makeResult(page1, true, 5))
    const realtimeEvents = ref<CollabEvent[]>([])
    const { loadHistory, loadMore, historyEvents, hasMore } = useCollabHistory(
      's',
      realtimeEvents,
    )
    await loadHistory()
    expect(historyEvents.value.length).toBe(3)

    // loadMore 应传 before_id=3（page1 最后一个 id）
    mockListCollabEvents.mockResolvedValueOnce(makeResult(page2, false, 5))
    await loadMore()

    expect(mockListCollabEvents).toHaveBeenLastCalledWith('s', {
      limit: 50,
      before_id: 3,
    })
    expect(historyEvents.value.length).toBe(5)
    expect(hasMore.value).toBe(false)
  })

  it('loadMore 在 hasMore=false 时不调用 API', async () => {
    mockListCollabEvents.mockResolvedValue(makeResult([], false, 0))
    const realtimeEvents = ref<CollabEvent[]>([])
    const { loadHistory, loadMore } = useCollabHistory('s', realtimeEvents)
    await loadHistory()

    mockListCollabEvents.mockClear()
    await loadMore()
    expect(mockListCollabEvents).not.toHaveBeenCalled()
  })

  // ────────── mergedEvents 合并去重 ──────────

  it('mergedEvents：历史 + 实时合并，按时间倒序', async () => {
    const historyEventsData = [
      makeHistoryEvent(1, 's', 100, 'user_joined', 'u1', 'Alice', '历史: Alice 加入'),
    ]
    mockListCollabEvents.mockResolvedValue(makeResult(historyEventsData, false, 1))

    const realtimeEvents = ref<CollabEvent[]>([
      {
        timestamp: 200 * 1000, // 毫秒
        type: 'lock_acquired',
        userId: 'u1',
        displayName: 'Alice',
        message: '实时: Alice 获锁',
      },
    ])
    const { loadHistory, mergedEvents } = useCollabHistory('s', realtimeEvents)
    await loadHistory()

    // 历史事件 timestamp=100s → 100000ms；实时事件 timestamp=200000ms
    // 倒序：实时(200000) 在前，历史(100000) 在后
    expect(mergedEvents.value.length).toBe(2)
    expect(mergedEvents.value[0].message).toBe('实时: Alice 获锁')
    expect(mergedEvents.value[1].message).toBe('历史: Alice 加入')
  })

  it('mergedEvents：相同 (timestamp, type, userId) 去重', async () => {
    const historyEventsData = [
      makeHistoryEvent(1, 's', 100, 'user_joined', 'u1', 'Alice', 'Alice 加入'),
    ]
    mockListCollabEvents.mockResolvedValue(makeResult(historyEventsData, false, 1))

    const realtimeEvents = ref<CollabEvent[]>([
      {
        timestamp: 100 * 1000, // 同一秒
        type: 'user_joined',
        userId: 'u1',
        displayName: 'Alice',
        message: 'Alice 加入（实时重复）',
      },
    ])
    const { loadHistory, mergedEvents } = useCollabHistory('s', realtimeEvents)
    await loadHistory()

    // 去重后只剩 1 条（历史优先）
    expect(mergedEvents.value.length).toBe(1)
    expect(mergedEvents.value[0].message).toBe('Alice 加入')
  })

  it('mergedEvents：不同 userId 不去重', async () => {
    const historyEventsData = [
      makeHistoryEvent(1, 's', 100, 'user_joined', 'u1', 'Alice', 'Alice 加入'),
    ]
    mockListCollabEvents.mockResolvedValue(makeResult(historyEventsData, false, 1))

    const realtimeEvents = ref<CollabEvent[]>([
      {
        timestamp: 100 * 1000,
        type: 'user_joined',
        userId: 'u2', // 不同用户
        displayName: 'Bob',
        message: 'Bob 加入',
      },
    ])
    const { loadHistory, mergedEvents } = useCollabHistory('s', realtimeEvents)
    await loadHistory()

    expect(mergedEvents.value.length).toBe(2)
  })

  // ────────── historyEventToCollabEvent ──────────

  it('historyEventToCollabEvent：秒→毫秒转换 + 字段映射', () => {
    const h = makeHistoryEvent(1, 's', 1234567890.123, 'user_joined', 'u1', 'Alice', 'msg')
    const e = historyEventToCollabEvent(h)
    expect(e.timestamp).toBe(1234567890.123 * 1000)
    expect(e.type).toBe('user_joined')
    expect(e.userId).toBe('u1')
    expect(e.displayName).toBe('Alice')
    expect(e.message).toBe('msg')
  })

  it('historyEventToCollabEvent：display_name 为空时回退到 user_id', () => {
    const h = makeHistoryEvent(1, 's', 1, 'user_left', 'u1', '', 'msg')
    const e = historyEventToCollabEvent(h)
    expect(e.displayName).toBe('u1')
  })

  // ────────── reset ──────────

  it('reset：清空所有状态', async () => {
    const events = [makeHistoryEvent(1, 's', 1, 'user_joined', 'u1', 'A', 'm')]
    mockListCollabEvents.mockResolvedValue(makeResult(events, true, 100))

    const realtimeEvents = ref<CollabEvent[]>([])
    const { loadHistory, reset, historyEvents, hasMore, totalCount } = useCollabHistory(
      's',
      realtimeEvents,
    )
    await loadHistory()
    expect(historyEvents.value.length).toBe(1)

    reset()
    expect(historyEvents.value).toEqual([])
    expect(hasMore.value).toBe(false)
    expect(totalCount.value).toBe(0)
  })

  // ────────── slug 变化 ──────────

  it('slug 变化时自动重置 + 重新加载', async () => {
    const slugRef = ref('slug-a')
    mockListCollabEvents.mockResolvedValue(makeResult([], false, 0))

    const realtimeEvents = ref<CollabEvent[]>([])
    const { loadHistory } = useCollabHistory(slugRef, realtimeEvents)
    await loadHistory()

    expect(mockListCollabEvents).toHaveBeenLastCalledWith('slug-a', { limit: 50 })

    // 切换 slug
    slugRef.value = 'slug-b'
    await nextTick()
    await nextTick()
    await vi.waitFor(() => {
      expect(mockListCollabEvents).toHaveBeenLastCalledWith('slug-b', { limit: 50 })
    })
  })

  // ────────── loading 并发保护 ──────────

  it('loadHistory 在 loading=true 时不重复调用', async () => {
    mockListCollabEvents.mockResolvedValue(makeResult([], false, 0))

    const realtimeEvents = ref<CollabEvent[]>([])
    const { loadHistory } = useCollabHistory('s', realtimeEvents)

    // 并发调用两次
    await Promise.all([loadHistory(), loadHistory()])
    // 仅调用一次（第二次被 loading 守卫拦截）
    expect(mockListCollabEvents).toHaveBeenCalledTimes(1)
  })

  it('loadMore 在 loading=true 时不重复调用', async () => {
    const page1 = [makeHistoryEvent(1, 's', 1, 'user_joined', 'u1', 'A', 'm')]
    mockListCollabEvents.mockResolvedValue(makeResult(page1, true, 100))

    const realtimeEvents = ref<CollabEvent[]>([])
    const { loadHistory, loadMore } = useCollabHistory('s', realtimeEvents)
    await loadHistory()

    mockListCollabEvents.mockClear()
    mockListCollabEvents.mockResolvedValue(makeResult([], false, 100))

    await Promise.all([loadMore(), loadMore()])
    expect(mockListCollabEvents).toHaveBeenCalledTimes(1)
  })
})
