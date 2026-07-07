import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// mock vue-router
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

// mock @/api/wiki
vi.mock('@/api/wiki', () => ({
  queryWiki: vi.fn(),
}))

import { queryWiki } from '@/api/wiki'
import WikiQueryView from '@/views/WikiQueryView.vue'
import '@/test/setup'

describe('WikiQueryView.vue', () => {
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
    return mount(WikiQueryView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态：question 为空、loading false、asked false、result null', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.question).toBe('')
    expect(vm.loading).toBe(false)
    expect(vm.asked).toBe(false)
    expect(vm.result).toBe(null)
  })

  it('hasError / hasAnswer computed 在 result null 时为 false', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.hasError).toBe(false)
    expect(vm.hasAnswer).toBe(false)
  })

  it('handleQuery 空问题时直接 return，不调用 queryWiki', async () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.handleQuery()
    expect(queryWiki).not.toHaveBeenCalled()
  })

  it('handleQuery 成功：result 填充、loading 恢复 false', async () => {
    ;(queryWiki as any).mockResolvedValue({
      question: '什么是 nginx',
      answer: 'Nginx 是反向代理',
      cited_slugs: ['nginx-502-troubleshooting'],
      recalled_pages: [
        { slug: 'nginx-502-troubleshooting', title: 'Nginx 502', type: 'incident', score: 0.92 },
      ],
      insufficient_knowledge: false,
      error: null,
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.question = '什么是 nginx'
    vm.handleQuery()
    expect(vm.loading).toBe(true)
    expect(vm.asked).toBe(true)
    await flushPromises()
    expect(vm.loading).toBe(false)
    expect(vm.result).not.toBeNull()
    expect(vm.result.answer).toBe('Nginx 是反向代理')
    expect(vm.hasAnswer).toBe(true)
    expect(vm.hasError).toBe(false)
    expect(queryWiki).toHaveBeenCalledWith('什么是 nginx')
  })

  it('handleQuery 失败：result 填充 error 字段', async () => {
    ;(queryWiki as any).mockRejectedValue(new Error('LLM 不可用'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.question = 'test'
    vm.handleQuery()
    await flushPromises()
    expect(vm.result).not.toBeNull()
    expect(vm.result.error).toBe('LLM 不可用')
    expect(vm.hasError).toBe(true)
    expect(vm.hasAnswer).toBe(false)
  })

  it('insufficient_knowledge=true 时 hasAnswer 为 false', async () => {
    ;(queryWiki as any).mockResolvedValue({
      question: 'test',
      answer: '',
      cited_slugs: [],
      recalled_pages: [],
      insufficient_knowledge: true,
      error: null,
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.question = 'test'
    vm.handleQuery()
    await flushPromises()
    expect(vm.hasAnswer).toBe(false)
    expect(vm.hasError).toBe(false)
  })

  it('formatScore：0.92 → 92.0%', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.formatScore(0.92)).toBe('92.0%')
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
