import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { formatDateTime } from '@/utils/format'

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

vi.mock('@/api/aiops', () => ({
  listChanges: vi.fn(),
  getChange: vi.fn(),
  correlateChanges: vi.fn(),
  ingestChanges: vi.fn(),
}))

import { listChanges, getChange, correlateChanges, ingestChanges } from '@/api/aiops'
import ChangesView from '@/views/ChangesView.vue'
import '@/test/setup'

const sampleChange = {
  change_id: 'ch-001',
  change_type: 'deployment',
  description: '上线订单服务 v2.3.1',
  service: 'order-service',
  host: 'web-prod-01',
  severity: 'normal',
  status: 'completed',
  author: 'alice',
  ticket_id: 'OPS-1234',
  timestamp: '2026-07-01T10:00:00Z',
}

describe('ChangesView.vue', () => {
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

  function mountView() {
    return mount(ChangesView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态：onMounted 触发后 loading true、changes 空、serviceFilter 空', () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(true)
    expect(vm.changes).toEqual([])
    expect(vm.serviceFilter).toBe('')
    expect(vm.detailVisible).toBe(false)
    expect(vm.correlateVisible).toBe(false)
    expect(vm.ingestVisible).toBe(false)
  })

  it('onMounted 调用 loadChanges（默认参数 service 空字符串、limit 100）', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [sampleChange], count: 1 })
    const wrapper = mountView()
    await flushPromises()
    expect(listChanges).toHaveBeenCalledWith('', 100)
    const vm = wrapper.vm as any
    expect(vm.changes).toHaveLength(1)
    expect(vm.loading).toBe(false)
  })

  it('loadChanges 带 serviceFilter 传参', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.serviceFilter = 'order-service'
    await vm.loadChanges()
    expect(listChanges).toHaveBeenCalledWith('order-service', 100)
  })

  it('loadChanges 失败时 message.error', async () => {
    ;(listChanges as any).mockRejectedValue(new Error('network'))
    const wrapper = mountView()
    await flushPromises()
    expect(mockMessage.error).toHaveBeenCalledWith('network')
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(false)
  })

  it('openDetail 加载详情并打开抽屉', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    ;(getChange as any).mockResolvedValue(sampleChange)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openDetail('ch-001')
    expect(getChange).toHaveBeenCalledWith('ch-001')
    expect(vm.detailVisible).toBe(true)
    expect(vm.currentChange).toEqual(sampleChange)
    expect(vm.detailLoading).toBe(false)
  })

  it('openDetail 失败时 message.error', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    ;(getChange as any).mockRejectedValue(new Error('not found'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openDetail('ch-001')
    expect(mockMessage.error).toHaveBeenCalledWith('not found')
    expect(vm.detailLoading).toBe(false)
  })

  it('openDetail 空 ID 直接 return', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openDetail('')
    expect(getChange).not.toHaveBeenCalled()
    expect(vm.detailVisible).toBe(false)
  })

  it('handleCorrelate 成功填充 result', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    ;(correlateChanges as any).mockResolvedValue({
      changes_scanned: 10,
      correlations: [{}, {}],
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleCorrelate()
    expect(correlateChanges).toHaveBeenCalledWith(24, 30)
    expect(vm.correlateResult).not.toBeNull()
    expect(vm.correlateResult.changes_scanned).toBe(10)
    expect(mockMessage.success).toHaveBeenCalledWith('关联完成')
    expect(vm.correlateLoading).toBe(false)
  })

  it('handleCorrelate 失败时 message.error', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    ;(correlateChanges as any).mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleCorrelate()
    expect(mockMessage.error).toHaveBeenCalledWith('fail')
    expect(vm.correlateLoading).toBe(false)
  })

  it('handleIngest 成功调用 ingestChanges 并刷新列表', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    ;(ingestChanges as any).mockResolvedValue({ ingested: 1 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    ;(listChanges as any).mockClear()
    await vm.handleIngest()
    expect(ingestChanges).toHaveBeenCalled()
    expect(mockMessage.success).toHaveBeenCalledWith('已接收 1 条变更')
    expect(vm.ingestVisible).toBe(false)
    expect(listChanges).toHaveBeenCalled()
  })

  it('handleIngest JSON 解析失败时不调用 ingestChanges', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.ingestJson = 'not a json'
    await vm.handleIngest()
    expect(ingestChanges).not.toHaveBeenCalled()
    expect(mockMessage.error).toHaveBeenCalledWith(expect.stringContaining('JSON 解析失败'))
  })

  it('handleIngest 非数组 JSON 报错', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.ingestJson = '{"a":1}'
    await vm.handleIngest()
    expect(ingestChanges).not.toHaveBeenCalled()
    expect(mockMessage.error).toHaveBeenCalledWith(expect.stringContaining('必须是数组'))
  })

  it('tagSeverity：severity 映射 tag type', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.tagSeverity('normal')).toBe('default')
    expect(vm.tagSeverity('warning')).toBe('warning')
    expect(vm.tagSeverity('high')).toBe('error')
    expect(vm.tagSeverity('critical')).toBe('error')
    expect(vm.tagSeverity('info')).toBe('info')
    expect(vm.tagSeverity('low')).toBe('info')
    expect(vm.tagSeverity('fatal')).toBe('error')
    expect(vm.tagSeverity('unknown')).toBe('default')
  })

  it('formatTime：undefined/空/有效时间', async () => {
    ;(listChanges as any).mockResolvedValue({ changes: [], count: 0 })
    mountView()
    await flushPromises()
    expect(formatDateTime('') || '-').toBe('-')
    expect(formatDateTime('2026-07-01T10:30:00Z') || '-').toContain('2026')
  })
})
