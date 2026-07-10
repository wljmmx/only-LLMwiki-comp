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

vi.mock('@/api/wiki', () => ({
  runWikiLint: vi.fn(),
  getWikiOrphans: vi.fn(),
  getWikiStale: vi.fn(),
  recompileStale: vi.fn(),
  rebuildIndex: vi.fn(),
  ignoreLintIssue: vi.fn(),
}))

import {
  runWikiLint,
  getWikiOrphans,
  getWikiStale,
  recompileStale,
  rebuildIndex,
  ignoreLintIssue,
} from '@/api/wiki'
import WikiHealthView from '@/views/WikiHealthView.vue'
import '@/test/setup'

const sampleLintReport = {
  pages_checked: 5,
  total_issues: 2,
  ignored_count: 0,
  by_type: { contradiction: 1, stale: 1 },
  by_severity: { error: 1, warn: 1 },
  issues: [
    {
      issue_key: 'key-contradiction-1',
      type: 'contradiction',
      severity: 'error',
      slug: 'nginx-502',
      message: '端口冲突',
    },
    {
      issue_key: 'key-stale-2',
      type: 'stale',
      severity: 'warn',
      slug: 'reverse-proxy',
      message: '原始文档已更新',
    },
  ],
}

const sampleWikiPage = {
  slug: 'nginx-502',
  title: 'Nginx 502',
  type: 'incident',
  updated_at: '2026-07-01T10:00:00Z',
}

describe('WikiHealthView.vue', () => {
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
    return mount(WikiHealthView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态：activeTab=lint、lintReport=null、各列表空', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.activeTab).toBe('lint')
    expect(vm.lintReport).toBe(null)
    expect(vm.stalePages).toEqual([])
    expect(vm.orphanPages).toEqual([])
    expect(vm.orphanLoaded).toBe(false)
    expect(vm.lintLoading).toBe(false)
  })

  it('onMounted 不自动加载 lint（仅用户点击触发）', async () => {
    mountView()
    await flushPromises()
    expect(runWikiLint).not.toHaveBeenCalled()
    expect(getWikiStale).not.toHaveBeenCalled()
    expect(getWikiOrphans).not.toHaveBeenCalled()
  })

  it('handleRunLint 成功填充 lintReport', async () => {
    ;(runWikiLint as any).mockResolvedValue(sampleLintReport)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.handleRunLint()
    expect(runWikiLint).toHaveBeenCalledWith(true)
    expect(vm.lintReport).toEqual(sampleLintReport)
    expect(vm.lintLoading).toBe(false)
    expect(mockMessage.success).toHaveBeenCalledWith('检查完成，共发现 2 个问题')
  })

  it('handleRunLint 0 issues 显示不同消息', async () => {
    ;(runWikiLint as any).mockResolvedValue({
      pages_checked: 5,
      total_issues: 0,
      by_type: {},
      by_severity: {},
      issues: [],
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.handleRunLint()
    expect(mockMessage.success).toHaveBeenCalledWith('检查完成，未发现问题')
  })

  it('handleRunLint 失败时 message.error', async () => {
    ;(runWikiLint as any).mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.handleRunLint()
    expect(mockMessage.error).toHaveBeenCalledWith('运行检查失败')
    expect(vm.lintLoading).toBe(false)
  })

  it('切换到 drift tab 触发 fetchStale', async () => {
    ;(getWikiStale as any).mockResolvedValue({ pages: [sampleWikiPage] })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.activeTab = 'drift'
    await flushPromises()
    expect(getWikiStale).toHaveBeenCalledTimes(1)
    expect(vm.stalePages).toHaveLength(1)
    expect(vm.staleLoading).toBe(false)
  })

  it('fetchStale 失败时 message.error', async () => {
    ;(getWikiStale as any).mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.fetchStale()
    expect(mockMessage.error).toHaveBeenCalledWith('获取 stale 列表失败')
    expect(vm.staleLoading).toBe(false)
  })

  it('切换到 orphan tab 触发 fetchOrphans', async () => {
    ;(getWikiOrphans as any).mockResolvedValue({ pages: [sampleWikiPage] })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.activeTab = 'orphan'
    await flushPromises()
    expect(getWikiOrphans).toHaveBeenCalledTimes(1)
    expect(vm.orphanPages).toHaveLength(1)
    expect(vm.orphanLoaded).toBe(true)
  })

  it('fetchOrphans 失败时 message.error 且标记 orphanLoaded', async () => {
    ;(getWikiOrphans as any).mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.fetchOrphans()
    expect(mockMessage.error).toHaveBeenCalledWith('获取孤岛页面失败')
    expect(vm.orphanLoading).toBe(false)
    expect(vm.orphanLoaded).toBe(true)
  })

  it('handleRecompileAll 成功并刷新 stale 列表', async () => {
    ;(recompileStale as any).mockResolvedValue({ recompiled: 2 })
    ;(getWikiStale as any).mockResolvedValue({ pages: [] })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.handleRecompileAll()
    expect(recompileStale).toHaveBeenCalledWith(true)
    expect(mockMessage.success).toHaveBeenCalledWith('全部重编译完成')
    expect(getWikiStale).toHaveBeenCalled()
    expect(vm.recompilingAll).toBe(false)
  })

  it('handleRecompileAll 失败时 message.error', async () => {
    ;(recompileStale as any).mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.handleRecompileAll()
    expect(mockMessage.error).toHaveBeenCalledWith('重编译失败')
    expect(vm.recompilingAll).toBe(false)
  })

  it('handleRebuildIndex 成功', async () => {
    ;(rebuildIndex as any).mockResolvedValue({ ok: true })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.handleRebuildIndex()
    expect(rebuildIndex).toHaveBeenCalledTimes(1)
    expect(mockMessage.success).toHaveBeenCalledWith('索引重建完成')
    expect(vm.rebuildingIndex).toBe(false)
  })

  it('handleRebuildIndex 失败时 message.error', async () => {
    ;(rebuildIndex as any).mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.handleRebuildIndex()
    expect(mockMessage.error).toHaveBeenCalledWith('索引重建失败')
    expect(vm.rebuildingIndex).toBe(false)
  })

  it('lintStatCards 计算属性反映报告数据', async () => {
    ;(runWikiLint as any).mockResolvedValue(sampleLintReport)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.handleRunLint()
    const cards = vm.lintStatCards
    expect(cards[0].value).toBe(5) // pages_checked
    expect(cards[1].value).toBe(2) // total_issues
    expect(cards[2].value).toBe(0) // ignored_count
    expect(cards[3].value).toBe(1) // error
  })

  it('formatDate：YYYY-MM-DD HH:mm 格式', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    const result = vm.formatDate('2026-07-01T10:30:00Z')
    expect(typeof result).toBe('string')
    expect(result).toContain('2026')
  })

  // ────────── P1-12b: Lint 忽略 ──────────

  it('handleIgnoreIssue 成功后本地移除该 issue 并调整计数', async () => {
    ;(runWikiLint as any).mockResolvedValue(sampleLintReport)
    ;(ignoreLintIssue as any).mockResolvedValue({
      issue_key: 'key-contradiction-1',
      ignored: true,
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.handleRunLint()
    expect(vm.lintReport.issues).toHaveLength(2)

    await vm.handleIgnoreIssue(sampleLintReport.issues[0])
    await flushPromises()

    expect(ignoreLintIssue).toHaveBeenCalledWith('key-contradiction-1', {
      type: 'contradiction',
      slug: 'nginx-502',
      message: '端口冲突',
    })
    expect(vm.lintReport.issues).toHaveLength(1)
    expect(vm.lintReport.issues[0].issue_key).toBe('key-stale-2')
    expect(vm.lintReport.total_issues).toBe(1)
    expect(vm.lintReport.ignored_count).toBe(1)
    expect(vm.lintReport.by_severity.error).toBe(0)
    expect(vm.lintReport.by_type.contradiction).toBe(0)
    expect(mockMessage.success).toHaveBeenCalledWith('已忽略该问题')
  })

  it('handleIgnoreIssue 失败时 message.error 且不移除 issue', async () => {
    ;(runWikiLint as any).mockResolvedValue(sampleLintReport)
    ;(ignoreLintIssue as any).mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.handleRunLint()
    await vm.handleIgnoreIssue(sampleLintReport.issues[0])
    await flushPromises()

    expect(mockMessage.error).toHaveBeenCalledWith('忽略失败')
    expect(vm.lintReport.issues).toHaveLength(2)
    expect(vm.lintReport.total_issues).toBe(2)
    expect(vm.lintReport.ignored_count).toBe(0)
  })
})
