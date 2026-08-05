import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// WikiView 嵌套 WikiVersionHistory，其 useMessage 需 provider，测试中 mock 掉
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useMessage: () => ({
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
      info: vi.fn(),
    }),
  }
})

vi.mock('@/api/wiki', () => ({
  listWikiPages: vi.fn(),
  getWikiPage: vi.fn(),
  getWikiBacklinks: vi.fn(),
  deleteWikiPage: vi.fn(),
  getWikiHeadingTree: vi.fn().mockResolvedValue({ heading_tree: [] }),
}))

// P1-12a: mock vue-router useRoute（可覆盖 route.query.slug）
const mockRoute = { query: {} as Record<string, any> }
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
}))

vi.mock('@/utils/wikiRender', () => ({
  renderWikiMarkdown: vi.fn((text: string) => `<p>${text}</p>`),
  parseSlugFromHash: vi.fn(() => null),
}))

// S16-1：mock useCollab 避免 WikiView 测试触发真实 WebSocket
// S16-3：mock 需补全 events 字段，否则 CollabPanel 的 reversedEvents computed 报 undefined
vi.mock('@/composables/useCollab', () => ({
  useCollab: () => ({
    onlineUsers: { value: [] },
    lockHolder: { value: null },
    connectionState: { value: 'disconnected' },
    lastError: { value: '' },
    events: { value: [] },
    hasLock: { value: false },
    onlineCount: { value: 0 },
    connect: vi.fn(),
    disconnect: vi.fn(),
    acquireLock: vi.fn(),
    releaseLock: vi.fn(),
  }),
}))

// mock 子组件，避免深度渲染带来的依赖问题
vi.mock('@/components/wiki/WikiSidebar.vue', () => ({
  default: { name: 'WikiSidebar', template: '<div class="wiki-sidebar-stub" />' },
}))
vi.mock('@/components/wiki/WikiContent.vue', () => ({
  default: { name: 'WikiContent', template: '<div class="wiki-content-stub" />' },
}))
vi.mock('@/components/wiki/WikiVersionHistory.vue', () => ({
  default: { name: 'WikiVersionHistory', template: '<div class="wiki-version-history-stub" />' },
}))
vi.mock('@/components/wiki/WikiHeadingTree.vue', () => ({
  default: { name: 'WikiHeadingTree', template: '<div class="wiki-heading-tree-stub" />' },
}))

import { listWikiPages, getWikiPage, getWikiBacklinks } from '@/api/wiki'
import WikiView from '@/views/WikiView.vue'
import '@/test/setup'

const samplePage = {
  slug: 'nginx-502-troubleshooting',
  title: 'Nginx 502 故障排查',
  type: 'incident',
  content: '## 概述\n502 错误排查。',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
}

describe('WikiView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    mockRoute.query = {}
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView() {
    return mount(WikiView, {
      global: {
        plugins: [pinia],
        config: {
          errorHandler: () => {
            // 抑制 headingTree 初始为 null 时模板渲染的 TypeError
          },
        },
      },
    })
  }

  it('初始状态：treeLoading true、pages 空、currentPage null', () => {
    ;(listWikiPages as any).mockResolvedValue({ pages: [], total: 0 })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.treeLoading).toBe(true)
    expect(vm.pages).toEqual([])
    expect(vm.currentPage).toBe(null)
  })

  it('onMounted 调用 loadPages 加载页面列表', async () => {
    ;(listWikiPages as any).mockResolvedValue({ pages: [samplePage], total: 1 })
    ;(getWikiPage as any).mockResolvedValue(samplePage)
    ;(getWikiBacklinks as any).mockResolvedValue([])
    const wrapper = mountView()
    await flushPromises()
    expect(listWikiPages).toHaveBeenCalledTimes(1)
    const vm = wrapper.vm as any
    expect(vm.pages).toHaveLength(1)
    expect(vm.treeLoading).toBe(false)
  })

  it('loadPages 有页面时自动选中第一个并加载内容', async () => {
    ;(listWikiPages as any).mockResolvedValue({ pages: [samplePage], total: 1 })
    ;(getWikiPage as any).mockResolvedValue(samplePage)
    ;(getWikiBacklinks as any).mockResolvedValue([])
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.selectedKey).toBe('nginx-502-troubleshooting')
    expect(getWikiPage).toHaveBeenCalledWith('nginx-502-troubleshooting')
    expect(vm.currentPage).not.toBeNull()
    expect(vm.currentPage.title).toBe('Nginx 502 故障排查')
  })

  it('loadPages 无页面时不自动加载', async () => {
    ;(listWikiPages as any).mockResolvedValue({ pages: [], total: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.selectedKey).toBe(null)
    expect(getWikiPage).not.toHaveBeenCalled()
  })

  it('renderedContent computed 渲染当前页 markdown', async () => {
    ;(listWikiPages as any).mockResolvedValue({ pages: [samplePage], total: 1 })
    ;(getWikiPage as any).mockResolvedValue(samplePage)
    ;(getWikiBacklinks as any).mockResolvedValue([])
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.renderedContent).toContain('<p>')
  })

  it('renderedContent 无当前页时为空字符串', async () => {
    ;(listWikiPages as any).mockResolvedValue({ pages: [], total: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.renderedContent).toBe('')
  })

  // ────────── P1-12a: ?slug= query 跳转 ──────────

  it('loadPages 优先选中 ?slug= query 指定的页面', async () => {
    const pages = [
      samplePage,
      { ...samplePage, slug: 'reverse-proxy', title: '反向代理', type: 'concept' },
    ]
    ;(listWikiPages as any).mockResolvedValue({ pages, total: 2 })
    ;(getWikiPage as any).mockImplementation((slug: string) =>
      Promise.resolve(pages.find((p) => p.slug === slug) || pages[0]),
    )
    ;(getWikiBacklinks as any).mockResolvedValue([])

    mockRoute.query = { slug: 'reverse-proxy' }
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any

    expect(vm.selectedKey).toBe('reverse-proxy')
    expect(getWikiPage).toHaveBeenCalledWith('reverse-proxy')
    expect(vm.currentPage.title).toBe('反向代理')
  })

  it('?slug= 指向不存在的页面时回退到首个', async () => {
    ;(listWikiPages as any).mockResolvedValue({ pages: [samplePage], total: 1 })
    ;(getWikiPage as any).mockResolvedValue(samplePage)
    ;(getWikiBacklinks as any).mockResolvedValue([])

    mockRoute.query = { slug: 'nonexistent' }
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any

    expect(vm.selectedKey).toBe('nginx-502-troubleshooting')
    expect(getWikiPage).toHaveBeenCalledWith('nginx-502-troubleshooting')
  })

  it('无 ?slug= query 时选中首个', async () => {
    ;(listWikiPages as any).mockResolvedValue({ pages: [samplePage], total: 1 })
    ;(getWikiPage as any).mockResolvedValue(samplePage)
    ;(getWikiBacklinks as any).mockResolvedValue([])

    mockRoute.query = {}
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any

    expect(vm.selectedKey).toBe('nginx-502-troubleshooting')
  })
})
