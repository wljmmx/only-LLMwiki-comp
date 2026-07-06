import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// mock updateWikiPage API
const mockUpdateWikiPage = vi.fn()
vi.mock('@/api/wiki', () => ({
  updateWikiPage: (...args: any[]) => mockUpdateWikiPage(...args),
}))

// mock renderWikiMarkdown（返回原文本加包裹，便于断言）
vi.mock('@/utils/wikiRender', () => ({
  renderWikiMarkdown: vi.fn((text: string) => `<p>${text}</p>`),
  parseSlugFromHash: vi.fn(() => null),
}))

import WikiEditor from '@/components/wiki/WikiEditor.vue'
import type { WikiPageUpdateResult } from '@/types/api'
import '@/test/setup'

describe('components/wiki/WikiEditor.vue — S16-2 Wiki 编辑器', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountEditor(overrides: { content?: string; canEdit?: boolean; version?: number } = {}) {
    return mount(WikiEditor, {
      props: {
        slug: 'nginx-502',
        content: overrides.content ?? '---\nslug: nginx-502\n---\n\n原文',
        version: overrides.version ?? 1,
        canEdit: overrides.canEdit ?? true,
      },
      global: { plugins: [pinia] },
    })
  }

  // ────────── 渲染 ──────────

  it('渲染标题"编辑页面"+ 取消/保存按钮', () => {
    const wrapper = mountEditor()
    expect(wrapper.text()).toContain('编辑页面')
    expect(wrapper.text()).toContain('取消')
    expect(wrapper.text()).toContain('保存')
  })

  it('初始内容显示在 textarea', () => {
    const wrapper = mountEditor({ content: '唯一初始内容 XYZ' })
    // NInput stub 化，textarea 不可直接断言 value；
    // 但可以断言 preview 区域包含内容
    expect(wrapper.text()).toContain('唯一初始内容 XYZ')
  })

  it('预览区域渲染 previewHtml', () => {
    const wrapper = mountEditor({ content: 'PREVIEW_TEST' })
    expect(wrapper.html()).toContain('<p>PREVIEW_TEST</p>')
  })

  // ────────── dirty 状态 ──────────

  it('初始 isDirty=false 显示"已保存"', () => {
    const wrapper = mountEditor()
    expect(wrapper.text()).toContain('已保存')
  })

  // ────────── canEdit 控制 ──────────

  it('canEdit=false 显示警告提示', () => {
    const wrapper = mountEditor({ canEdit: false })
    expect(wrapper.text()).toContain('未持有编辑锁')
  })

  it('canEdit=true 不显示警告提示', () => {
    const wrapper = mountEditor({ canEdit: true })
    expect(wrapper.text()).not.toContain('未持有编辑锁')
  })

  // ────────── 保存流程 ──────────

  it('保存成功 → emit saved + 调用 updateWikiPage', async () => {
    const result: WikiPageUpdateResult = {
      slug: 'nginx-502',
      title: '测试',
      version: 2,
      checksum: 'abc123',
      created_at: '2026-07-06T00:00:00Z',
      skipped: false,
    }
    mockUpdateWikiPage.mockResolvedValue(result)

    const wrapper = mountEditor()
    // 修改内容（textarea stub 不接受 input，直接设 editingContent）
    const vm = wrapper.vm as any
    vm.editingContent = '---\nslug: nginx-502\n---\n\n新内容'
    await wrapper.vm.$nextTick()

    // 点击保存
    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
    expect(saveBtn).toBeDefined()
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockUpdateWikiPage).toHaveBeenCalledTimes(1)
    expect(mockUpdateWikiPage).toHaveBeenCalledWith('nginx-502', expect.objectContaining({
      content: '---\nslug: nginx-502\n---\n\n新内容',
      expected_version: 1,
    }))
    const savedEvents = wrapper.emitted('saved')
    expect(savedEvents).toBeDefined()
    expect(savedEvents![0][0]).toEqual(result)
  })

  it('保存失败 → 显示错误信息 + 不 emit saved', async () => {
    mockUpdateWikiPage.mockRejectedValue({
      response: { data: { detail: '页面正被 user:bob 编辑' } },
    })

    const wrapper = mountEditor()
    const vm = wrapper.vm as any
    vm.editingContent = '---\nslug: nginx-502\n---\n\n修改'
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('页面正被 user:bob 编辑')
    expect(wrapper.emitted('saved')).toBeUndefined()
  })

  it('内容无变化时保存按钮禁用', () => {
    const wrapper = mountEditor()
    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
    // NButton stub 化为 <button>，disabled 属性透传
    expect(saveBtn?.attributes('disabled')).toBeDefined()
  })

  // ────────── 取消流程 ──────────

  it('点击取消 → emit cancel', async () => {
    const wrapper = mountEditor()
    const cancelBtn = wrapper.findAll('button').find((b) => b.text() === '取消')
    await cancelBtn!.trigger('click')
    const cancelEvents = wrapper.emitted('cancel')
    expect(cancelEvents).toBeDefined()
    expect(cancelEvents!.length).toBe(1)
  })

  // ────────── slug 切换重置 ──────────

  it('content prop 变化 → editingContent 重置', async () => {
    const wrapper = mountEditor({ content: '原内容' })
    const vm = wrapper.vm as any
    vm.editingContent = '用户修改'
    await wrapper.vm.$nextTick()
    expect(vm.editingContent).toBe('用户修改')

    // 切换 slug（父组件会传新 content）
    await wrapper.setProps({ content: '新页面内容', slug: 'other-page' })
    expect(vm.editingContent).toBe('新页面内容')
  })

  // ────────── 变更摘要 ──────────

  it('保存时传 change_summary', async () => {
    mockUpdateWikiPage.mockResolvedValue({
      slug: 'nginx-502', title: 'T', version: 2, checksum: 'c',
      created_at: '2026-07-06T00:00:00Z', skipped: false,
    })

    const wrapper = mountEditor()
    const vm = wrapper.vm as any
    vm.editingContent = '---\nslug: nginx-502\n---\n\n新内容'
    vm.changeSummary = '修正错别字'
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockUpdateWikiPage).toHaveBeenCalledWith('nginx-502', expect.objectContaining({
      change_summary: '修正错别字',
    }))
  })

  it('changeSummary 为空时传 undefined', async () => {
    mockUpdateWikiPage.mockResolvedValue({
      slug: 'nginx-502', title: 'T', version: 2, checksum: 'c',
      created_at: '2026-07-06T00:00:00Z', skipped: false,
    })

    const wrapper = mountEditor()
    const vm = wrapper.vm as any
    vm.editingContent = '---\nslug: nginx-502\n---\n\n新'
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockUpdateWikiPage).toHaveBeenCalledWith('nginx-502', expect.objectContaining({
      change_summary: undefined,
    }))
  })

  // ────────── canEdit 控制保存按钮 ──────────

  it('canEdit=false 时保存按钮禁用', () => {
    const wrapper = mountEditor({ canEdit: false })
    const vm = wrapper.vm as any
    vm.editingContent = '修改后'
    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
    expect(saveBtn?.attributes('disabled')).toBeDefined()
  })

  // ────────── version 传递 ──────────

  it('version prop 传给 expected_version', async () => {
    mockUpdateWikiPage.mockResolvedValue({
      slug: 'nginx-502', title: 'T', version: 6, checksum: 'c',
      created_at: '2026-07-06T00:00:00Z', skipped: false,
    })

    const wrapper = mountEditor({ version: 5 })
    const vm = wrapper.vm as any
    vm.editingContent = '新内容'
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockUpdateWikiPage).toHaveBeenCalledWith('nginx-502', expect.objectContaining({
      expected_version: 5,
    }))
  })
})
