import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/search', () => ({
  searchKnowledge: vi.fn(),
}))

import { searchKnowledge } from '@/api/search'
import SearchView from '@/views/SearchView.vue'
import '@/test/setup'

describe('SearchView.vue', () => {
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
    return mount(SearchView, {
      global: { plugins: [pinia] },
    })
  }

  it('组件能正确 mount 并显示空状态', () => {
    const wrapper = mountView()
    expect(wrapper.html()).toContain('知识搜索')
    expect(wrapper.html()).toContain('输入关键词')
  })

  it('初始状态：query 空、loading false、searched false、results 空、total 0', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.query).toBe('')
    expect(vm.loading).toBe(false)
    expect(vm.searched).toBe(false)
    expect(vm.results).toEqual([])
    expect(vm.total).toBe(0)
  })

  it('handleSearch 空查询时不调用 searchKnowledge', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.handleSearch()
    expect(searchKnowledge).not.toHaveBeenCalled()
  })

  it('handleSearch 成功后填充 results 与 total', async () => {
    ;(searchKnowledge as any).mockResolvedValue({
      query: 'nginx',
      results: [
        { id: 'r1', title: 'Nginx 502', snippet: 'snippet 1', score: 0.95, type: 'incident' },
        { id: 'r2', title: 'Nginx Config', snippet: 'snippet 2', score: 0.7, type: 'runbook' },
      ],
      count: 2,
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.query = 'nginx'
    vm.handleSearch()
    expect(vm.loading).toBe(true)
    expect(vm.searched).toBe(true)
    await flushPromises()
    expect(vm.loading).toBe(false)
    expect(vm.results).toHaveLength(2)
    expect(vm.total).toBe(2)
    expect(searchKnowledge).toHaveBeenCalledWith('nginx')
  })

  it('handleSearch 失败时清空 results 与 total', async () => {
    ;(searchKnowledge as any).mockRejectedValue(new Error('network'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.query = 'test'
    vm.handleSearch()
    await flushPromises()
    expect(vm.loading).toBe(false)
    expect(vm.results).toEqual([])
    expect(vm.total).toBe(0)
  })

  it('handleSearch 修剪空白查询后传给 API', async () => {
    ;(searchKnowledge as any).mockResolvedValue({ results: [], count: 0 })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.query = '  nginx  '
    vm.handleSearch()
    await flushPromises()
    expect(searchKnowledge).toHaveBeenCalledWith('nginx')
  })

  it('handleKeydown 按 Enter 时触发 handleSearch', async () => {
    ;(searchKnowledge as any).mockResolvedValue({ results: [], count: 0 })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.query = 'test'
    vm.handleKeydown({ key: 'Enter' } as KeyboardEvent)
    await flushPromises()
    expect(searchKnowledge).toHaveBeenCalledWith('test')
  })

  it('handleKeydown 按非 Enter 键时不触发搜索', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.query = 'test'
    vm.handleKeydown({ key: 'ArrowDown' } as KeyboardEvent)
    expect(searchKnowledge).not.toHaveBeenCalled()
  })

  it('formatScore：分数转百分比字符串', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.formatScore(0.95)).toBe('95.0%')
    expect(vm.formatScore(0)).toBe('0.0%')
    expect(vm.formatScore(1)).toBe('100.0%')
  })

  it('getScoreType：分数区间映射', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.getScoreType(0.85)).toBe('success')
    expect(vm.getScoreType(0.6)).toBe('info')
    expect(vm.getScoreType(0.4)).toBe('warning')
    expect(vm.getScoreType(0.2)).toBe('default')
  })
})
