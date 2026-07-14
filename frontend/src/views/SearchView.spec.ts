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

  // ===== P1-15：highlightSnippet 关键词高亮 + XSS 防护 =====

  it('highlightSnippet：基本高亮，query 词被 mark 包裹', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    const out = vm.highlightSnippet('nginx 502 bad gateway', 'nginx')
    expect(out).toContain('<mark class="search-hit">nginx</mark>')
  })

  it('highlightSnippet：大小写不敏感匹配', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    const out = vm.highlightSnippet('Nginx NGINX nginx', 'nginx')
    // 三处均被包裹，且保留原文大小写
    const matches = out.match(/<mark class="search-hit">/g)
    expect(matches).toHaveLength(3)
    expect(out).toContain('<mark class="search-hit">Nginx</mark>')
    expect(out).toContain('<mark class="search-hit">NGINX</mark>')
    expect(out).toContain('<mark class="search-hit">nginx</mark>')
  })

  it('highlightSnippet：多词按空格分词，全部高亮', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    const out = vm.highlightSnippet('nginx 502 bad gateway', 'nginx 502')
    expect(out).toContain('<mark class="search-hit">nginx</mark>')
    expect(out).toContain('<mark class="search-hit">502</mark>')
  })

  it('highlightSnippet：XSS 防护 —— snippet 含 <script> 被转义，不产生可执行脚本', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    const out = vm.highlightSnippet('<script>alert(1)</script> nginx', 'nginx')
    // 不存在未转义的 <script> 标签
    expect(out).not.toContain('<script>')
    expect(out).not.toContain('</script>')
    // 转义后的实体存在
    expect(out).toContain('&lt;script&gt;')
    // 关键词仍被高亮
    expect(out).toContain('<mark class="search-hit">nginx</mark>')
  })

  it('highlightSnippet：XSS 防护 —— <img onerror> 标签被转义，不产生真实 img 元素', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    const out = vm.highlightSnippet('<img src=x onerror=alert(1)>', 'x')
    // 不存在真实的 <img 开始标签（已被转义为 &lt;img，onerror 仅作为无害纯文本）
    expect(out).not.toMatch(/<img[\s>]/i)
    // 转义实体存在
    expect(out).toContain('&lt;img')
    // 关键词仍被高亮
    expect(out).toContain('<mark class="search-hit">x</mark>')
  })

  it('highlightSnippet：空 snippet 返回空字符串', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.highlightSnippet('', 'nginx')).toBe('')
  })

  it('highlightSnippet：空 query 返回转义后的 snippet（不高亮）', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    const out = vm.highlightSnippet('a < b & c', '')
    expect(out).not.toContain('<mark')
    expect(out).toContain('&lt;')
    expect(out).toContain('&amp;')
  })

  it('highlightSnippet：query 含正则元字符不报错', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    // 元字符被当作字面量匹配，不应抛异常
    expect(() => vm.highlightSnippet('a.b*c', 'a.b*c')).not.toThrow()
  })
})
