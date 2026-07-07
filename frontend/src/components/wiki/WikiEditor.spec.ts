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
import { saveDraft, loadDraft, hasDraft } from '@/composables/useEditDraft'
import '@/test/setup'

describe('components/wiki/WikiEditor.vue — S16-2 Wiki 编辑器', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    localStorage.clear()
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

  // ────────── S16-5 草稿持久化与冲突恢复 ──────────

  describe('S16-5 草稿持久化与冲突恢复', () => {
    it('无草稿时不显示草稿恢复提示', () => {
      const wrapper = mountEditor()
      expect(wrapper.text()).not.toContain('恢复草稿')
      expect(wrapper.text()).not.toContain('丢弃草稿')
    })

    it('有草稿（版本一致）→ 显示"恢复未保存草稿"提示 + 恢复/丢弃按钮', () => {
      // 预先写入草稿（版本与 props.version 一致 = 1）
      saveDraft('nginx-502', '# 草稿内容', 1, 'old summary')
      const wrapper = mountEditor({ version: 1 })
      expect(wrapper.text()).toContain('未保存草稿')
      expect(wrapper.text()).toContain('恢复草稿')
      expect(wrapper.text()).toContain('丢弃草稿')
    })

    it('有草稿（版本不一致）→ 显示冲突提示（含版本号对比）', () => {
      // 草稿版本 1，但服务器版本已升到 3
      saveDraft('nginx-502', '# 草稿内容', 1)
      const wrapper = mountEditor({ version: 3 })
      expect(wrapper.text()).toContain('冲突')
      expect(wrapper.text()).toContain('1')
      expect(wrapper.text()).toContain('3')
    })

    it('点击"恢复草稿" → editingContent 变为草稿内容 + summary 恢复 + 提示消失', async () => {
      saveDraft('nginx-502', '# 草稿内容 XYZ', 1, '恢复的摘要')
      const wrapper = mountEditor({ content: '# 原文', version: 1 })
      const vm = wrapper.vm as any

      const restoreBtn = wrapper.findAll('button').find((b) => b.text() === '恢复草稿')
      expect(restoreBtn).toBeDefined()
      await restoreBtn!.trigger('click')

      expect(vm.editingContent).toBe('# 草稿内容 XYZ')
      expect(vm.changeSummary).toBe('恢复的摘要')
      // 提示消失
      expect(wrapper.text()).not.toContain('恢复草稿')
    })

    it('点击"丢弃草稿" → 草稿从 localStorage 清除 + 提示消失', async () => {
      saveDraft('nginx-502', '# 草稿内容', 1)
      expect(hasDraft('nginx-502')).toBe(true)
      const wrapper = mountEditor({ version: 1 })

      const discardBtn = wrapper.findAll('button').find((b) => b.text() === '丢弃草稿')
      expect(discardBtn).toBeDefined()
      await discardBtn!.trigger('click')

      expect(hasDraft('nginx-502')).toBe(false)
      expect(wrapper.text()).not.toContain('恢复草稿')
    })

    it('编辑内容变化时持久化草稿到 localStorage（含版本号）', async () => {
      const wrapper = mountEditor({ content: '# 原文', version: 5 })
      const vm = wrapper.vm as any
      vm.editingContent = '# 修改后内容'
      await wrapper.vm.$nextTick()

      expect(hasDraft('nginx-502')).toBe(true)
      const draft = loadDraft('nginx-502')
      expect(draft).not.toBeNull()
      expect(draft!.content).toBe('# 修改后内容')
      expect(draft!.version).toBe(5)
    })

    it('内容未变化时不持久化草稿', async () => {
      const wrapper = mountEditor({ content: '# 原文', version: 1 })
      // 不修改内容
      await wrapper.vm.$nextTick()
      expect(hasDraft('nginx-502')).toBe(false)
    })

    it('changeSummary 变化时同步到草稿', async () => {
      const wrapper = mountEditor({ content: '# 原文', version: 1 })
      const vm = wrapper.vm as any
      vm.editingContent = '# 修改'
      await wrapper.vm.$nextTick()
      vm.changeSummary = '修正错别字'
      await wrapper.vm.$nextTick()

      const draft = loadDraft('nginx-502')
      expect(draft).not.toBeNull()
      expect(draft!.summary).toBe('修正错别字')
    })

    it('保存成功后清除 localStorage 草稿', async () => {
      mockUpdateWikiPage.mockResolvedValue({
        slug: 'nginx-502', title: 'T', version: 2, checksum: 'c',
        created_at: '2026-07-06T00:00:00Z', skipped: false,
      })
      const wrapper = mountEditor({ content: '# 原文', version: 1 })
      const vm = wrapper.vm as any
      vm.editingContent = '# 修改后'
      await wrapper.vm.$nextTick()
      expect(hasDraft('nginx-502')).toBe(true)

      const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
      await saveBtn!.trigger('click')
      await flushPromises()

      expect(hasDraft('nginx-502')).toBe(false)
    })

    it('保存失败后保留草稿（不清除）', async () => {
      mockUpdateWikiPage.mockRejectedValue({
        response: { data: { detail: '版本冲突' } },
      })
      const wrapper = mountEditor({ content: '# 原文', version: 1 })
      const vm = wrapper.vm as any
      vm.editingContent = '# 修改后'
      await wrapper.vm.$nextTick()
      expect(hasDraft('nginx-502')).toBe(true)

      const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存')
      await saveBtn!.trigger('click')
      await flushPromises()

      // 保存失败后草稿仍存在
      expect(hasDraft('nginx-502')).toBe(true)
    })

    it('不同 slug 的草稿互不干扰', () => {
      saveDraft('page-a', '# A 草稿', 1)
      const wrapperA = mountEditor({ content: '# A 原文' })
      // mountEditor 固定 slug='nginx-502'，所以 page-a 的草稿不会显示
      expect(wrapperA.text()).not.toContain('A 草稿')
    })

    it('草稿恢复提示在恢复后消失但草稿保留（用户可继续编辑）', async () => {
      saveDraft('nginx-502', '# 草稿内容', 1)
      const wrapper = mountEditor({ version: 1 })

      const restoreBtn = wrapper.findAll('button').find((b) => b.text() === '恢复草稿')
      await restoreBtn!.trigger('click')

      // 提示消失
      expect(wrapper.text()).not.toContain('恢复草稿')
      // 草稿仍在 localStorage（用户继续编辑会覆盖）
      expect(hasDraft('nginx-502')).toBe(true)
    })

    it('version prop 缺失（undefined）时编辑不持久化草稿', async () => {
      const wrapper = mount(WikiEditor, {
        props: { slug: 'no-version', content: '# 原文', canEdit: true },
        global: { plugins: [pinia] },
      })
      const vm = wrapper.vm as any
      vm.editingContent = '# 修改'
      await wrapper.vm.$nextTick()
      expect(hasDraft('no-version')).toBe(false)
    })
  })
})
