import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { formatDateTime } from '@/utils/format'

const mockMessage = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}
const mockDialog = {
  warning: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
}

// mock naive-ui 的 useMessage/useDialog（避免需要 Provider 包裹）
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useMessage: () => mockMessage,
    useDialog: () => mockDialog,
  }
})

// mock @/api/templates 模块
vi.mock('@/api/templates', () => ({
  listTemplates: vi.fn(),
  getTemplate: vi.fn(),
  createTemplate: vi.fn(),
  updateTemplate: vi.fn(),
  deleteTemplate: vi.fn(),
  renderTemplate: vi.fn(),
}))

// mock @/api/index 模块（提供 getAuthToken）
vi.mock('@/api/index', () => ({
  getAuthToken: () => 'fake-token',
}))

import {
  listTemplates,
  getTemplate,
  createTemplate,
  updateTemplate,
  deleteTemplate,
  renderTemplate,
} from '@/api/templates'
import TemplatesView from '@/views/TemplatesView.vue'
import '@/test/setup'

const builtinTemplate = {
  slug: 'nginx-502-runbook',
  name: 'Nginx 502 Runbook',
  category: 'incident',
  description: '502 故障处置手册',
  content: '# Nginx 502\n步骤...',
  is_builtin: 1,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
}

const customTemplate = {
  slug: 'my-custom-tpl',
  name: '自定义模板',
  category: 'custom',
  description: '示例',
  content: 'Hello {{name}}',
  is_builtin: 0,
  created_at: '2026-07-02T00:00:00Z',
  updated_at: '2026-07-02T00:00:00Z',
}

describe('TemplatesView.vue', () => {
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
    return mount(TemplatesView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态：onMounted 触发后 loading true、templates 空、各弹窗关闭', () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(true)
    expect(vm.templates).toEqual([])
    expect(vm.detailVisible).toBe(false)
    expect(vm.editorVisible).toBe(false)
    expect(vm.renderVisible).toBe(false)
  })

  it('onMounted 调用 listTemplates(undefined) 加载列表', async () => {
    ;(listTemplates as any).mockResolvedValue({
      templates: [builtinTemplate, customTemplate],
      count: 2,
    })
    const wrapper = mountView()
    await flushPromises()
    expect(listTemplates).toHaveBeenCalledTimes(1)
    expect(listTemplates).toHaveBeenCalledWith(undefined)
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(false)
    expect(vm.templates).toHaveLength(2)
  })

  it('loadTemplates 失败时 message.error 且 loading 恢复 false', async () => {
    ;(listTemplates as any).mockRejectedValue({
      response: { data: { detail: '网络错误' } },
    })
    const wrapper = mountView()
    await flushPromises()
    expect(mockMessage.error).toHaveBeenCalledWith('网络错误')
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(false)
    expect(vm.templates).toEqual([])
  })

  it('loadTemplates 传入 categoryFilter 时调用 listTemplates(category)', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.categoryFilter = 'ops'
    ;(listTemplates as any).mockClear()
    await vm.loadTemplates()
    expect(listTemplates).toHaveBeenCalledWith('ops')
  })

  it('isBuiltin 同时支持 boolean 与 number', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.isBuiltin({ ...builtinTemplate, is_builtin: 1 })).toBe(true)
    expect(vm.isBuiltin({ ...builtinTemplate, is_builtin: 0 })).toBe(false)
    expect(vm.isBuiltin({ ...builtinTemplate, is_builtin: true })).toBe(true)
    expect(vm.isBuiltin({ ...builtinTemplate, is_builtin: false })).toBe(false)
    expect(vm.isBuiltin({ ...builtinTemplate, is_builtin: undefined })).toBe(false)
  })

  it('formatDate 空串返回 -，否则返回含年份的本地化字符串', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    mountView()
    await flushPromises()
    expect(formatDateTime('') || '-').toBe('-')
    const result = formatDateTime('2026-07-01T00:00:00Z') || '-'
    expect(typeof result).toBe('string')
    expect(result).toContain('2026')
  })

  it('openDetail 打开抽屉并调用 getTemplate 获取完整内容', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [customTemplate], count: 1 })
    ;(getTemplate as any).mockResolvedValue({ ...customTemplate, content: '完整内容' })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openDetail(customTemplate)
    expect(getTemplate).toHaveBeenCalledWith('my-custom-tpl')
    expect(vm.detailVisible).toBe(true)
    expect(vm.selectedTemplate.content).toBe('完整内容')
    expect(vm.detailLoading).toBe(false)
  })

  it('openDetail getTemplate 失败时保留列表数据，detailLoading 恢复 false', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [customTemplate], count: 1 })
    ;(getTemplate as any).mockRejectedValue(new Error('404'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openDetail(customTemplate)
    expect(vm.detailVisible).toBe(true)
    expect(vm.selectedTemplate).toEqual(customTemplate) // 退回列表数据
    expect(vm.detailLoading).toBe(false)
  })

  it('openEditor 创建模式重置表单与 builtin 标志', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openEditor(null, 'create')
    expect(vm.editorMode).toBe('create')
    expect(vm.editorVisible).toBe(true)
    expect(vm.editorForm.slug).toBe('')
    expect(vm.editorForm.name).toBe('')
    expect(vm.editorForm.category).toBe('custom')
    expect(vm.editorForm.content).toBe('')
    expect(vm.editorOriginalSlug).toBe('')
    expect(vm.editorIsBuiltin).toBe(false)
  })

  it('openEditor 编辑模式填充表单并保留 original slug/builtin 标志', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [builtinTemplate], count: 1 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openEditor(builtinTemplate, 'edit')
    expect(vm.editorMode).toBe('edit')
    expect(vm.editorVisible).toBe(true)
    expect(vm.editorForm.slug).toBe('nginx-502-runbook')
    expect(vm.editorForm.name).toBe('Nginx 502 Runbook')
    expect(vm.editorForm.content).toBe('# Nginx 502\n步骤...')
    expect(vm.editorOriginalSlug).toBe('nginx-502-runbook')
    expect(vm.editorIsBuiltin).toBe(true)
  })

  it('saveTemplate 创建模式必填校验失败时 warning 不调用 createTemplate', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openEditor(null, 'create')
    vm.editorForm.slug = ''
    vm.editorForm.name = 'x'
    vm.editorForm.content = 'y'
    await vm.saveTemplate()
    expect(createTemplate).not.toHaveBeenCalled()
    expect(mockMessage.warning).toHaveBeenCalledWith('slug / 名称 / 内容均为必填项')
  })

  it('saveTemplate 创建模式成功后调用 createTemplate、关闭弹窗并刷新列表', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    ;(createTemplate as any).mockResolvedValue({ slug: 'new-tpl' })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openEditor(null, 'create')
    vm.editorForm.slug = 'new-tpl'
    vm.editorForm.name = '新模板'
    vm.editorForm.content = '内容'
    vm.editorForm.category = 'ops'
    ;(listTemplates as any).mockClear()
    await vm.saveTemplate()
    expect(createTemplate).toHaveBeenCalledWith(
      expect.objectContaining({
        slug: 'new-tpl',
        name: '新模板',
        content: '内容',
        category: 'ops',
      }),
    )
    expect(mockMessage.success).toHaveBeenCalled()
    expect(vm.editorVisible).toBe(false)
    expect(listTemplates).toHaveBeenCalled()
    expect(vm.editorSaving).toBe(false)
  })

  it('saveTemplate 编辑模式内置模板 content 传 undefined', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [builtinTemplate], count: 1 })
    ;(updateTemplate as any).mockResolvedValue({ slug: 'nginx-502-runbook' })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openEditor(builtinTemplate, 'edit')
    vm.editorForm.name = '更新名'
    await vm.saveTemplate()
    expect(updateTemplate).toHaveBeenCalledWith(
      'nginx-502-runbook',
      expect.objectContaining({
        name: '更新名',
        content: undefined,
      }),
    )
    expect(mockMessage.success).toHaveBeenCalled()
    expect(vm.editorVisible).toBe(false)
  })

  it('saveTemplate 编辑模式自定义模板传 content', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [customTemplate], count: 1 })
    ;(updateTemplate as any).mockResolvedValue({ slug: 'my-custom-tpl' })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openEditor(customTemplate, 'edit')
    vm.editorForm.content = '新内容'
    await vm.saveTemplate()
    expect(updateTemplate).toHaveBeenCalledWith(
      'my-custom-tpl',
      expect.objectContaining({ content: '新内容' }),
    )
  })

  it('saveTemplate 失败时 message.error 且弹窗保持打开', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    ;(createTemplate as any).mockRejectedValue({
      response: { data: { detail: 'slug 已存在' } },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openEditor(null, 'create')
    vm.editorForm.slug = 'dup'
    vm.editorForm.name = 'x'
    vm.editorForm.content = 'y'
    await vm.saveTemplate()
    expect(mockMessage.error).toHaveBeenCalledWith('slug 已存在')
    expect(vm.editorVisible).toBe(true)
    expect(vm.editorSaving).toBe(false)
  })

  it('handleDelete 弹出 dialog.warning 确认框', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [customTemplate], count: 1 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.handleDelete(customTemplate)
    expect(mockDialog.warning).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '删除模板',
        positiveText: '删除',
        negativeText: '取消',
      }),
    )
  })

  it('handleDelete 确认回调中调用 deleteTemplate 并刷新列表', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [customTemplate], count: 1 })
    ;(deleteTemplate as any).mockResolvedValue({ deleted: true, slug: 'my-custom-tpl' })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.handleDelete(customTemplate)
    const args = (mockDialog.warning as any).mock.calls[0][0]
    ;(listTemplates as any).mockClear()
    await args.onPositiveClick()
    expect(deleteTemplate).toHaveBeenCalledWith('my-custom-tpl')
    expect(mockMessage.success).toHaveBeenCalled()
    expect(listTemplates).toHaveBeenCalled()
  })

  it('openRender 重置渲染弹窗字段并打开', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [customTemplate], count: 1 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openRender(customTemplate)
    expect(vm.renderVisible).toBe(true)
    expect(vm.renderSlug).toBe('my-custom-tpl')
    expect(vm.renderOutput).toBe('')
    expect(vm.renderError).toBe('')
    expect(vm.renderVarsText).toContain('title')
  })

  it('doRender JSON 解析失败时设置 renderError 且不调用 renderTemplate', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.renderSlug = 'my-custom-tpl'
    vm.renderVarsText = '{invalid json'
    await vm.doRender()
    expect(renderTemplate).not.toHaveBeenCalled()
    expect(vm.renderError).toContain('JSON 解析失败')
    expect(vm.renderLoading).toBe(false)
  })

  it('doRender 成功填充 renderOutput', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    ;(renderTemplate as any).mockResolvedValue({ rendered: 'Hello World', length: 11 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.renderSlug = 'my-custom-tpl'
    vm.renderVarsText = '{"name":"World"}'
    await vm.doRender()
    expect(renderTemplate).toHaveBeenCalledWith('my-custom-tpl', { name: 'World' })
    expect(vm.renderOutput).toBe('Hello World')
    expect(vm.renderLoading).toBe(false)
  })

  it('doRender 失败时填充 renderError', async () => {
    ;(listTemplates as any).mockResolvedValue({ templates: [], count: 0 })
    ;(renderTemplate as any).mockRejectedValue({
      response: { data: { detail: '变量缺失' } },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.renderSlug = 'my-custom-tpl'
    vm.renderVarsText = '{}'
    await vm.doRender()
    expect(vm.renderError).toBe('变量缺失')
    expect(vm.renderLoading).toBe(false)
  })
})
