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

vi.mock('@/api/versions', () => ({
  listVersions: vi.fn(),
  getVersion: vi.fn(),
  diffVersions: vi.fn(),
  rollbackVersion: vi.fn(),
  listWikiDocs: vi.fn(),
}))

vi.mock('@/api/index', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
  getAuthToken: vi.fn(() => 'fake-token'),
}))

import {
  listVersions,
  getVersion,
  diffVersions,
  rollbackVersion,
  listWikiDocs,
} from '@/api/versions'
import { getAuthToken } from '@/api/index'
import VersionsView from '@/views/VersionsView.vue'
import '@/test/setup'

const sampleVersion = {
  id: 1,
  doc_key: 'wiki:nginx-502',
  version: 1,
  title: 'Nginx 502 排查',
  checksum: 'abc123',
  author: 'alice',
  change_summary: '初版',
  created_at: '2026-07-01T10:00:00Z',
}

const sampleWikiDoc = {
  slug: 'nginx-502',
  title: 'Nginx 502',
  version: 3,
}

describe('VersionsView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    ;(getAuthToken as any).mockReturnValue('fake-token')
    vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView() {
    return mount(VersionsView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态：docKeyInput=wiki:、versions 空、isAuthenticated=true', () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.docKeyInput).toBe('wiki:')
    expect(vm.versions).toEqual([])
    expect(vm.currentDocKey).toBe('')
    expect(vm.isAuthenticated).toBe(true)
    expect(vm.loading).toBe(false)
  })

  it('onMounted 调用 loadWikiDocs', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [sampleWikiDoc], count: 1 })
    const wrapper = mountView()
    await flushPromises()
    expect(listWikiDocs).toHaveBeenCalledWith(100, 0)
    const vm = wrapper.vm as any
    expect(vm.wikiDocs).toHaveLength(1)
    expect(vm.wikiDocsLoading).toBe(false)
  })

  it('loadWikiDocs 失败时静默（仅 console.warn，不弹 message）', async () => {
    ;(listWikiDocs as any).mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.wikiDocs).toEqual([])
    expect(vm.wikiDocsLoading).toBe(false)
    expect(mockMessage.error).not.toHaveBeenCalled()
  })

  it('loadVersions 成功填充 versions 与 currentDocKey', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    ;(listVersions as any).mockResolvedValue({
      doc_key: 'wiki:nginx-502',
      versions: [sampleVersion],
      count: 1,
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.docKeyInput = 'wiki:nginx-502'
    await vm.loadVersions()
    expect(listVersions).toHaveBeenCalledWith('wiki:nginx-502')
    expect(vm.versions).toHaveLength(1)
    expect(vm.currentDocKey).toBe('wiki:nginx-502')
    expect(vm.loading).toBe(false)
  })

  it('loadVersions 空 key 时 warning 且不调用 listVersions', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.docKeyInput = '   '
    await vm.loadVersions()
    expect(listVersions).not.toHaveBeenCalled()
    expect(mockMessage.warning).toHaveBeenCalledWith('请输入 doc_key')
  })

  it('loadVersions 返回空版本时 message.info', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    ;(listVersions as any).mockResolvedValue({ versions: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.docKeyInput = 'wiki:missing'
    await vm.loadVersions()
    expect(mockMessage.info).toHaveBeenCalledWith('未找到 wiki:missing 的版本记录')
  })

  it('loadVersions 失败时 message.error 并清空 versions', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    ;(listVersions as any).mockRejectedValue(new Error('not found'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.docKeyInput = 'wiki:missing'
    await vm.loadVersions()
    expect(mockMessage.error).toHaveBeenCalledWith('not found')
    expect(vm.versions).toEqual([])
    expect(vm.loading).toBe(false)
  })

  it('selectWikiDoc 设置 docKeyInput 并加载版本', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [sampleWikiDoc], count: 1 })
    ;(listVersions as any).mockResolvedValue({
      doc_key: 'wiki:nginx-502',
      versions: [sampleVersion],
      count: 1,
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.selectWikiDoc(sampleWikiDoc)
    expect(vm.docKeyInput).toBe('wiki:nginx-502')
    expect(listVersions).toHaveBeenCalledWith('wiki:nginx-502')
  })

  it('openDetail 加载详情并打开抽屉', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    ;(getVersion as any).mockResolvedValue({ ...sampleVersion, content: '# Hello' })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentDocKey = 'wiki:nginx-502'
    await vm.openDetail(sampleVersion)
    expect(getVersion).toHaveBeenCalledWith('wiki:nginx-502', 1)
    expect(vm.detailVisible).toBe(true)
    expect(vm.selectedVersion.content).toBe('# Hello')
    expect(vm.detailLoading).toBe(false)
  })

  it('openContent 加载内容预览', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    ;(getVersion as any).mockResolvedValue({ ...sampleVersion, content: '# Body' })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentDocKey = 'wiki:nginx-502'
    await vm.openContent(sampleVersion)
    expect(getVersion).toHaveBeenCalledWith('wiki:nginx-502', 1)
    expect(vm.contentVisible).toBe(true)
    expect(vm.contentPreview.content).toBe('# Body')
  })

  it('doDiff 成功生成 diff 与 stats', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    ;(diffVersions as any).mockResolvedValue({
      doc_key: 'wiki:nginx-502',
      v1: 1,
      v2: 2,
      added_lines: 3,
      removed_lines: 1,
      diff: '+ added',
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentDocKey = 'wiki:nginx-502'
    vm.diffV1 = 1
    vm.diffV2 = 2
    await vm.doDiff()
    expect(diffVersions).toHaveBeenCalledWith('wiki:nginx-502', 1, 2)
    expect(vm.diffResult).toBe('+ added')
    expect(vm.diffStats).toEqual({ added: 3, removed: 1 })
    expect(vm.diffLoading).toBe(false)
  })

  it('doDiff 相同版本时 warning', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.diffV1 = 1
    vm.diffV2 = 1
    await vm.doDiff()
    expect(diffVersions).not.toHaveBeenCalled()
    expect(mockMessage.warning).toHaveBeenCalledWith('请选择不同的版本')
  })

  it('doDiff 缺少版本时 warning', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.diffV1 = null
    vm.diffV2 = null
    await vm.doDiff()
    expect(diffVersions).not.toHaveBeenCalled()
    expect(mockMessage.warning).toHaveBeenCalledWith('请选择两个版本')
  })

  it('doDiff 返回 error 字段时填充 diffError', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    ;(diffVersions as any).mockResolvedValue({
      doc_key: 'wiki:nginx-502',
      v1: 1,
      v2: 99,
      added_lines: 0,
      removed_lines: 0,
      diff: '',
      error: '版本不存在',
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentDocKey = 'wiki:nginx-502'
    vm.diffV1 = 1
    vm.diffV2 = 99
    await vm.doDiff()
    expect(vm.diffError).toBe('版本不存在')
    expect(vm.diffResult).toBe('')
  })

  it('handleRollback 未登录时 message.error 且不调用 rollbackVersion', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    ;(getAuthToken as any).mockReturnValue(null)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentDocKey = 'wiki:nginx-502'
    await vm.handleRollback(1)
    expect(rollbackVersion).not.toHaveBeenCalled()
    expect(mockMessage.error).toHaveBeenCalledWith('回滚需要登录 token')
  })

  it('handleRollback 成功后关闭抽屉并刷新列表', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    ;(listVersions as any).mockResolvedValue({ versions: [sampleVersion], count: 1 })
    ;(rollbackVersion as any).mockResolvedValue({
      doc_key: 'wiki:nginx-502',
      version: 4,
      checksum: 'new',
      created_at: '2026-07-02T00:00:00Z',
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentDocKey = 'wiki:nginx-502'
    vm.detailVisible = true
    ;(listVersions as any).mockClear()
    await vm.handleRollback(1)
    expect(rollbackVersion).toHaveBeenCalledWith('wiki:nginx-502', 1)
    expect(mockMessage.success).toHaveBeenCalledWith('已回滚到 v1（创建为新版本 v4）')
    expect(vm.detailVisible).toBe(false)
    expect(listVersions).toHaveBeenCalled()
  })

  it('handleRollback skipped 时 message.warning', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    ;(rollbackVersion as any).mockResolvedValue({
      skipped: true,
      reason: '内容相同',
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.currentDocKey = 'wiki:nginx-502'
    await vm.handleRollback(1)
    expect(mockMessage.warning).toHaveBeenCalledWith('回滚跳过: 内容相同')
  })

  it('versionOptions 计算属性映射版本下拉项', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    ;(listVersions as any).mockResolvedValue({
      versions: [
        { ...sampleVersion, version: 2 },
        { ...sampleVersion, version: 1 },
      ],
      count: 2,
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.docKeyInput = 'wiki:nginx-502'
    await vm.loadVersions()
    const opts = vm.versionOptions
    expect(opts).toHaveLength(2)
    expect(opts[0]).toEqual({ label: 'v2', value: 2 })
    expect(opts[1]).toEqual({ label: 'v1', value: 1 })
  })

  it('formatDate：空字符串返回 -，有效日期含 2026', async () => {
    ;(listWikiDocs as any).mockResolvedValue({ documents: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.formatDate('')).toBe('-')
    const result = vm.formatDate('2026-07-01T10:30:00Z')
    expect(typeof result).toBe('string')
    expect(result).toContain('2026')
  })
})
