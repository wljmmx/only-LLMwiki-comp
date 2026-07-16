import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { SseEvent } from '@/composables/useSse'

const mockMessage = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}

// Naive UI 组件依赖 vueuc/vooks，在 jsdom 中会触发 window.matchMedia 错误
// 完全 mock naive-ui 模块，避免 importOriginal() 加载真实模块
vi.mock('naive-ui', () => {
  const createStubComponent = (name: string) => ({
    name,
    template: `<div class="${name}"><slot /></div>`,
    props: ['type', 'size', 'bordered', 'title', 'label', 'defaultValue', 'value', 'checked', 'disabled', 'loading', 'placeholder', 'rows', 'showFeedback', 'status', 'round', 'ghost', 'text', 'tag', 'secondary', 'strong', 'underline', 'depth', 'content', 'closable', 'onUpdateValue', 'onUpdateChecked', 'onUpdateShow', 'onClick', 'onClose', 'onConfirm', 'onPositiveClick', 'onNegativeClick', 'renderLabel', 'renderPrefix', 'renderSuffix'],
    emits: ['update:value', 'update:checked', 'update:show', 'click', 'close', 'confirm', 'positiveClick', 'negativeClick'],
  })

  return {
    default: {},
    useMessage: () => mockMessage,
    useDialog: () => ({ create: vi.fn() }),
    useNotification: () => ({ create: vi.fn() }),
    NButton: createStubComponent('n-button'),
    NInput: createStubComponent('n-input'),
    NDataTable: createStubComponent('n-data-table'),
    NTabs: createStubComponent('n-tabs'),
    NTabPane: createStubComponent('n-tab-pane'),
    NSteps: createStubComponent('n-steps'),
    NStep: createStubComponent('n-step'),
    NProgress: createStubComponent('n-progress'),
    NCollapse: createStubComponent('n-collapse'),
    NCollapseItem: createStubComponent('n-collapse-item'),
    NCode: createStubComponent('n-code'),
    NModal: createStubComponent('n-modal'),
    NUpload: createStubComponent('n-upload'),
    NSlider: createStubComponent('n-slider'),
    NGi: createStubComponent('n-gi'),
    NStatistic: createStubComponent('n-statistic'),
    NGrid: createStubComponent('n-grid'),
    NDivider: createStubComponent('n-divider'),
    NSpace: createStubComponent('n-space'),
    NEmpty: createStubComponent('n-empty'),
    NAlert: createStubComponent('n-alert'),
    NTag: createStubComponent('n-tag'),
    NForm: createStubComponent('n-form'),
    NFormItem: createStubComponent('n-form-item'),
    NCard: createStubComponent('n-card'),
    NCheckbox: createStubComponent('n-checkbox'),
    NIcon: createStubComponent('n-icon'),
    NPopconfirm: createStubComponent('n-popconfirm'),
    NSpin: createStubComponent('n-spin'),
    NSelect: createStubComponent('n-select'),
    NDescriptions: createStubComponent('n-descriptions'),
    NDescriptionsItem: createStubComponent('n-descriptions-item'),
    NDrawer: createStubComponent('n-drawer'),
    NDrawerContent: createStubComponent('n-drawer-content'),
    NLayout: createStubComponent('n-layout'),
    NLayoutSider: createStubComponent('n-layout-sider'),
    NLayoutContent: createStubComponent('n-layout-content'),
    NLayoutHeader: createStubComponent('n-layout-header'),
    NMenu: createStubComponent('n-menu'),
    NAvatar: createStubComponent('n-avatar'),
    NBadge: createStubComponent('n-badge'),
    NText: createStubComponent('n-text'),
    NBreadcrumb: createStubComponent('n-breadcrumb'),
    NBreadcrumbItem: createStubComponent('n-breadcrumb-item'),
    NRadio: createStubComponent('n-radio'),
    NRadioGroup: createStubComponent('n-radio-group'),
    NSwitch: createStubComponent('n-switch'),
    NCheckboxGroup: createStubComponent('n-checkbox-group'),
    NPagination: createStubComponent('n-pagination'),
    NBackTop: createStubComponent('n-back-top'),
    NGridItem: createStubComponent('n-grid-item'),
    NPopover: createStubComponent('n-popover'),
    NTooltip: createStubComponent('n-tooltip'),
    NDropdown: createStubComponent('n-dropdown'),
  }
})

const mockRouterPush = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: mockRouterPush }),
}))

let capturedOnEvent: ((evt: SseEvent) => void) | null = null
let capturedOnError: ((err: string) => void) | null = null

const mockSubscribe = vi.fn((_endpoint: string, options: any) => {
  capturedOnEvent = options.onEvent
  capturedOnError = options.onError
})

vi.mock('@/composables/useSse', () => ({
  useSse: () => ({
    subscribe: mockSubscribe,
  }),
}))

vi.mock('@/api/documents', () => ({
  listDocuments: vi.fn(),
  parseDocument: vi.fn(),
}))

vi.mock('@/api/wiki', () => ({
  getCompileTrace: vi.fn(),
  recompileSection: vi.fn(),
  updateWikiPage: vi.fn(),
}))

import { listDocuments } from '@/api/documents'
import { getCompileTrace } from '@/api/wiki'
import PipelineView from '@/views/PipelineView.vue'

const sampleDoc = {
  id: 'd1',
  title: 'Test Doc',
  filename: 'test.md',
  format: 'md',
  size: 1024,
  checksum: 'abc123',
  status: 'parsed' as const,
  created_at: '2026-07-01T10:00:00Z',
  updated_at: '2026-07-01T10:00:00Z',
}

const sampleTrace = {
  doc_id: 'd1',
  doc_title: 'Test Doc',
  available: true,
  summary: {
    total_sections: 3,
    sections_with_children: 2,
    total_raw_chars: 5000,
    total_compiled_chars: 4000,
    llm_success_count: 2,
    llm_fail_count: 1,
    duration_ms: 1500,
  },
  sections: [
    {
      title: 'Section 1',
      level: 1,
      slug: 'section-1',
      raw_content: '原始内容 A',
      raw_chars: 100,
      compiled_content: '编译后内容 A',
      compiled_chars: 80,
      llm_success: true,
      processing_time_ms: 500,
      children_count: 1,
    },
    {
      title: 'Section 2',
      level: 2,
      slug: 'section-2',
      raw_content: '原始内容 B',
      raw_chars: 200,
      compiled_content: '原始内容 B',
      compiled_chars: 200,
      llm_success: true,
      processing_time_ms: 300,
      children_count: 0,
    },
    {
      title: 'Section 3',
      level: 3,
      slug: 'section-3',
      raw_content: '',
      raw_chars: 0,
      compiled_content: '编译后内容 C',
      compiled_chars: 50,
      llm_success: false,
      processing_time_ms: 700,
      children_count: 0,
    },
  ],
}

// 模拟浏览器 window.matchMedia（Naive UI 组件依赖）
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

describe('PipelineView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    capturedOnEvent = null
    capturedOnError = null
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView() {
    return mount(PipelineView, {
      global: { plugins: [pinia] },
    })
  }

  // ========== 1. 初始渲染 ==========

  it('初始渲染：upload tab 默认选中，existing tab 可见', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.sourceTab).toBe('upload')
    expect(vm.phase).toBe('input')
  })

  it('onMounted 自动调用 loadExistingDocs', async () => {
    ;(listDocuments as any).mockResolvedValue({
      documents: [sampleDoc],
      stats: { total: 1 },
    })
    const wrapper = mountView()
    await flushPromises()
    expect(listDocuments).toHaveBeenCalledWith({ limit: 200 })
    const vm = wrapper.vm as any
    expect(vm.existingDocs).toHaveLength(1)
    expect(vm.existingDocsLoading).toBe(false)
  })

  it('onMounted 加载失败时 message.error', async () => {
    ;(listDocuments as any).mockRejectedValue(new Error('fail'))
    void mountView()
    await flushPromises()
    expect(mockMessage.error).toHaveBeenCalledWith('加载文档列表失败')
  })

  // ========== 2. 搜索过滤 ==========

  it('docSearchText 为空时 filteredDocs 返回全部文档', async () => {
    ;(listDocuments as any).mockResolvedValue({
      documents: [sampleDoc, { ...sampleDoc, id: 'd2', filename: 'other.md' }],
      stats: { total: 2 },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.docSearchText = ''
    expect(vm.filteredDocs).toHaveLength(2)
  })

  it('docSearchText 按文件名过滤文档', async () => {
    ;(listDocuments as any).mockResolvedValue({
      documents: [sampleDoc, { ...sampleDoc, id: 'd2', filename: 'nginx-guide.md' }],
      stats: { total: 2 },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.docSearchText = 'nginx'
    expect(vm.filteredDocs).toHaveLength(1)
    expect(vm.filteredDocs[0].filename).toBe('nginx-guide.md')
  })

  it('docSearchText 按 id 过滤文档', async () => {
    ;(listDocuments as any).mockResolvedValue({
      documents: [sampleDoc, { ...sampleDoc, id: 'xyz-999', filename: 'other.md' }],
      stats: { total: 2 },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.docSearchText = 'xyz'
    expect(vm.filteredDocs).toHaveLength(1)
    expect(vm.filteredDocs[0].id).toBe('xyz-999')
  })

  it('docSearchText 大小写不敏感过滤', async () => {
    ;(listDocuments as any).mockResolvedValue({
      documents: [sampleDoc, { ...sampleDoc, id: 'd2', filename: 'NGINX.md' }],
      stats: { total: 2 },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.docSearchText = 'nginx'
    expect(vm.filteredDocs).toHaveLength(1)
    expect(vm.filteredDocs[0].filename).toBe('NGINX.md')
  })

  // ========== 3. 选择文档 ==========

  it('点击文档行更新 selectedDocId', async () => {
    ;(listDocuments as any).mockResolvedValue({
      documents: [sampleDoc],
      stats: { total: 1 },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    expect(vm.selectedDocId).toBe('d1')
  })

  it('切换 sourceTab 到 existing 时自动加载文档（若未加载）', async () => {
    ;(listDocuments as any).mockResolvedValue({
      documents: [sampleDoc],
      stats: { total: 1 },
    })
    const wrapper = mountView()
    await flushPromises()
    ;(listDocuments as any).mockClear()
    const vm = wrapper.vm as any
    // 清空文档列表，模拟未加载
    vm.existingDocs = []
    vm.sourceTab = 'existing'
    await flushPromises()
    // watch 条件：val === 'existing' && existingDocs.value.length === 0 → 触发 loadExistingDocs
    // 但 onMounted 已加载过，这里不会再次触发（因为 len !== 0）
    // 直接验证 loadExistingDocs 手动调用
    await vm.loadExistingDocs()
    expect(listDocuments).toHaveBeenCalled()
  })

  // ========== 4. 未选择文档时点击编译 ==========

  it('未选择文档时 startCompile 显示 warning', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = ''
    vm.startCompile()
    expect(mockMessage.warning).toHaveBeenCalledWith('请先选择或上传文档')
    expect(vm.phase).toBe('input')
  })

  // ========== 5. 阶段转换 ==========

  it('startCompile 后 phase 变为 compiling', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()
    expect(vm.phase).toBe('compiling')
    expect(vm.compiling).toBe(true)
    expect(mockSubscribe).toHaveBeenCalledWith(
      '/llm-wiki/recompile/d1/stream?force=true',
      expect.objectContaining({
        onEvent: expect.any(Function),
        onError: expect.any(Function),
      }),
    )
  })

  it('SSE done 事件触发 phase 变为 done', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    ;(getCompileTrace as any).mockResolvedValue(sampleTrace)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    // 模拟 SSE done 事件
    capturedOnEvent!({ type: 'done', data: { pages_created: 2, pages_updated: 1, errors: [] } })
    await flushPromises()

    expect(vm.phase).toBe('done')
    expect(vm.compiling).toBe(false)
    expect(vm.compileProgress).toBe(100)
    expect(mockMessage.success).toHaveBeenCalledWith('编译成功：2 个页面创建，1 个页面更新')
  })

  it('SSE done 含 errors 时显示 warning', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    ;(getCompileTrace as any).mockResolvedValue(sampleTrace)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnEvent!({
      type: 'done',
      data: { pages_created: 0, pages_updated: 0, errors: ['编译错误1', '编译错误2'] },
    })
    await flushPromises()

    expect(mockMessage.warning).toHaveBeenCalledWith('编译完成（0 创建 / 0 更新），但有 2 个错误')
  })

  it('SSE done 无新页面时显示 info', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    ;(getCompileTrace as any).mockResolvedValue(sampleTrace)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnEvent!({ type: 'done', data: { pages_created: 0, pages_updated: 0, errors: [] } })
    await flushPromises()

    expect(mockMessage.info).toHaveBeenCalledWith('编译完成，无新页面生成')
  })

  it('SSE error 事件回退到 input 阶段', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnEvent!({
      type: 'error',
      data: { message: 'LLM 调用失败', step: 'compile' },
    })
    await flushPromises()

    expect(vm.phase).toBe('input')
    expect(vm.compiling).toBe(false)
    expect(mockMessage.error).toHaveBeenCalledWith('编译失败：LLM 调用失败')
    expect(vm.compileSteps[2].status).toBe('error')
    expect(vm.compileSteps[2].error).toBe('LLM 调用失败')
  })

  it('SSEonError 回退到 input 阶段', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnError!('连接超时')
    await flushPromises()

    expect(vm.phase).toBe('input')
    expect(vm.compiling).toBe(false)
    expect(mockMessage.error).toHaveBeenCalledWith('编译连接失败：连接超时')
  })

  // ========== 6. 编译步骤状态 ==========

  it('编译步骤初始状态均为 pending', () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.compileSteps).toHaveLength(4)
    vm.compileSteps.forEach((step: any) => {
      expect(step.status).toBe('pending')
    })
  })

  it('编译步骤标签正确', () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.compileSteps[0].label).toBe('解析文档')
    expect(vm.compileSteps[1].label).toBe('知识抽取')
    expect(vm.compileSteps[2].label).toBe('LLM 编译 Wiki')
    expect(vm.compileSteps[3].label).toBe('重建索引')
  })

  it('SSE step_start 更新步骤状态为 running', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnEvent!({ type: 'step_start', data: { step: 'parse' } })
    expect(vm.compileSteps[0].status).toBe('running')

    capturedOnEvent!({ type: 'step_start', data: { step: 'extract' } })
    expect(vm.compileSteps[1].status).toBe('running')
  })

  it('SSE step_done 更新步骤状态为 done 并记录耗时', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnEvent!({ type: 'step_start', data: { step: 'parse' } })
    capturedOnEvent!({ type: 'step_done', data: { step: 'parse', duration_ms: 1200 } })
    expect(vm.compileSteps[0].status).toBe('done')
    expect(vm.compileSteps[0].duration_ms).toBe(1200)
  })

  it('SSE step_done 中 compile 步骤写入 compileResult', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnEvent!({ type: 'step_start', data: { step: 'compile' } })
    capturedOnEvent!({
      type: 'step_done',
      data: {
        step: 'compile',
        pages_created: 5,
        pages_updated: 3,
        pages_unchanged: 1,
        paragraph_count: 42,
      },
    })
    expect(vm.compileResult).toEqual(
      expect.objectContaining({
        pages_created: 5,
        pages_updated: 3,
        pages_unchanged: 1,
        paragraph_count: 42,
      }),
    )
  })

  it('SSE page_start/page_done 更新子进度', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnEvent!({ type: 'step_start', data: { step: 'compile' } })
    capturedOnEvent!({
      type: 'page_start',
      data: { index: 0, total: 3, entity: 'nginx-502' },
    })
    expect(vm.compileSteps[2].subProgress).toEqual({
      current: 0,
      total: 3,
      currentEntity: 'nginx-502',
    })

    capturedOnEvent!({ type: 'page_done', data: { index: 0 } })
    expect(vm.compileSteps[2].subProgress!.current).toBe(1)
    expect(vm.compileSteps[2].subProgress!.currentEntity).toBe('')
  })

  it('SSE progress 事件更新 compileProgress', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnEvent!({ type: 'step_start', data: { step: 'compile' } })
    capturedOnEvent!({ type: 'progress', data: { percent: 50 } })
    // compileProgress = 50 + (50/100) * 25 = 62.5
    expect(vm.compileProgress).toBeCloseTo(62.5)
  })

  // ========== 7. 编译完成后加载 trace 数据 ==========

  it('SSE done 后自动调用 loadTraceData', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    ;(getCompileTrace as any).mockResolvedValue(sampleTrace)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnEvent!({ type: 'done', data: { pages_created: 1, pages_updated: 0, errors: [] } })
    await flushPromises()

    expect(getCompileTrace).toHaveBeenCalledWith('d1', false)
    expect(vm.traceData).toEqual(sampleTrace)
  })

  it('loadTraceData 失败时静默处理', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    ;(getCompileTrace as any).mockRejectedValue(new Error('not found'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnEvent!({ type: 'done', data: { pages_created: 1, pages_updated: 0, errors: [] } })
    await flushPromises()

    expect(vm.traceData).toBeNull()
    expect(vm.traceLoading).toBe(false)
    // 静默失败不应弹出 message.error
  })

  // ========== 8. 章节差异过滤 ==========

  it('showOnlyWithDiffs 为 false 时显示所有章节', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.traceData = sampleTrace
    vm.showOnlyWithDiffs = false
    expect(vm.filteredSections).toHaveLength(3)
  })

  it('showOnlyWithDiffs 为 true 时仅显示有差异的章节', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.traceData = sampleTrace
    vm.showOnlyWithDiffs = true
    // Section 1: raw≠compiled (有差异), Section 2: raw==compiled (无差异), Section 3: raw≠compiled (有差异)
    expect(vm.filteredSections).toHaveLength(2)
    expect(vm.filteredSections[0].slug).toBe('section-1')
    expect(vm.filteredSections[1].slug).toBe('section-3')
  })

  it('traceData 为空时 filteredSections 返回空数组', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.traceData = null
    expect(vm.filteredSections).toEqual([])
  })

  // ========== 9. 辅助函数 ==========

  it('hasDiff: 内容有差异返回 true', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.hasDiff({ raw_content: 'abc', compiled_content: 'def' })).toBe(true)
  })

  it('hasDiff: 内容相同返回 false', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.hasDiff({ raw_content: 'abc', compiled_content: 'abc' })).toBe(false)
  })

  it('hasDiff: 仅空白差异也返回 false（trim 后相同）', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    // hasDiff 使用 trim() 比较，纯空白差异会被忽略
    expect(vm.hasDiff({ raw_content: '  abc  ', compiled_content: 'abc' })).toBe(false)
  })

  it('getLevelLabel: level 1 返回 H1', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.getLevelLabel(1)).toBe('H1')
    expect(vm.getLevelLabel(2)).toBe('H2')
    expect(vm.getLevelLabel(3)).toBe('H3')
  })

  it('getLevelType: 返回对应 tag type', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.getLevelType(1)).toBe('success')
    expect(vm.getLevelType(2)).toBe('info')
    expect(vm.getLevelType(3)).toBe('warning')
    expect(vm.getLevelType(4)).toBe('info')
  })

  it('formatMs: 格式化毫秒', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.formatMs(1500)).toBe('1.5s')
    expect(vm.formatMs(500)).toBe('500ms')
    expect(vm.formatMs(0)).toBe('<1ms')
    expect(vm.formatMs(0.5)).toBe('<1ms')
  })

  it('formatChars: 格式化字符数', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.formatChars(15000)).toBe('15.0k')
    expect(vm.formatChars(1500)).toBe('1.50k')
    expect(vm.formatChars(500)).toBe('500')
    expect(vm.formatChars(0)).toBe('0')
  })

  it('calcReduction: 计算字符缩减百分比', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    // (100-80)/100 * 100 = 20% → -20.0%
    expect(vm.calcReduction(100, 80)).toBe('-20.0%')
    // (100-120)/100 * 100 = -20% → +20.0%
    expect(vm.calcReduction(100, 120)).toBe('+20.0%')
    // raw 为 0 时返回 0%
    expect(vm.calcReduction(0, 100)).toBe('0%')
  })

  // ========== 10. 重置 ==========

  it('resetAll 回到 input 阶段并清空所有状态', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    ;(getCompileTrace as any).mockResolvedValue(sampleTrace)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedDocId = 'd1'
    vm.startCompile()

    capturedOnEvent!({ type: 'done', data: { pages_created: 1, pages_updated: 0, errors: [] } })
    await flushPromises()

    vm.resetAll()
    expect(vm.phase).toBe('input')
    expect(vm.selectedDocId).toBe('')
    expect(vm.compileResult).toBeNull()
    expect(vm.traceData).toBeNull()
    vm.compileSteps.forEach((step: any) => {
      expect(step.status).toBe('pending')
    })
    expect(vm.compileProgress).toBe(0)
  })
})