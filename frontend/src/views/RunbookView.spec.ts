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

// mock vue-router（RunbookView 用 useRouter 跳转 wiki）
const mockRouterPush = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mockRouterPush }),
}))

vi.mock('@/api/aiops', () => ({
  generateRunbook: vi.fn(),
}))

vi.mock('@/utils/wikiRender', () => ({
  renderWikiMarkdown: vi.fn((text: string) => `<p>${text}</p>`),
}))

import { generateRunbook } from '@/api/aiops'
import { renderWikiMarkdown } from '@/utils/wikiRender'
import RunbookView from '@/views/RunbookView.vue'
import '@/test/setup'

describe('RunbookView.vue', () => {
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
    return mount(RunbookView, {
      global: { plugins: [pinia] },
    })
  }

  it('组件能正确 mount，初始状态：symptom 空、loading false、result null、errorMsg 空', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.symptom).toBe('')
    expect(vm.service).toBe('')
    expect(vm.host).toBe('')
    expect(vm.maxDocs).toBe(5)
    expect(vm.publish).toBe(false)
    expect(vm.loading).toBe(false)
    expect(vm.result).toBe(null)
    expect(vm.errorMsg).toBe('')
  })

  it('初始渲染：未生成时显示空状态提示文案', () => {
    const wrapper = mountView()
    expect(wrapper.text()).toContain('Runbook 工作台')
    expect(wrapper.text()).toContain('生成 Runbook')
  })

  it('handleGenerate：symptom 为空时直接 return，不调用 generateRunbook', async () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.handleGenerate()
    await flushPromises()
    expect(generateRunbook).not.toHaveBeenCalled()
  })

  it('handleGenerate：loading 中时直接 return（防重复提交）', async () => {
    ;(generateRunbook as any).mockResolvedValue({
      runbook_md: '# rb',
      sources: [],
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.symptom = 'Nginx 502'
    vm.loading = true
    vm.handleGenerate()
    await flushPromises()
    expect(generateRunbook).not.toHaveBeenCalled()
  })

  it('handleGenerate 成功：填充 result、loading 恢复 false、调用 message.success', async () => {
    const fakeResult = {
      runbook_md: '# Nginx 502 Runbook',
      sources: [{ doc_id: 'abc123def456', title: '部署指南', score: 0.85, snippet: '片段' }],
      wiki_published: false,
    }
    ;(generateRunbook as any).mockResolvedValue(fakeResult)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.symptom = 'Nginx 502 Bad Gateway'
    vm.handleGenerate()
    await flushPromises()
    expect(generateRunbook).toHaveBeenCalledWith({
      symptom: 'Nginx 502 Bad Gateway',
      service: '',
      host: '',
      max_docs: 5,
      publish: false,
    })
    expect(vm.result).toEqual(fakeResult)
    expect(vm.loading).toBe(false)
    expect(vm.errorMsg).toBe('')
    expect(mockMessage.success).toHaveBeenCalledWith('Runbook 生成成功')
  })

  it('handleGenerate 成功且 wiki_published + wiki_slug：显示已发布 Wiki 消息', async () => {
    ;(generateRunbook as any).mockResolvedValue({
      runbook_md: '# rb',
      sources: [],
      wiki_published: true,
      wiki_slug: 'nginx-502-troubleshooting',
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.symptom = '502 错误'
    vm.publish = true
    vm.handleGenerate()
    await flushPromises()
    expect(generateRunbook).toHaveBeenCalledWith(
      expect.objectContaining({ publish: true }),
    )
    expect(mockMessage.success).toHaveBeenCalledWith(
      '已发布为 Wiki: nginx-502-troubleshooting',
    )
  })

  it('handleGenerate 失败：填充 errorMsg、调用 message.error、loading 恢复 false', async () => {
    const err = new Error('upstream timeout')
    ;(generateRunbook as any).mockRejectedValue(err)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.symptom = '故障现象'
    vm.handleGenerate()
    await flushPromises()
    expect(vm.errorMsg).toBe('upstream timeout')
    expect(vm.result).toBe(null)
    expect(vm.loading).toBe(false)
    expect(mockMessage.error).toHaveBeenCalledWith('upstream timeout')
  })

  it('handleGenerate 失败：优先取 response.data.detail', async () => {
    ;(generateRunbook as any).mockRejectedValue({
      response: { data: { detail: '服务端内部错误' } },
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.symptom = '故障'
    vm.handleGenerate()
    await flushPromises()
    expect(vm.errorMsg).toBe('服务端内部错误')
    expect(mockMessage.error).toHaveBeenCalledWith('服务端内部错误')
  })

  it('handleGenerate 失败：无 detail/message 时回退到"生成失败"', async () => {
    ;(generateRunbook as any).mockRejectedValue({})
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.symptom = '故障'
    vm.handleGenerate()
    await flushPromises()
    expect(vm.errorMsg).toBe('生成失败')
  })

  it('handleGenerate 调用前置空旧 result 与 errorMsg', async () => {
    ;(generateRunbook as any).mockResolvedValue({ runbook_md: '# rb', sources: [] })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.symptom = '故障'
    vm.errorMsg = '旧错误'
    vm.result = { runbook_md: 'old' } as any
    // handleGenerate 同步阶段立即清空旧 result 与 errorMsg
    vm.handleGenerate()
    expect(vm.errorMsg).toBe('')
    expect(vm.result).toBe(null)
    await flushPromises()
  })

  it('renderedRunbook computed：基于 result.runbook_md 调用 renderWikiMarkdown', async () => {
    ;(generateRunbook as any).mockResolvedValue({
      runbook_md: '# 标题',
      sources: [],
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.renderedRunbook).toBe('')
    vm.symptom = '故障'
    vm.handleGenerate()
    await flushPromises()
    expect(renderWikiMarkdown).toHaveBeenCalledWith('# 标题')
    expect(vm.renderedRunbook).toBe('<p># 标题</p>')
  })

  it('goToWiki：调用 router.push 跳转 /wiki?slug=', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.goToWiki('nginx-502-troubleshooting')
    expect(mockRouterPush).toHaveBeenCalledWith({
      path: '/wiki',
      query: { slug: 'nginx-502-troubleshooting' },
    })
  })
})
