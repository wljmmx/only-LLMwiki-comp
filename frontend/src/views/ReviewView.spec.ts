import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const mockMessage = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return { ...actual, useMessage: () => mockMessage }
})

vi.mock('@/api/review', () => ({
  getReviewQueue: vi.fn(),
  getReviewStats: vi.fn(),
  approveReview: vi.fn(),
  rejectReview: vi.fn(),
  batchApprove: vi.fn(),
}))

import {
  getReviewQueue,
  getReviewStats,
  approveReview,
  rejectReview,
  batchApprove,
} from '@/api/review'
import ReviewView from '@/views/ReviewView.vue'
import '@/test/setup'

const sampleItem = {
  id: 'r1',
  title: '审查项 1',
  type: 'entity',
  status: 'pending' as const,
  source_doc_id: 'doc-abc',
  created_at: '2026-07-01T10:00:00Z',
}

const sampleStats = {
  pending: 3,
  approved: 5,
  rejected: 2,
  modified: 0,
}

describe('ReviewView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function setupMocks() {
    ;(getReviewStats as any).mockResolvedValue(sampleStats)
    ;(getReviewQueue as any).mockResolvedValue({ items: [sampleItem], total: 1 })
  }

  function mountView() {
    return mount(ReviewView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态：onMounted 触发后 loading/statsLoading true、items 空、reviewStats null', () => {
    setupMocks()
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(true)
    expect(vm.statsLoading).toBe(true)
    expect(vm.items).toEqual([])
    expect(vm.total).toBe(0)
    expect(vm.statusFilter).toBe('')
    expect(vm.limit).toBe(10)
    expect(vm.offset).toBe(0)
    expect(vm.reviewStats).toBe(null)
    expect(vm.checkedRowKeys).toEqual([])
    expect(vm.rejectModalVisible).toBe(false)
  })

  it('onMounted 调用 fetchStats 与 fetchItems', async () => {
    setupMocks()
    mountView()
    await flushPromises()
    expect(getReviewStats).toHaveBeenCalledTimes(1)
    expect(getReviewQueue).toHaveBeenCalledWith(undefined, 10, 0)
  })

  it('fetchItems 成功：填充 items/total、loading 恢复 false', async () => {
    setupMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.items).toHaveLength(1)
    expect(vm.items[0].id).toBe('r1')
    expect(vm.total).toBe(1)
    expect(vm.loading).toBe(false)
  })

  it('fetchStats 成功：填充 reviewStats、statCards 反映统计数据', async () => {
    setupMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.reviewStats).toEqual(sampleStats)
    expect(vm.statCards).toHaveLength(4)
    expect(vm.statCards[0]).toEqual(expect.objectContaining({ label: '总数', value: 10 }))
    expect(vm.statCards[1]).toEqual(expect.objectContaining({ label: '待审', value: 3 }))
    expect(vm.statCards[2]).toEqual(expect.objectContaining({ label: '已批准', value: 5 }))
    expect(vm.statCards[3]).toEqual(expect.objectContaining({ label: '已拒绝', value: 2 }))
    expect(vm.statsLoading).toBe(false)
  })

  it('statCards：reviewStats 为 null 时所有 value 为 0', async () => {
    setupMocks()
    const wrapper = mountView()
    const vm = wrapper.vm as any
    // mount 后、flush 前 reviewStats 仍为 null
    expect(vm.statCards.map((c: any) => c.value)).toEqual([0, 0, 0, 0])
    await flushPromises()
  })

  it('fetchStats 失败：message.error("获取统计数据失败")、statsLoading 恢复', async () => {
    ;(getReviewStats as any).mockRejectedValue(new Error('network'))
    ;(getReviewQueue as any).mockResolvedValue({ items: [], total: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(mockMessage.error).toHaveBeenCalledWith('获取统计数据失败')
    expect(vm.statsLoading).toBe(false)
    expect(vm.reviewStats).toBe(null)
  })

  it('fetchItems 失败：message.error("获取审查列表失败")、loading 恢复', async () => {
    ;(getReviewStats as any).mockResolvedValue(sampleStats)
    ;(getReviewQueue as any).mockRejectedValue(new Error('network'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(mockMessage.error).toHaveBeenCalledWith('获取审查列表失败')
    expect(vm.loading).toBe(false)
    expect(vm.items).toEqual([])
  })

  it('fetchItems：statusFilter 非空时透传 status 参数', async () => {
    setupMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    ;(getReviewQueue as any).mockClear()
    vm.statusFilter = 'approved'
    await vm.fetchItems()
    expect(getReviewQueue).toHaveBeenCalledWith('approved', 10, 0)
  })

  it('handleApprove 成功：调用 approveReview 并刷新 stats+items', async () => {
    setupMocks()
    ;(approveReview as any).mockResolvedValue({})
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    ;(getReviewStats as any).mockClear()
    ;(getReviewQueue as any).mockClear()
    await vm.handleApprove('r1')
    expect(approveReview).toHaveBeenCalledWith('r1')
    expect(mockMessage.success).toHaveBeenCalledWith('已批准')
    expect(getReviewStats).toHaveBeenCalledTimes(1)
    expect(getReviewQueue).toHaveBeenCalledTimes(1)
  })

  it('handleApprove 失败：message.error("批准失败")', async () => {
    setupMocks()
    ;(approveReview as any).mockRejectedValue(new Error('forbidden'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleApprove('r1')
    expect(mockMessage.error).toHaveBeenCalledWith('批准失败')
  })

  it('handleReject：打开 modal、设置 currentRejectItem、重置 rejectReason', async () => {
    setupMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.rejectReason = '旧原因'
    vm.handleReject(sampleItem)
    expect(vm.rejectModalVisible).toBe(true)
    expect(vm.currentRejectItem).toEqual(sampleItem)
    expect(vm.rejectReason).toBe('')
  })

  it('confirmReject 成功：调用 rejectReview(id, reason)、关闭 modal、刷新', async () => {
    setupMocks()
    ;(rejectReview as any).mockResolvedValue({})
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentRejectItem = sampleItem
    vm.rejectReason = '内容不准确'
    vm.rejectModalVisible = true
    ;(getReviewStats as any).mockClear()
    ;(getReviewQueue as any).mockClear()
    await vm.confirmReject()
    expect(rejectReview).toHaveBeenCalledWith('r1', '内容不准确')
    expect(mockMessage.success).toHaveBeenCalledWith('已拒绝')
    expect(vm.rejectModalVisible).toBe(false)
    expect(getReviewStats).toHaveBeenCalledTimes(1)
    expect(getReviewQueue).toHaveBeenCalledTimes(1)
  })

  it('confirmReject：无 currentRejectItem 时直接 return', async () => {
    setupMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentRejectItem = null
    await vm.confirmReject()
    expect(rejectReview).not.toHaveBeenCalled()
  })

  it('handleBatchApprove：无选中项时 message.warning', async () => {
    setupMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.checkedRowKeys = []
    await vm.handleBatchApprove()
    expect(mockMessage.warning).toHaveBeenCalledWith('请先选择要批准的项目')
    expect(batchApprove).not.toHaveBeenCalled()
  })

  it('handleBatchApprove：有选中项时调用 batchApprove、清空 checked、刷新', async () => {
    setupMocks()
    ;(batchApprove as any).mockResolvedValue({})
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.checkedRowKeys = ['r1', 'r2']
    ;(getReviewStats as any).mockClear()
    ;(getReviewQueue as any).mockClear()
    await vm.handleBatchApprove()
    expect(batchApprove).toHaveBeenCalledWith(['r1', 'r2'])
    expect(mockMessage.success).toHaveBeenCalledWith('已批准 2 项')
    expect(vm.checkedRowKeys).toEqual([])
    expect(getReviewStats).toHaveBeenCalledTimes(1)
    expect(getReviewQueue).toHaveBeenCalledTimes(1)
  })

  it('handlePageChange：更新 offset 并重新 fetchItems', async () => {
    setupMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    ;(getReviewQueue as any).mockClear()
    vm.handlePageChange(3)
    expect(vm.offset).toBe(20) // (3-1)*10
    expect(getReviewQueue).toHaveBeenCalled()
  })

  it('handlePageSizeChange：更新 limit、重置 offset=0 并重新 fetchItems', async () => {
    setupMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.offset = 30
    ;(getReviewQueue as any).mockClear()
    vm.handlePageSizeChange(20)
    expect(vm.limit).toBe(20)
    expect(vm.offset).toBe(0)
    expect(getReviewQueue).toHaveBeenCalled()
  })

  it('handleStatusChange：重置 offset、清空 checked、重新 fetchItems', async () => {
    setupMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.offset = 30
    vm.checkedRowKeys = ['r1']
    ;(getReviewQueue as any).mockClear()
    vm.handleStatusChange()
    expect(vm.offset).toBe(0)
    expect(vm.checkedRowKeys).toEqual([])
    expect(getReviewQueue).toHaveBeenCalled()
  })

  it('formatDate：ISO 字符串转本地化日期', async () => {
    setupMocks()
    const wrapper = mountView()
    const vm = wrapper.vm as any
    const result = vm.formatDate('2026-07-01T10:30:00Z')
    expect(typeof result).toBe('string')
    expect(result).toContain('2026')
  })
})
