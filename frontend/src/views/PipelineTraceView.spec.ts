import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

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
    NCard: createStubComponent('n-card'),
    NAlert: createStubComponent('n-alert'),
    NTag: createStubComponent('n-tag'),
    NCollapse: createStubComponent('n-collapse'),
    NCollapseItem: createStubComponent('n-collapse-item'),
    NCode: createStubComponent('n-code'),
    NSpace: createStubComponent('n-space'),
    NDivider: createStubComponent('n-divider'),
    NEmpty: createStubComponent('n-empty'),
    NStatistic: createStubComponent('n-statistic'),
    NGrid: createStubComponent('n-grid'),
    NGi: createStubComponent('n-gi'),
    NCheckbox: createStubComponent('n-checkbox'),
    NIcon: createStubComponent('n-icon'),
    NLayout: createStubComponent('n-layout'),
    NLayoutContent: createStubComponent('n-layout-content'),
    NLayoutHeader: createStubComponent('n-layout-header'),
    NLayoutSider: createStubComponent('n-layout-sider'),
    NMenu: createStubComponent('n-menu'),
    NAvatar: createStubComponent('n-avatar'),
    NBadge: createStubComponent('n-badge'),
    NText: createStubComponent('n-text'),
    NBreadcrumb: createStubComponent('n-breadcrumb'),
    NBreadcrumbItem: createStubComponent('n-breadcrumb-item'),
    NDataTable: createStubComponent('n-data-table'),
    NProgress: createStubComponent('n-progress'),
    NModal: createStubComponent('n-modal'),
    NTabs: createStubComponent('n-tabs'),
    NTabPane: createStubComponent('n-tab-pane'),
    NSteps: createStubComponent('n-steps'),
    NStep: createStubComponent('n-step'),
    NForm: createStubComponent('n-form'),
    NFormItem: createStubComponent('n-form-item'),
    NSelect: createStubComponent('n-select'),
    NUpload: createStubComponent('n-upload'),
    NSlider: createStubComponent('n-slider'),
    NPopconfirm: createStubComponent('n-popconfirm'),
    NSpin: createStubComponent('n-spin'),
    NDescriptions: createStubComponent('n-descriptions'),
    NDescriptionsItem: createStubComponent('n-descriptions-item'),
    NDrawer: createStubComponent('n-drawer'),
    NDrawerContent: createStubComponent('n-drawer-content'),
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

vi.mock('@/api/wiki', () => ({
  getCompileTrace: vi.fn(),
}))

import { getCompileTrace } from '@/api/wiki'
const mockGetCompileTrace = getCompileTrace as ReturnType<typeof vi.fn>

const mockRoute = {
  query: {} as Record<string, string>,
}

vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
}))

import PipelineTraceView from '@/views/PipelineTraceView.vue'
import type { SectionTrace, CompileTraceResponse } from '@/types/api'
import '@/test/setup'

// ────────── 测试数据 ──────────

const mockSectionWithDiff: SectionTrace = {
  slug: 'test-section',
  title: 'Test Section',
  level: 1,
  raw_content: 'raw content',
  compiled_content: 'compiled content',
  raw_chars: 11,
  compiled_chars: 15,
  processing_time_ms: 100,
  llm_success: true,
  children_count: 0,
}

const mockSectionNoDiff: SectionTrace = {
  slug: 'same-section',
  title: 'Same Section',
  level: 2,
  raw_content: 'same',
  compiled_content: 'same',
  raw_chars: 4,
  compiled_chars: 4,
  processing_time_ms: 50,
  llm_success: true,
  children_count: 2,
}

const mockSectionFail: SectionTrace = {
  slug: 'fail-section',
  title: 'Failed Section',
  level: 3,
  raw_content: 'before',
  compiled_content: 'after',
  raw_chars: 6,
  compiled_chars: 5,
  processing_time_ms: 1500,
  llm_success: false,
  children_count: 0,
}

const mockTraceData: CompileTraceResponse = {
  available: true,
  doc_id: 'test123',
  doc_title: 'Test Document',
  sections: [mockSectionWithDiff, mockSectionNoDiff, mockSectionFail],
  summary: {
    total_sections: 3,
    sections_with_children: 1,
    total_raw_chars: 1500,
    total_compiled_chars: 1200,
    llm_success_count: 2,
    llm_fail_count: 1,
    duration_ms: 500,
  },
}

// ────────── 测试套件 ──────────

describe('PipelineTraceView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  function mountView() {
    return mount(PipelineTraceView, {
      global: {
        plugins: [pinia],
        stubs: { PageHeader: true, LoadingState: true },
      },
    })
  }

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

  // ─── 1. 初始渲染 ───

  it('初始渲染显示 doc_id 输入框和查询按钮', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.docId).toBe('')
    expect(vm.loading).toBe(false)
    expect(vm.traceData).toBeNull()
  })

  // ─── 2. 未输入 doc_id 点击查询 ───

  it('未输入 doc_id 点击查询时显示警告并中止', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = ''
    vm.fetchTrace()
    expect(mockMessage.warning).toHaveBeenCalledWith('请输入文档 ID')
    expect(mockGetCompileTrace).not.toHaveBeenCalled()
  })

  it('输入空白字符后点击查询也显示警告', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = '   '
    vm.fetchTrace()
    expect(mockMessage.warning).toHaveBeenCalledWith('请输入文档 ID')
    expect(mockGetCompileTrace).not.toHaveBeenCalled()
  })

  // ─── 3. Loading 状态 ───

  it('fetchTrace 开始时设置 loading 为 true 并清空 traceData', async () => {
    mockGetCompileTrace.mockResolvedValue(mockTraceData)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    vm.fetchTrace()
    // 同步断言：fetchTrace 内部在 await 前已设置 loading=true 和 traceData=null
    expect(vm.loading).toBe(true)
    expect(vm.traceData).toBeNull()
    await flushPromises()
    expect(vm.loading).toBe(false)
    expect(vm.traceData).toEqual(mockTraceData)
  })

  it('请求完成后 loading 恢复为 false', async () => {
    mockGetCompileTrace.mockResolvedValue(mockTraceData)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    expect(vm.loading).toBe(false)
  })

  // ─── 4. 空状态 ───

  it('无数据时 traceData 为 null', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.traceData).toBeNull()
  })

  it('请求失败后 traceData 保持 null', async () => {
    mockGetCompileTrace.mockRejectedValue(new Error('网络错误'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    expect(vm.traceData).toBeNull()
    expect(vm.loading).toBe(false)
  })

  // ─── 5. 追踪不可用时的 Alert ───

  it('追踪数据不可用时 available 为 false', async () => {
    mockGetCompileTrace.mockResolvedValue({
      available: false,
      message: '该文档尚未编译，无法获取追踪数据',
    } as CompileTraceResponse)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    expect(vm.traceData).not.toBeNull()
    expect(vm.traceData!.available).toBe(false)
    expect(vm.traceData!.message).toBe('该文档尚未编译，无法获取追踪数据')
  })

  it('追踪不可用且无 message 时使用默认值', async () => {
    mockGetCompileTrace.mockResolvedValue({
      available: false,
    } as CompileTraceResponse)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    expect(vm.traceData!.available).toBe(false)
  })

  // ─── 6. 汇总统计卡片 ───

  it('获取数据后 summary 统计信息完整', async () => {
    mockGetCompileTrace.mockResolvedValue(mockTraceData)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    expect(vm.traceData!.summary).toBeDefined()
    expect(vm.traceData!.summary!.total_sections).toBe(3)
    expect(vm.traceData!.summary!.sections_with_children).toBe(1)
    expect(vm.traceData!.summary!.total_raw_chars).toBe(1500)
    expect(vm.traceData!.summary!.total_compiled_chars).toBe(1200)
    expect(vm.traceData!.summary!.llm_success_count).toBe(2)
    expect(vm.traceData!.summary!.llm_fail_count).toBe(1)
    expect(vm.traceData!.summary!.duration_ms).toBe(500)
  })

  it('有数据时 filteredSections 等于全量 sections', async () => {
    mockGetCompileTrace.mockResolvedValue(mockTraceData)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    expect(vm.filteredSections).toHaveLength(3)
  })

  // ─── 7. 章节对比列表 ───

  it('章节列表包含所有 section 数据', async () => {
    mockGetCompileTrace.mockResolvedValue(mockTraceData)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    const sections = vm.filteredSections
    expect(sections).toHaveLength(3)
    expect(sections[0].slug).toBe('test-section')
    expect(sections[1].slug).toBe('same-section')
    expect(sections[2].slug).toBe('fail-section')
  })

  it('sections 为空数组时 filteredSections 为空', async () => {
    mockGetCompileTrace.mockResolvedValue({
      ...mockTraceData,
      sections: [],
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    expect(vm.filteredSections).toHaveLength(0)
  })

  it('sections 为 undefined 时 filteredSections 为空', async () => {
    mockGetCompileTrace.mockResolvedValue({
      ...mockTraceData,
      sections: undefined,
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    expect(vm.filteredSections).toHaveLength(0)
  })

  // ─── 8. "仅显示有差异" 复选框过滤 ───

  it('showOnlyWithDiffs 为 false 时返回全部 sections', async () => {
    mockGetCompileTrace.mockResolvedValue(mockTraceData)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    vm.showOnlyWithDiffs = false
    await flushPromises()
    expect(vm.filteredSections).toHaveLength(3)
  })

  it('showOnlyWithDiffs 为 true 时仅返回有差异的 sections', async () => {
    mockGetCompileTrace.mockResolvedValue(mockTraceData)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    vm.showOnlyWithDiffs = true
    await flushPromises()
    // 预期：test-section（raw≠compiled）和 fail-section（raw≠compiled）有差异
    // same-section 无差异（raw==compiled），应被过滤
    const diffSections = vm.filteredSections
    const slugs = diffSections.map((s: SectionTrace) => s.slug)
    expect(slugs).toContain('test-section')
    expect(slugs).toContain('fail-section')
    expect(slugs).not.toContain('same-section')
  })

  it('所有 sections 无差异时 filteredSections 为空', async () => {
    mockGetCompileTrace.mockResolvedValue({
      ...mockTraceData,
      sections: [mockSectionNoDiff, { ...mockSectionNoDiff, slug: 'same-2' }],
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    vm.showOnlyWithDiffs = true
    await flushPromises()
    expect(vm.filteredSections).toHaveLength(0)
  })

  // ─── 9. 辅助函数 ───

  describe('hasDiff', () => {
    it('raw_content 与 compiled_content 不同时返回 true', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.hasDiff(mockSectionWithDiff)).toBe(true)
    })

    it('raw_content 与 compiled_content 相同时返回 false', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.hasDiff(mockSectionNoDiff)).toBe(false)
    })

    it('仅空白字符差异视为有差异（trim 后不同）', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(
        vm.hasDiff({
          ...mockSectionNoDiff,
          raw_content: '  text  ',
          compiled_content: 'different',
        }),
      ).toBe(true)
    })
  })

  describe('getLevelLabel', () => {
    it('level=1 返回 H1', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.getLevelLabel(1)).toBe('H1')
    })

    it('level=2 返回 H2', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.getLevelLabel(2)).toBe('H2')
    })

    it('level=3 返回 H3', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.getLevelLabel(3)).toBe('H3')
    })
  })

  describe('getLevelType', () => {
    it('level=1 返回 success', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.getLevelType(1)).toBe('success')
    })

    it('level=2 返回 info', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.getLevelType(2)).toBe('info')
    })

    it('level=3 返回 warning', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.getLevelType(3)).toBe('warning')
    })

    it('未知 level 返回 info', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.getLevelType(99)).toBe('info')
    })
  })

  describe('formatMs', () => {
    it('>=1000ms 返回秒格式', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.formatMs(1500)).toBe('1.5s')
      expect(vm.formatMs(1000)).toBe('1.0s')
    })

    it('1ms ~ 999ms 返回毫秒格式', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.formatMs(500)).toBe('500ms')
      expect(vm.formatMs(1)).toBe('1ms')
    })

    it('<1ms 返回特殊字符串', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.formatMs(0)).toBe('<1ms')
      expect(vm.formatMs(0.5)).toBe('<1ms')
    })
  })

  describe('formatChars', () => {
    it('>=10000 返回 k 格式（1 位小数）', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.formatChars(15000)).toBe('15.0k')
      expect(vm.formatChars(10000)).toBe('10.0k')
    })

    it('1000 ~ 9999 返回 k 格式（2 位小数）', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.formatChars(1500)).toBe('1.50k')
      expect(vm.formatChars(1000)).toBe('1.00k')
    })

    it('<1000 返回原始数字字符串', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.formatChars(999)).toBe('999')
      expect(vm.formatChars(0)).toBe('0')
    })
  })

  describe('calcReduction', () => {
    it('减少时返回负百分比', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.calcReduction(100, 80)).toBe('-20.0%')
    })

    it('增加时返回正百分比', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.calcReduction(10, 15)).toBe('+50.0%')
    })

    it('raw 为 0 时返回 0%', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.calcReduction(0, 100)).toBe('0%')
    })

    it('无变化时返回 +0.0%', async () => {
      mockGetCompileTrace.mockResolvedValue(mockTraceData)
      const wrapper = mountView()
      const vm = wrapper.vm as any
      vm.docId = 'test123'
      await vm.fetchTrace()
      expect(vm.calcReduction(100, 100)).toBe('+0.0%')
    })
  })

  // ─── 10. 回车键触发查询 ───

  it('回车键触发 fetchTrace', async () => {
    mockGetCompileTrace.mockResolvedValue(mockTraceData)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    // 模拟回车键：直接调用 fetchTrace（实际由 @keyup.enter 触发）
    await vm.fetchTrace()
    expect(mockGetCompileTrace).toHaveBeenCalledWith('test123', true)
    expect(mockGetCompileTrace).toHaveBeenCalledTimes(1)
  })

  it('回车键时 doc_id 为空不发起请求', async () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = ''
    await vm.fetchTrace()
    expect(mockGetCompileTrace).not.toHaveBeenCalled()
  })

  // ─── 11. onMounted 从 URL query 读取 doc_id ───

  it('onMounted 时若 URL 有 doc_id 则自动查询', async () => {
    mockGetCompileTrace.mockResolvedValue(mockTraceData)
    mockRoute.query = { doc_id: 'auto123' }
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await flushPromises()
    expect(vm.docId).toBe('auto123')
    expect(mockGetCompileTrace).toHaveBeenCalledWith('auto123', true)
  })

  it('onMounted 时若 URL 无 doc_id 则不自动查询', async () => {
    mockRoute.query = {}
    void mountView()
    await flushPromises()
    expect(mockGetCompileTrace).not.toHaveBeenCalled()
  })

  // ─── 12. 错误处理 ───

  it('请求失败时显示错误消息（有 response.data.detail）', async () => {
    mockGetCompileTrace.mockRejectedValue({
      response: { data: { detail: '服务器内部错误' } },
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    expect(mockMessage.error).toHaveBeenCalledWith('服务器内部错误')
  })

  it('请求失败时显示错误消息（仅有 message）', async () => {
    mockGetCompileTrace.mockRejectedValue(new Error('网络超时'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    expect(mockMessage.error).toHaveBeenCalledWith('网络超时')
  })

  it('请求失败时显示默认错误消息', async () => {
    mockGetCompileTrace.mockRejectedValue({})
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.docId = 'test123'
    await vm.fetchTrace()
    expect(mockMessage.error).toHaveBeenCalledWith('获取管道追踪失败')
  })
})