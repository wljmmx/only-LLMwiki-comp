import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { formatFileSize, formatDateTime } from '@/utils/format'

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
      total: 10,
      total_size_mb: 0.01,
      by_format: [{ format: 'md', cnt: 5 }, { format: 'txt', cnt: 5 }],
      by_status: [{ status: 'parsed', cnt: 8 }, { status: 'failed', cnt: 1 }],
    })
    ;(api.get as any).mockResolvedValue({
      total_entities: 20,
      total_relations: 30,
      by_type: [{ type: 'host', count: 5 }, { type: 'service', count: 15 }],
    })
    ;(getReviewStats as any).mockResolvedValue({
      pending: 3,
      approved: 5,
      rejected: 1,
      modified: 0,
    })
    ;(getSearchStats as any).mockResolvedValue({
      indexed_docs: 8,
      vectorized_docs: 6,
      numpy_enabled: true,
    })
    ;(listDocuments as any).mockResolvedValue({
      documents: [
        {
          id: 'd1',
          doc_id: 'd1',
          filename: 'test.md',
          format: 'md',
          size: 1024,
          status: 'parsed',
          created_at: '2026-07-01T00:00:00Z',
        },
      ],
      stats: { total: 1, total_size_mb: 0.0, by_format: [], by_status: [] },
      limit: 5,
      offset: 0,
    })
    ;(getReviewQueue as any).mockResolvedValue({
      items: [
        {
          id: 'r1',
          title: '审查项 1',
          type: 'entity',
          status: 'pending',
          source_doc_id: 'd1',
          created_at: '2026-07-01T00:00:00Z',
        },
      ],
      stats: { pending: 3, approved: 5, rejected: 1, modified: 0 },
      total: 1,
      limit: 5,
      offset: 0,
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
    expect(vm.documentStats.total).toBe(10)
    expect(vm.graphStats.total_entities).toBe(20)
    expect(vm.reviewStats.pending).toBe(3)
    expect(vm.searchStats.indexed_docs).toBe(8)
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
    mountView()
    expect(formatFileSize(500)).toBe('500 B')
    expect(formatFileSize(1024)).toBe('1 KB')
    expect(formatFileSize(1048576)).toBe('1 MB')
  })

  it('formatDate：ISO 字符串转本地化', () => {
    setupMocks()
    mountView()
    const result = formatDateTime('2026-07-01T00:00:00Z')
    // 不校验具体时区输出，只确认是字符串且含 2026
    expect(typeof result).toBe('string')
    expect(result).toContain('2026')
  })
})
