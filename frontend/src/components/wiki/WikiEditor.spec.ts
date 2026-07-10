import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// mock updateWikiPage + listWikiPages API
const mockUpdateWikiPage = vi.fn()
const mockListWikiPages = vi.fn()
vi.mock('@/api/wiki', () => ({
  updateWikiPage: (...args: any[]) => mockUpdateWikiPage(...args),
  listWikiPages: (...args: any[]) => mockListWikiPages(...args),
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

// 标准 frontmatter 内容（用于测试）
// 使用非数字标签避免 YAML 序列化引号差异（502 → '502'）
const FM_CONTENT = `---
slug: nginx-502
title: Nginx 502 故障排查
type: incident
tags: [nginx, upstream, gateway]
---
# Nginx 502

排查步骤...`

describe('components/wiki/WikiEditor.vue — P1-5 Wiki 编辑器', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    localStorage.clear()
    // 默认返回空列表（wikilink 补全测试会覆盖）
    mockListWikiPages.mockResolvedValue({ pages: [], total: 0 })
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountEditor(overrides: { content?: string; canEdit?: boolean; version?: number } = {}) {
    return mount(WikiEditor, {
      props: {
        slug: 'nginx-502',
        content: overrides.content ?? FM_CONTENT,
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
    expect(wrapper.text()).toContain('Ctrl+S')
  })

  it('渲染 frontmatter 结构化表单（标题/类型/标签）', () => {
    const wrapper = mountEditor()
    expect(wrapper.text()).toContain('Frontmatter')
    expect(wrapper.text()).toContain('标题')
    expect(wrapper.text()).toContain('类型')
    expect(wrapper.text()).toContain('标签')
    expect(wrapper.text()).toContain('Slug（只读）')
  })

  it('显示快捷键提示 Ctrl+S', () => {
    const wrapper = mountEditor()
    expect(wrapper.text()).toContain('Ctrl+S')
  })

  it('显示 wikilink 补全提示', () => {
    const wrapper = mountEditor()
    expect(wrapper.text()).toContain('触发页面链接补全')
  })

  // ────────── frontmatter 解析 ──────────

  it('解析 frontmatter 到结构化字段', () => {
    const wrapper = mountEditor()
    const vm = wrapper.vm as any
    expect(vm.fmSlug).toBe('nginx-502')
    expect(vm.fmTitle).toBe('Nginx 502 故障排查')
    expect(vm.fmType).toBe('incident')
    expect(vm.fmTags).toEqual(['nginx', 'upstream', 'gateway'])
    expect(vm.bodyText).toContain('# Nginx 502')
    expect(vm.bodyText).toContain('排查步骤...')
  })

  it('editingContent 序列化结构化字段 + body', () => {
    const wrapper = mountEditor()
    const vm = wrapper.vm as any
    const content = vm.editingContent
    expect(content).toContain('slug: nginx-502')
    expect(content).toContain('title: Nginx 502 故障排查')
    expect(content).toContain('type: incident')
    expect(content).toContain('[nginx, upstream, gateway]')
    expect(content).toContain('# Nginx 502')
  })

  it('修改 fmTitle → editingContent 反映新标题', async () => {
    const wrapper = mountEditor()
    const vm = wrapper.vm as any
    vm.fmTitle = '新标题'
    await wrapper.vm.$nextTick()
    expect(vm.editingContent).toContain('title: 新标题')
    expect(vm.isDirty).toBe(true)
  })

  it('修改 fmType → editingContent 反映新类型', async () => {
    const wrapper = mountEditor()
    const vm = wrapper.vm as any
    vm.fmType = 'runbook'
    await wrapper.vm.$nextTick()
    expect(vm.editingContent).toContain('type: runbook')
  })

  it('修改 fmTags → editingContent 反映新标签', async () => {
    const wrapper = mountEditor()
    const vm = wrapper.vm as any
    vm.fmTags = ['new-tag']
    await wrapper.vm.$nextTick()
    expect(vm.editingContent).toContain('[new-tag]')
  })

  it('修改 bodyText → editingContent 反映新正文', async () => {
    const wrapper = mountEditor()
    const vm = wrapper.vm as any
    vm.bodyText = '# 新正文'
    await wrapper.vm.$nextTick()
    expect(vm.editingContent).toContain('# 新正文')
    expect(vm.isDirty).toBe(true)
  })

  it('保留未知 frontmatter 字段（created_at 等）', () => {
    const content = `---
slug: test
title: Test
type: concept
tags: []
created_at: 2026-07-05T10:00:00Z
review_status: auto
---
body`
    const wrapper = mountEditor({ content })
    const vm = wrapper.vm as any
    expect(vm.fmRest.created_at).toBe('2026-07-05T10:00:00Z')
    expect(vm.fmRest.review_status).toBe('auto')
    expect(vm.editingContent).toMatch(/created_at:/)
    expect(vm.editingContent).toContain('review_status: auto')
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
    const vm = wrapper.vm as any
    // 修改正文
    vm.bodyText = '# 新正文内容'
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存'))
    expect(saveBtn).toBeDefined()
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockUpdateWikiPage).toHaveBeenCalledTimes(1)
    expect(mockUpdateWikiPage).toHaveBeenCalledWith(
      'nginx-502',
      expect.objectContaining({
        expected_version: 1,
      }),
    )
    // content 应包含新正文
    const callArgs = mockUpdateWikiPage.mock.calls[0][1]
    expect(callArgs.content).toContain('# 新正文内容')
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
    vm.bodyText = '# 修改'
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存'))
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('页面正被 user:bob 编辑')
    expect(wrapper.emitted('saved')).toBeUndefined()
  })

  it('内容无变化时保存按钮禁用', () => {
    const wrapper = mountEditor()
    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存'))
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

  it('content prop 变化 → 重新解析结构化字段', async () => {
    const wrapper = mountEditor({ content: FM_CONTENT })
    const vm = wrapper.vm as any
    vm.fmTitle = '用户修改'
    await wrapper.vm.$nextTick()
    expect(vm.fmTitle).toBe('用户修改')

    // 切换 slug（父组件会传新 content）
    const newContent = `---
slug: other-page
title: Other Page
type: concept
tags: []
---
其他页面`
    await wrapper.setProps({ content: newContent, slug: 'other-page' })
    expect(vm.fmSlug).toBe('other-page')
    expect(vm.fmTitle).toBe('Other Page')
    expect(vm.bodyText).toContain('其他页面')
  })

  // ────────── 变更摘要 ──────────

  it('保存时传 change_summary', async () => {
    mockUpdateWikiPage.mockResolvedValue({
      slug: 'nginx-502', title: 'T', version: 2, checksum: 'c',
      created_at: '2026-07-06T00:00:00Z', skipped: false,
    })

    const wrapper = mountEditor()
    const vm = wrapper.vm as any
    vm.bodyText = '# 新内容'
    vm.changeSummary = '修正错别字'
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存'))
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockUpdateWikiPage).toHaveBeenCalledWith(
      'nginx-502',
      expect.objectContaining({
        change_summary: '修正错别字',
      }),
    )
  })

  it('changeSummary 为空时传 undefined', async () => {
    mockUpdateWikiPage.mockResolvedValue({
      slug: 'nginx-502', title: 'T', version: 2, checksum: 'c',
      created_at: '2026-07-06T00:00:00Z', skipped: false,
    })

    const wrapper = mountEditor()
    const vm = wrapper.vm as any
    vm.bodyText = '# 新'
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存'))
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockUpdateWikiPage).toHaveBeenCalledWith(
      'nginx-502',
      expect.objectContaining({
        change_summary: undefined,
      }),
    )
  })

  // ────────── canEdit 控制保存按钮 ──────────

  it('canEdit=false 时保存按钮禁用', () => {
    const wrapper = mountEditor({ canEdit: false })
    const vm = wrapper.vm as any
    vm.bodyText = '# 修改后'
    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存'))
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
    vm.bodyText = '# 新内容'
    await wrapper.vm.$nextTick()

    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存'))
    await saveBtn!.trigger('click')
    await flushPromises()

    expect(mockUpdateWikiPage).toHaveBeenCalledWith(
      'nginx-502',
      expect.objectContaining({
        expected_version: 5,
      }),
    )
  })

  // ────────── S16-5 草稿持久化与冲突恢复 ──────────

  describe('S16-5 草稿持久化与冲突恢复', () => {
    it('无草稿时不显示草稿恢复提示', () => {
      const wrapper = mountEditor()
      expect(wrapper.text()).not.toContain('恢复草稿')
      expect(wrapper.text()).not.toContain('丢弃草稿')
    })

    it('有草稿（版本一致）→ 显示"恢复未保存草稿"提示 + 恢复/丢弃按钮', () => {
      saveDraft('nginx-502', FM_CONTENT, 1, 'old summary')
      const wrapper = mountEditor({ version: 1 })
      expect(wrapper.text()).toContain('未保存草稿')
      expect(wrapper.text()).toContain('恢复草稿')
      expect(wrapper.text()).toContain('丢弃草稿')
    })

    it('有草稿（版本不一致）→ 显示冲突提示（含版本号对比）', () => {
      saveDraft('nginx-502', FM_CONTENT, 1)
      const wrapper = mountEditor({ version: 3 })
      expect(wrapper.text()).toContain('冲突')
      expect(wrapper.text()).toContain('1')
      expect(wrapper.text()).toContain('3')
    })

    it('点击"恢复草稿" → 结构化字段恢复为草稿内容 + 提示消失', async () => {
      const draftContent = `---
slug: nginx-502
title: 草稿标题 XYZ
type: runbook
tags: [draft]
---
# 草稿内容`
      saveDraft('nginx-502', draftContent, 1, '恢复的摘要')
      const wrapper = mountEditor({ content: FM_CONTENT, version: 1 })
      const vm = wrapper.vm as any

      const restoreBtn = wrapper.findAll('button').find((b) => b.text() === '恢复草稿')
      expect(restoreBtn).toBeDefined()
      await restoreBtn!.trigger('click')

      expect(vm.fmTitle).toBe('草稿标题 XYZ')
      expect(vm.fmType).toBe('runbook')
      expect(vm.fmTags).toEqual(['draft'])
      expect(vm.bodyText).toContain('# 草稿内容')
      expect(vm.changeSummary).toBe('恢复的摘要')
      expect(wrapper.text()).not.toContain('恢复草稿')
    })

    it('点击"丢弃草稿" → 草稿从 localStorage 清除 + 提示消失', async () => {
      saveDraft('nginx-502', FM_CONTENT, 1)
      expect(hasDraft('nginx-502')).toBe(true)
      const wrapper = mountEditor({ version: 1 })

      const discardBtn = wrapper.findAll('button').find((b) => b.text() === '丢弃草稿')
      expect(discardBtn).toBeDefined()
      await discardBtn!.trigger('click')

      expect(hasDraft('nginx-502')).toBe(false)
      expect(wrapper.text()).not.toContain('恢复草稿')
    })

    it('编辑内容变化时持久化草稿到 localStorage（含版本号）', async () => {
      const wrapper = mountEditor({ content: FM_CONTENT, version: 5 })
      const vm = wrapper.vm as any
      vm.bodyText = '# 修改后内容'
      await wrapper.vm.$nextTick()

      expect(hasDraft('nginx-502')).toBe(true)
      const draft = loadDraft('nginx-502')
      expect(draft).not.toBeNull()
      expect(draft!.content).toContain('# 修改后内容')
      expect(draft!.version).toBe(5)
    })

    it('内容未变化时不持久化草稿', async () => {
      const wrapper = mountEditor({ content: FM_CONTENT, version: 1 })
      await wrapper.vm.$nextTick()
      expect(hasDraft('nginx-502')).toBe(false)
    })

    it('changeSummary 变化时同步到草稿', async () => {
      const wrapper = mountEditor({ content: FM_CONTENT, version: 1 })
      const vm = wrapper.vm as any
      vm.bodyText = '# 修改'
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
      const wrapper = mountEditor({ content: FM_CONTENT, version: 1 })
      const vm = wrapper.vm as any
      vm.bodyText = '# 修改后'
      await wrapper.vm.$nextTick()
      expect(hasDraft('nginx-502')).toBe(true)

      const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存'))
      await saveBtn!.trigger('click')
      await flushPromises()

      expect(hasDraft('nginx-502')).toBe(false)
    })

    it('保存失败后保留草稿（不清除）', async () => {
      mockUpdateWikiPage.mockRejectedValue({
        response: { data: { detail: '版本冲突' } },
      })
      const wrapper = mountEditor({ content: FM_CONTENT, version: 1 })
      const vm = wrapper.vm as any
      vm.bodyText = '# 修改后'
      await wrapper.vm.$nextTick()
      expect(hasDraft('nginx-502')).toBe(true)

      const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存'))
      await saveBtn!.trigger('click')
      await flushPromises()

      expect(hasDraft('nginx-502')).toBe(true)
    })

    it('不同 slug 的草稿互不干扰', () => {
      saveDraft('page-a', '# A 草稿', 1)
      const wrapper = mountEditor()
      expect(wrapper.text()).not.toContain('A 草稿')
    })

    it('version prop 缺失（undefined）时编辑不持久化草稿', async () => {
      const wrapper = mount(WikiEditor, {
        props: { slug: 'no-version', content: FM_CONTENT, canEdit: true },
        global: { plugins: [pinia] },
      })
      const vm = wrapper.vm as any
      vm.bodyText = '# 修改'
      await wrapper.vm.$nextTick()
      expect(hasDraft('no-version')).toBe(false)
    })
  })

  // ────────── P1-5: wikilink 补全 ──────────

  describe('P1-5 [[wikilink]] 自动补全', () => {
    it('挂载时加载 wiki 页面列表', async () => {
      mockListWikiPages.mockResolvedValue({
        pages: [
          { slug: 'nginx-502', title: 'Nginx 502', type: 'incident', tags: [] },
          { slug: 'reverse-proxy', title: '反向代理', type: 'concept', tags: [] },
        ],
        total: 2,
      })
      mountEditor()
      await flushPromises()
      expect(mockListWikiPages).toHaveBeenCalled()
    })

    it('wikilinkOptions 按 query 过滤', async () => {
      mockListWikiPages.mockResolvedValue({
        pages: [
          { slug: 'nginx-502', title: 'Nginx 502', type: 'incident', tags: [] },
          { slug: 'reverse-proxy', title: '反向代理', type: 'concept', tags: [] },
          { slug: 'load-balancing', title: '负载均衡', type: 'concept', tags: [] },
        ],
        total: 3,
      })
      const wrapper = mountEditor()
      await flushPromises()
      const vm = wrapper.vm as any

      // 模拟输入 [[nginx
      vm.wikilinkQuery = 'nginx'
      vm.wikilinkActive = true
      expect(vm.wikilinkOptions).toHaveLength(1)
      expect(vm.wikilinkOptions[0].value).toBe('nginx-502')

      // 空查询返回全部（截断到 20）
      vm.wikilinkQuery = ''
      expect(vm.wikilinkOptions).toHaveLength(3)
    })

    it('insertWikilink 替换 [[query 为 [[slug]]', async () => {
      mockListWikiPages.mockResolvedValue({
        pages: [{ slug: 'nginx-502', title: 'Nginx 502', type: 'incident', tags: [] }],
        total: 1,
      })
      const wrapper = mountEditor()
      await flushPromises()
      const vm = wrapper.vm as any

      // 模拟 body 中有 [[ngin
      vm.bodyText = '参见 [[ngin 这里'
      vm.wikilinkStart = 3 // [[ 的位置
      vm.wikilinkQuery = 'ngin'
      vm.wikilinkActive = true

      vm.insertWikilink('nginx-502')
      expect(vm.bodyText).toBe('参见 [[nginx-502]] 这里')
      expect(vm.wikilinkActive).toBe(false)
    })

    it('wikilinkOptions 过滤 index/log 保留文件', async () => {
      mockListWikiPages.mockResolvedValue({
        pages: [
          { slug: 'nginx-502', title: 'Nginx 502', type: 'incident', tags: [] },
          { slug: 'index', title: 'Index', type: 'index', tags: [] },
          { slug: 'log', title: 'Log', type: 'log', tags: [] },
        ],
        total: 3,
      })
      const wrapper = mountEditor()
      await flushPromises()
      const vm = wrapper.vm as any

      vm.wikilinkQuery = ''
      expect(vm.wikilinkOptions).toHaveLength(1) // 只有 nginx-502
    })
  })
})
