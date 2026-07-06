import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

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

// S14-3：ExportView 从 @/api/index 取 getAuthToken；export.ts 内部用 apiRaw/getApiBaseUrl
vi.mock('@/api/index', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  getAuthToken: vi.fn(),
  apiRaw: { post: vi.fn(), get: vi.fn() },
  getApiBaseUrl: () => '/api',
}))

// 保留真实 downloadBlob 与 exportFormatOptions，仅覆盖 exportDocument
vi.mock('@/api/export', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/export')>()
  return { ...actual, exportDocument: vi.fn() }
})

vi.mock('@/api/versions', () => ({
  listWikiDocs: vi.fn(),
  listVersions: vi.fn(),
  getVersion: vi.fn(),
}))

vi.mock('@/api/templates', () => ({
  getTemplate: vi.fn(),
  listTemplates: vi.fn(),
}))

import { exportDocument } from '@/api/export'
import { listWikiDocs, listVersions, getVersion } from '@/api/versions'
import { getTemplate, listTemplates } from '@/api/templates'
import { getAuthToken } from '@/api/index'
import ExportView from '@/views/ExportView.vue'
import '@/test/setup'

describe('ExportView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    // 默认已认证（isAuthenticated computed 在 mount 时缓存）
    ;(getAuthToken as any).mockReturnValue('fake-token')
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    // downloadBlob 真实实现依赖 URL.createObjectURL + anchor.click；
    // jsdom 未实现 URL.createObjectURL/revokeObjectURL，手动提供 mock
    ;(URL as any).createObjectURL = vi.fn(() => 'blob:fake-url')
    ;(URL as any).revokeObjectURL = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function setupListMocks() {
    ;(listWikiDocs as any).mockResolvedValue({
      documents: [
        { slug: 'nginx-502', title: 'Nginx 502', version: 3 },
        { slug: 'redis-oom', title: 'Redis OOM', version: 1 },
      ],
      count: 2,
    })
    ;(listTemplates as any).mockResolvedValue({
      templates: [
        { slug: 'incident-tpl', name: '故障模板', category: 'ops' },
      ],
      count: 1,
    })
  }

  function mountView() {
    return mount(ExportView, {
      global: { plugins: [pinia] },
    })
  }

  it('组件能正确 mount，初始状态：title untitled、format markdown、exporting false', async () => {
    setupListMocks()
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.title).toBe('untitled')
    expect(vm.format).toBe('markdown')
    expect(vm.exporting).toBe(false)
    expect(vm.wikiDocs).toEqual([])
    expect(vm.templates).toEqual([])
    await flushPromises()
  })

  it('onMounted 并行调用 loadWikiDocs 与 loadTemplates', async () => {
    setupListMocks()
    mountView()
    await flushPromises()
    expect(listWikiDocs).toHaveBeenCalledWith(100, 0)
    expect(listTemplates).toHaveBeenCalledWith()
  })

  it('loadWikiDocs 成功：填充 wikiDocs（slug/title/version）', async () => {
    setupListMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.wikiDocs).toHaveLength(2)
    expect(vm.wikiDocs[0]).toEqual({
      slug: 'nginx-502',
      title: 'Nginx 502',
      version: 3,
    })
    expect(vm.wikiDocsLoading).toBe(false)
  })

  it('loadWikiDocs 失败：console.warn，wikiDocsLoading 恢复 false', async () => {
    ;(listWikiDocs as any).mockRejectedValue(new Error('network'))
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(console.warn).toHaveBeenCalled()
    expect(vm.wikiDocs).toEqual([])
    expect(vm.wikiDocsLoading).toBe(false)
  })

  it('loadTemplates 成功：填充 templates（slug/name）', async () => {
    setupListMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.templates).toHaveLength(1)
    expect(vm.templates[0]).toEqual({ slug: 'incident-tpl', name: '故障模板' })
    expect(vm.templatesLoading).toBe(false)
  })

  it('isAuthenticated：getAuthToken 返回 null 时为 false，显示未登录提示', async () => {
    ;(getAuthToken as any).mockReturnValue(null)
    setupListMocks()
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.isAuthenticated).toBe(false)
    expect(wrapper.text()).toContain('导出需要登录 Token')
    await flushPromises()
  })

  it('doExport：未认证时 message.error 且不调用 exportDocument', async () => {
    ;(getAuthToken as any).mockReturnValue(null)
    setupListMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.doExport()
    expect(mockMessage.error).toHaveBeenCalledWith('导出需要登录 token')
    expect(exportDocument).not.toHaveBeenCalled()
  })

  it('doExport：title 或 content 为空时 message.warning', async () => {
    setupListMocks()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.title = '   '
    await vm.doExport()
    expect(mockMessage.warning).toHaveBeenCalledWith('标题和内容不能为空')
    expect(exportDocument).not.toHaveBeenCalled()
  })

  it('doExport 成功：调用 exportDocument + downloadBlob + message.success', async () => {
    setupListMocks()
    const fakeBlob = new Blob(['# hello'], { type: 'text/markdown' })
    ;(exportDocument as any).mockResolvedValue({ blob: fakeBlob, filename: 'untitled.md' })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.doExport()
    expect(exportDocument).toHaveBeenCalledWith({
      title: 'untitled',
      content: vm.content,
      format: 'markdown',
    })
    // downloadBlob 为真实实现（非 spy），通过其内部调用的 URL.createObjectURL + click 间接验证已执行
    expect(URL.createObjectURL).toHaveBeenCalledWith(fakeBlob)
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled()
    expect(mockMessage.success).toHaveBeenCalled()
    expect(vm.exporting).toBe(false)
  })

  it('doExport 失败（pdf + wkhtmltopdf）：特定 PDF 错误消息', async () => {
    setupListMocks()
    ;(exportDocument as any).mockRejectedValue({
      response: { data: { detail: 'wkhtmltopdf not installed' } },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.format = 'pdf'
    await vm.doExport()
    expect(mockMessage.error).toHaveBeenCalledWith(
      'PDF 导出失败：服务端未安装 wkhtmltopdf',
    )
    expect(vm.exporting).toBe(false)
  })

  it('doExport 失败（普通错误）：直接显示 detail', async () => {
    setupListMocks()
    ;(exportDocument as any).mockRejectedValue({
      response: { data: { detail: '内容过长' } },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.doExport()
    expect(mockMessage.error).toHaveBeenCalledWith('内容过长')
  })

  it('handleWikiSelect：调用 listVersions + getVersion 加载 wiki 内容', async () => {
    setupListMocks()
    ;(listVersions as any).mockResolvedValue({
      versions: [{ version: 3 }],
      count: 1,
    })
    ;(getVersion as any).mockResolvedValue({
      title: 'Nginx 502 故障',
      content: '# Nginx 502\n内容',
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.handleWikiSelect('nginx-502')
    await flushPromises()
    expect(vm.selectedWikiSlug).toBe('nginx-502')
    expect(listVersions).toHaveBeenCalledWith('wiki:nginx-502')
    expect(getVersion).toHaveBeenCalledWith('wiki:nginx-502', 3)
    expect(vm.title).toBe('Nginx 502 故障')
    expect(vm.content).toBe('# Nginx 502\n内容')
    expect(mockMessage.success).toHaveBeenCalled()
  })

  it('loadWikiContent：版本列表为空时 message.warning', async () => {
    setupListMocks()
    ;(listVersions as any).mockResolvedValue({ versions: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.handleWikiSelect('empty-slug')
    await flushPromises()
    expect(mockMessage.warning).toHaveBeenCalledWith('未找到 wiki:empty-slug 的版本')
    expect(getVersion).not.toHaveBeenCalled()
  })

  it('handleTemplateSelect：调用 getTemplate 加载模板内容', async () => {
    setupListMocks()
    ;(getTemplate as any).mockResolvedValue({
      slug: 'incident-tpl',
      name: '故障模板',
      content: '# 故障模板\n正文',
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleTemplateSelect('incident-tpl')
    expect(vm.selectedTemplateSlug).toBe('incident-tpl')
    expect(getTemplate).toHaveBeenCalledWith('incident-tpl')
    expect(vm.title).toBe('故障模板')
    expect(vm.content).toBe('# 故障模板\n正文')
    expect(mockMessage.success).toHaveBeenCalledWith('已加载模板: 故障模板')
  })

  it('formatDesc computed：根据当前 format 返回对应描述', async () => {
    setupListMocks()
    const wrapper = mountView()
    const vm = wrapper.vm as any
    // markdown 描述
    expect(vm.formatDesc).toBe('保留原始 Markdown 语法')
    vm.format = 'pdf'
    expect(vm.formatDesc).toBe('需服务端安装 wkhtmltopdf')
    await flushPromises()
  })
})
