import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// mock vue-router
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

// P1-4: mock queryWikiStream（替代 queryWiki）
// 捕获回调以便测试中模拟 SSE 事件
let capturedCallbacks: {
  onMeta: (data: any) => void
  onDelta: (text: string) => void
  onDone: (data: any) => void
  onError?: (message: string) => void
} | null = null
let lastAbortController: AbortController | null = null

vi.mock('@/api/wiki', () => ({
  queryWikiStream: vi.fn((question: string, callbacks: any) => {
    capturedCallbacks = callbacks
    lastAbortController = new AbortController()
    return lastAbortController
  }),
}))

// mock renderWikiMarkdown（避免 marked/DOMPurify 在 jsdom 环境的副作用）
vi.mock('@/utils/wikiRender', () => ({
  renderWikiMarkdown: (text: string) => text,
}))

import { queryWikiStream } from '@/api/wiki'
import WikiQueryView from '@/views/WikiQueryView.vue'
import '@/test/setup'

describe('WikiQueryView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    capturedCallbacks = null
    lastAbortController = null
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

  it('handleQuery 空问题时直接 return，不调用 queryWikiStream', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.handleQuery()
    expect(queryWikiStream).not.toHaveBeenCalled()
  })

  it('handleQuery 流式成功：meta → delta(多次) → done，result 填充、loading 恢复 false', async () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.question = '什么是 nginx'
    vm.handleQuery()

    // 发起查询后立即处于 loading
    expect(vm.loading).toBe(true)
    expect(vm.asked).toBe(true)
    expect(queryWikiStream).toHaveBeenCalledWith('什么是 nginx', expect.any(Object))

    // 模拟 meta 事件
    capturedCallbacks!.onMeta({
      recalled_pages: [
        { slug: 'nginx-502-troubleshooting', title: 'Nginx 502', type: 'incident', score: 0.92 },
      ],
      cited_slugs: ['nginx-502-troubleshooting'],
      insufficient_knowledge: false,
    })
    expect(vm.result.recalled_pages).toHaveLength(1)
    expect(vm.result.cited_slugs).toEqual(['nginx-502-troubleshooting'])

    // 模拟 delta 事件（多次增量）
    capturedCallbacks!.onDelta('Nginx ')
    capturedCallbacks!.onDelta('是反向代理')
    expect(vm.streamingAnswer).toBe('Nginx 是反向代理')

    // 模拟 done 事件
    capturedCallbacks!.onDone({ writebacks: [] })
    await flushPromises()

    expect(vm.loading).toBe(false)
    expect(vm.result.answer).toBe('Nginx 是反向代理')
    expect(vm.hasAnswer).toBe(true)
    expect(vm.hasError).toBe(false)
  })

  it('handleQuery 流式错误：onError → result.error 填充、loading false', async () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.question = 'test'
    vm.handleQuery()
    expect(vm.loading).toBe(true)

    capturedCallbacks!.onError!('LLM 不可用')
    await flushPromises()

    expect(vm.loading).toBe(false)
    expect(vm.result.error).toBe('LLM 不可用')
    expect(vm.hasError).toBe(true)
    expect(vm.hasAnswer).toBe(false)
  })

  it('insufficient_knowledge=true 时 hasAnswer 为 false', async () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.question = 'test'
    vm.handleQuery()

    capturedCallbacks!.onMeta({
      recalled_pages: [],
      cited_slugs: [],
      insufficient_knowledge: true,
      answer: '知识库不足',
    })
    capturedCallbacks!.onDone({ writebacks: [] })
    await flushPromises()

    expect(vm.result.insufficient_knowledge).toBe(true)
    expect(vm.hasAnswer).toBe(false)
    expect(vm.hasError).toBe(false)
  })

  it('handleStop：取消流式、保留已收文本、loading false', async () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.question = 'test'
    vm.handleQuery()

    capturedCallbacks!.onDelta('部分回答')
    expect(vm.streamingAnswer).toBe('部分回答')
    expect(vm.loading).toBe(true)

    vm.handleStop()
    await flushPromises()

    expect(vm.loading).toBe(false)
    expect(vm.result.answer).toBe('部分回答')
    expect(lastAbortController?.signal.aborted).toBe(true)
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
