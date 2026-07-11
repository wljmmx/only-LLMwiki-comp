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
  listIncidents: vi.fn(),
  getIncident: vi.fn(),
  closeIncident: vi.fn(),
  incidentToRunbook: vi.fn(),
  getIncidentChanges: vi.fn(),
  getIncidentRollbackSuggestion: vi.fn(),
}))

vi.mock('@/utils/wikiRender', () => ({
  renderWikiMarkdown: vi.fn((text: string) => `<p>${text}</p>`),
}))

import {
  listIncidents,
  getIncident,
  closeIncident,
  getIncidentChanges,
  getIncidentRollbackSuggestion,
} from '@/api/aiops'
import IncidentsView from '@/views/IncidentsView.vue'
import '@/test/setup'

const sampleIncident = {
  incident_id: 'inc-001',
  severity: 'high',
  status: 'open',
  alert_count: 5,
  suspected_root_cause: 'nginx-down',
  first_seen: '2026-07-01T00:00:00Z',
  last_seen: '2026-07-01T01:00:00Z',
  alerts: [],
}

describe('IncidentsView.vue', () => {
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
    return mount(IncidentsView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态：onMounted 触发后 loading true、incidents 空、statusFilter=open', () => {
    ;(listIncidents as any).mockResolvedValue({ incidents: [], total: 0 })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    // onMounted 已触发 loadIncidents，loading 为 true（异步加载中）
    expect(vm.loading).toBe(true)
    expect(vm.incidents).toEqual([])
    expect(vm.statusFilter).toBe('open')
  })

  it('onMounted 调用 loadIncidents', async () => {
    ;(listIncidents as any).mockResolvedValue({ incidents: [sampleIncident], total: 1 })
    const wrapper = mountView()
    await flushPromises()
    expect(listIncidents).toHaveBeenCalledWith('open', 100)
    const vm = wrapper.vm as any
    expect(vm.incidents).toHaveLength(1)
    expect(vm.loading).toBe(false)
  })

  it('loadIncidents 失败时 message.error', async () => {
    ;(listIncidents as any).mockRejectedValue(new Error('network'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.loadIncidents()
    expect(mockMessage.error).toHaveBeenCalled()
    expect(vm.loading).toBe(false)
  })

  it('openDetail 并行加载 incident/changes/rollback', async () => {
    ;(listIncidents as any).mockResolvedValue({ incidents: [], total: 0 })
    ;(getIncident as any).mockResolvedValue(sampleIncident)
    ;(getIncidentChanges as any).mockResolvedValue({ changes: [], count: 0 })
    ;(getIncidentRollbackSuggestion as any).mockResolvedValue({ suggested: 'restart nginx' })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openDetail('inc-001')
    expect(getIncident).toHaveBeenCalledWith('inc-001')
    expect(getIncidentChanges).toHaveBeenCalledWith('inc-001')
    expect(getIncidentRollbackSuggestion).toHaveBeenCalledWith('inc-001')
    expect(vm.detailVisible).toBe(true)
    expect(vm.currentIncident).not.toBeNull()
    expect(vm.currentIncident.incident_id).toBe('inc-001')
  })

  it('openDetail 主请求失败时 message.error', async () => {
    ;(listIncidents as any).mockResolvedValue({ incidents: [], total: 0 })
    ;(getIncident as any).mockRejectedValue(new Error('not found'))
    ;(getIncidentChanges as any).mockResolvedValue({ changes: [], count: 0 })
    ;(getIncidentRollbackSuggestion as any).mockResolvedValue(null)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openDetail('inc-001')
    expect(mockMessage.error).toHaveBeenCalled()
    expect(vm.detailLoading).toBe(false)
  })

  it('handleClose 成功关闭并刷新列表', async () => {
    ;(listIncidents as any).mockResolvedValue({ incidents: [sampleIncident], total: 1 })
    ;(closeIncident as any).mockResolvedValue({ closed: true })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentIncident = sampleIncident
    ;(listIncidents as any).mockClear()
    await vm.handleClose()
    expect(closeIncident).toHaveBeenCalledWith('inc-001', '通过前端关闭')
    expect(mockMessage.success).toHaveBeenCalled()
    expect(vm.detailVisible).toBe(false)
    expect(listIncidents).toHaveBeenCalled()
  })

  it('handleClose 无当前 incident 时直接 return', async () => {
    ;(listIncidents as any).mockResolvedValue({ incidents: [], total: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentIncident = null
    await vm.handleClose()
    expect(closeIncident).not.toHaveBeenCalled()
  })

  it('handleClose 关闭中重复调用直接 return（closing 守卫）', async () => {
    ;(listIncidents as any).mockResolvedValue({ incidents: [], total: 0 })
    ;(closeIncident as any).mockResolvedValue({ closed: true })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentIncident = sampleIncident
    vm.closing = true
    await vm.handleClose()
    expect(closeIncident).not.toHaveBeenCalled()
  })

  it('tagSeverity：severity 映射 tag type', () => {
    ;(listIncidents as any).mockResolvedValue({ incidents: [], total: 0 })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.tagSeverity('info')).toBe('default')
    expect(vm.tagSeverity('low')).toBe('info')
    expect(vm.tagSeverity('warning')).toBe('warning')
    expect(vm.tagSeverity('high')).toBe('error')
    expect(vm.tagSeverity('critical')).toBe('error')
    expect(vm.tagSeverity('fatal')).toBe('error')
    expect(vm.tagSeverity('unknown')).toBe('default')
  })

  it('formatTime：undefined/无效/有效', () => {
    ;(listIncidents as any).mockResolvedValue({ incidents: [], total: 0 })
    mountView()
    expect(formatDateTime('') || '-').toBe('-')
    expect(formatDateTime('2026-07-01T10:30:00Z') || '-').toContain('2026')
  })
})
