import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// mock 各 API 模块
vi.mock('@/api/documents', () => ({
  getDocumentStats: vi.fn(),
  listDocuments: vi.fn(),
}))

vi.mock('@/api/review', () => ({
  getReviewStats: vi.fn(),
  getReviewQueue: vi.fn(),
}))

vi.mock('@/api/search', () => ({
  getSearchStats: vi.fn(),
}))

vi.mock('@/api/index', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
    put: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}))

import { getDocumentStats, listDocuments } from '@/api/documents'
import { getReviewStats, getReviewQueue } from '@/api/review'
import { getSearchStats } from '@/api/search'
import api from '@/api/index'
import DashboardView from '@/views/DashboardView.vue'
import '@/test/setup'

describe('DashboardView.vue', () => {
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
    ;(getDocumentStats as any).mockResolvedValue({
      total_documents: 10,
      total_parsed: 8,
      total_failed: 1,
      by_format: { md: 5, txt: 5 },
    })
    ;(api.get as any).mockResolvedValue({
      nodes: 20,
      edges: 30,
      by_type: { host: 5, service: 15 },
    })
    ;(getReviewStats as any).mockResolvedValue({
      total_pending: 3,
      total_approved: 5,
      total_rejected: 1,
    })
    ;(getSearchStats as any).mockResolvedValue({
      total_documents: 10,
      total_indexed: 8,
    })
    ;(listDocuments as any).mockResolvedValue({
      documents: [
        {
          doc_id: 'd1',
          filename: 'test.md',
          format: 'md',
          size: 1024,
          status: 'parsed',
          created_at: '2026-07-01T00:00:00Z',
        },
      ],
      total: 1,
    })
    ;(getReviewQueue as any).mockResolvedValue({
      items: [
        {
          item_id: 'r1',
          title: '审查项 1',
          type: 'entity',
          status: 'pending',
          created_at: '2026-07-01T00:00:00Z',
        },
      ],
      total: 1,
    })
  }

  function mountView() {
    return mount(DashboardView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态：onMounted 触发后 loading true、各 stats null、列表空', () => {
    setupMocks()
    const wrapper = mountView()
    const vm = wrapper.vm as any
    // onMounted 已触发 fetchData，loading 为 true（异步加载中）
    expect(vm.loading).toBe(true)
    expect(vm.documentStats).toBe(null)
    expect(vm.graphStats).toBe(null)
    expect(vm.reviewStats).toBe(null)
    expect(vm.searchStats).toBe(null)
    expect(vm.recentDocuments).toEqual([])
    expect(vm.recentReviews).toEqual([])
  })

  it('onMounted 调用 fetchData 并行加载 6 个 API', async () => {
    setupMocks()
    mountView()
    await flushPromises()
    expect(getDocumentStats).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith('/graph/stats')
    expect(getReviewStats).toHaveBeenCalledTimes(1)
    expect(getSearchStats).toHaveBeenCalledTimes(1)
    expect(listDocuments).toHaveBeenCalledWith({ limit: 5 })
    expect(getReviewQueue).toHaveBeenCalledWith(undefined, 5, 0)
  })

  it('fetchData 成功后填充 stats 与列表', async () => {
    setupMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(false)
    expect(vm.documentStats).not.toBeNull()
    expect(vm.documentStats.total_documents).toBe(10)
    expect(vm.graphStats.nodes).toBe(20)
    expect(vm.reviewStats.total_pending).toBe(3)
    expect(vm.searchStats.total_indexed).toBe(8)
    expect(vm.recentDocuments).toHaveLength(1)
    expect(vm.recentReviews).toHaveLength(1)
  })

  it('fetchData 失败时 loading 恢复 false，不抛错', async () => {
    ;(getDocumentStats as any).mockRejectedValue(new Error('network'))
    ;(api.get as any).mockRejectedValue(new Error('network'))
    ;(getReviewStats as any).mockRejectedValue(new Error('network'))
    ;(getSearchStats as any).mockRejectedValue(new Error('network'))
    ;(listDocuments as any).mockRejectedValue(new Error('network'))
    ;(getReviewQueue as any).mockRejectedValue(new Error('network'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(false)
    // 失败时各值保持 null（Promise.all 任何一个 reject 都进入 catch）
    expect(vm.documentStats).toBe(null)
  })

  it('formatFileSize：B/KB/MB 转换', () => {
    setupMocks()
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.formatFileSize(500)).toBe('500 B')
    expect(vm.formatFileSize(1024)).toBe('1.0 KB')
    expect(vm.formatFileSize(1048576)).toBe('1.0 MB')
  })

  it('formatDate：ISO 字符串转本地化', () => {
    setupMocks()
    const wrapper = mountView()
    const vm = wrapper.vm as any
    const result = vm.formatDate('2026-07-01T00:00:00Z')
    // 不校验具体时区输出，只确认是字符串且含 2026
    expect(typeof result).toBe('string')
    expect(result).toContain('2026')
  })
})
