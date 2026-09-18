import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('@/components/collab/CollabPanel.vue', () => ({
  default: { name: 'CollabPanel', template: '<div class="mock-collab"><slot /></div>' },
}))
vi.mock('@/components/wiki/WikiEditor.vue', () => ({
  default: { name: 'WikiEditor', template: '<div class="mock-editor"><slot /></div>' },
}))
vi.mock('@/utils/wikiRender', () => ({
  parseSlugFromHash: vi.fn(() => 'service-nginx'),
}))

import WikiContent from '@/components/wiki/WikiContent.vue'
import type { WikiPage, BacklinkItem } from '@/types/api'

const PAGE = {
  slug: 'service-nginx',
  title: 'Nginx 服务',
  type: 'service',
  tags: ['nginx', 'web'],
  content: 'x'.repeat(1200),
  version: 3,
  updated_at: '2026-01-01T00:00:00Z',
} as unknown as WikiPage

const BACKLINKS: BacklinkItem[] = [
  { slug: 'host-web1', title: 'Web1', context: '运行于' },
]

const PAGE_META = {
  updatedAt: '2026-01-01T00:00:00Z',
  version: 3,
  reviewStatus: 'auto',
  reviewStatusLabel: '自动通过',
  sourcesCount: 2,
}

const PARTIAL = {
  type: 'Partial',
  stubs: { NCard: true, NSpace: true, NTooltip: true, NTag: true, NSkeleton: true, NEmpty: true, NThing: true, NButton: true, NDivider: true },
}

function mountContent(overrides: Partial<Record<string, unknown>> = {}) {
  return mount(WikiContent, {
    props: {
      currentPage: PAGE,
      contentLoading: false,
      backlinks: BACKLINKS,
      isEditing: false,
      hasLock: true,
      selectedKey: null,
      renderedContent: '<p>内容</p>',
      pageMeta: PAGE_META,
      ...overrides,
    } as any,
    global: PARTIAL,
    attachTo: document.body,
  })
}

describe('components/wiki/WikiContent.vue — 页面内容区', () => {
  it('contentLoading 时展示骨架屏', () => {
    const wrapper = mountContent({ contentLoading: true })
    expect(wrapper.find('.content-skeleton').exists()).toBe(true)
  })

  it('无当前页面时展示空状态', () => {
    const wrapper = mountContent({ currentPage: null })
    expect(wrapper.text()).toContain('请选择一个页面')
  })

  it('渲染标题、类型跳、标签与元信息', () => {
    const wrapper = mountContent()
    expect(wrapper.text()).toContain('Nginx 服务')
    expect(wrapper.text()).toContain('服务')
    expect(wrapper.text()).toContain('#nginx')
    expect(wrapper.text()).toContain('#web')
    expect(wrapper.text()).toContain('v3')
    expect(wrapper.text()).toContain('自动通过')
    expect(wrapper.text()).toContain('来源 2')
  })

  it('计算阅读时间（字符数/400 向上取整）', () => {
    const wrapper = mountContent()
    expect(wrapper.text()).toContain('阅读 ~3 分钟')
  })

  it('hasLock=false 时编辑按钮禁用且文案为需先申请编辑锁', () => {
    const wrapper = mountContent({ hasLock: false })
    const btn = wrapper.find('.page-toolbar button')!
    expect(btn.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('需先申请编辑锁')
  })

  it('hasLock=true 时点击编辑按钮触发 start-editing', async () => {
    const wrapper = mountContent({ hasLock: true })
    await wrapper.find('.page-toolbar button').trigger('click')
    expect(wrapper.emitted('start-editing')).toHaveLength(1)
  })

  it('点击历史记录触发 toggle-version-history', async () => {
    const wrapper = mountContent()
    const btn = wrapper.findAll('.page-toolbar button')[1]!
    expect(btn.text()).toContain('历史记录')
    await btn.trigger('click')
    expect(wrapper.emitted('toggle-version-history')).toHaveLength(1)
  })

  it('点击反向链接项触发 backlink-click', async () => {
    const wrapper = mountContent()
    const item = wrapper.find('.backlink-item')!
    await item.trigger('click')
    expect(wrapper.emitted('backlink-click')![0]).toEqual(['host-web1'])
  })

  it('点击内容中的 <a href> 触发 content-click 并阻止默认', () => {
    const wrapper = mountContent({ renderedContent: '<a href="#/wiki/service-nginx">链接</a>' })
    const link = wrapper.find('.page-content a')
    link.trigger('click', { preventDefault: vi.fn() })
    expect(wrapper.emitted('content-click')![0]).toEqual(['service-nginx'])
  })
})