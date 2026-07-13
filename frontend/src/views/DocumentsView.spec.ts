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

vi.mock('@/api/documents', () => ({
  listDocuments: vi.fn(),
  searchDocuments: vi.fn(),
  deleteDocument: vi.fn(),
  parseDocument: vi.fn(),
  getDocumentContent: vi.fn(),
  getPipelineStatus: vi.fn(),
  compileToWiki: vi.fn(),
}))

import { listDocuments, searchDocuments, deleteDocument, getDocumentContent, compileToWiki } from '@/api/documents'
import DocumentsView from '@/views/DocumentsView.vue'
import '@/test/setup'

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

describe('DocumentsView.vue', () => {
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
    return mount(DocumentsView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态：onMounted 触发后 loading true、documents 空、分页初始值', () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    // onMounted 已触发 fetchDocuments，loading 为 true（异步加载中）
    expect(vm.loading).toBe(true)
    expect(vm.documents).toEqual([])
    expect(vm.total).toBe(0)
    expect(vm.limit).toBe(10)
    expect(vm.offset).toBe(0)
  })

  it('onMounted 调用 fetchDocuments', async () => {
    ;(listDocuments as any).mockResolvedValue({
      documents: [sampleDoc],
      stats: { total: 1 },
    })
    const wrapper = mountView()
    await flushPromises()
    expect(listDocuments).toHaveBeenCalledTimes(1)
    const vm = wrapper.vm as any
    expect(vm.documents).toHaveLength(1)
    expect(vm.total).toBe(1)
    expect(vm.loading).toBe(false)
  })

  it('fetchDocuments 带 format/status 过滤参数', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.formatFilter = 'md'
    vm.statusFilter = 'parsed'
    await vm.fetchDocuments()
    expect(listDocuments).toHaveBeenCalledWith(
      expect.objectContaining({ format: 'md', status: 'parsed' }),
    )
  })

  it('fetchDocuments 失败时 message.error', async () => {
    ;(listDocuments as any).mockRejectedValue(new Error('network'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.fetchDocuments()
    expect(mockMessage.error).toHaveBeenCalledWith('获取文档列表失败')
    expect(vm.loading).toBe(false)
  })

  it('handleView 打开抽屉并加载内容', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    ;(getDocumentContent as any).mockResolvedValue({ content: '# Hello', format: 'md' })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleView(sampleDoc)
    expect(vm.drawerVisible).toBe(true)
    expect(vm.currentDoc).toEqual(sampleDoc)
    await flushPromises()
    expect(vm.docContent).toBe('# Hello')
  })

  it('handleDelete 成功调用 deleteDocument 并刷新列表', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    ;(deleteDocument as any).mockResolvedValue({ deleted: true })
    // P1-13: 确认逻辑已移至 NPopconfirm UI 层，handleDelete 直接执行删除
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    ;(listDocuments as any).mockClear()
    await vm.handleDelete(sampleDoc)
    expect(deleteDocument).toHaveBeenCalledWith('d1')
    expect(mockMessage.success).toHaveBeenCalled()
    expect(listDocuments).toHaveBeenCalled() // 刷新
  })

  it('handleDelete 成功后清空该行在 checkedRowKeys 中的项', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    ;(deleteDocument as any).mockResolvedValue({ deleted: true })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.checkedRowKeys = ['d1', 'd2']
    await vm.handleDelete(sampleDoc)
    expect(vm.checkedRowKeys).toEqual(['d2'])
  })

  it('handleDelete 失败时 message.error', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    ;(deleteDocument as any).mockRejectedValue(new Error('forbidden'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleDelete(sampleDoc)
    expect(mockMessage.error).toHaveBeenCalledWith('删除失败')
  })

  it('P1-14: handleBatchDelete 批量删除并清空选中', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    ;(deleteDocument as any).mockResolvedValue({ deleted: true })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.checkedRowKeys = ['d1', 'd2', 'd3']
    ;(listDocuments as any).mockClear()
    await vm.handleBatchDelete()
    expect(deleteDocument).toHaveBeenCalledTimes(3)
    expect(deleteDocument).toHaveBeenCalledWith('d1')
    expect(deleteDocument).toHaveBeenCalledWith('d2')
    expect(deleteDocument).toHaveBeenCalledWith('d3')
    expect(mockMessage.success).toHaveBeenCalledWith('成功删除 3 个文档')
    expect(vm.checkedRowKeys).toEqual([])
    expect(listDocuments).toHaveBeenCalled()
  })

  it('P1-14: handleBatchDelete 部分失败时给出 warning', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    ;(deleteDocument as any)
      .mockResolvedValueOnce({ deleted: true })
      .mockRejectedValueOnce(new Error('fail'))
      .mockResolvedValueOnce({ deleted: true })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.checkedRowKeys = ['d1', 'd2', 'd3']
    await vm.handleBatchDelete()
    expect(mockMessage.warning).toHaveBeenCalledWith('批量删除部分失败：成功 2 / 失败 1')
    expect(vm.checkedRowKeys).toEqual([])
  })

  it('P1-14: handleBatchCompile 批量调用 compileToWiki 并清空选中', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    ;(compileToWiki as any).mockResolvedValue({ pages_created: 1 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.checkedRowKeys = ['d1', 'd2']
    ;(listDocuments as any).mockClear()
    await vm.handleBatchCompile()
    expect(compileToWiki).toHaveBeenCalledTimes(2)
    expect(compileToWiki).toHaveBeenCalledWith('d1')
    expect(compileToWiki).toHaveBeenCalledWith('d2')
    expect(mockMessage.success).toHaveBeenCalledWith('成功编译 2 个文档')
    expect(vm.checkedRowKeys).toEqual([])
    expect(listDocuments).toHaveBeenCalled()
  })

  it('P1-14: handleClearSelection 清空选中', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.checkedRowKeys = ['d1', 'd2']
    vm.handleClearSelection()
    expect(vm.checkedRowKeys).toEqual([])
  })

  it('P2-11：handleSearchInput 非空时防抖后调用 searchDocuments 服务端搜索', async () => {
    vi.useFakeTimers()
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    ;(searchDocuments as any).mockResolvedValue({
      query: 'nginx',
      results: [{ ...sampleDoc, filename: 'nginx-502.md' }],
      count: 1,
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    ;(listDocuments as any).mockClear()

    vm.handleSearchInput('nginx')
    // 防抖未到时不发请求
    expect(searchDocuments).not.toHaveBeenCalled()
    vi.advanceTimersByTime(300)
    await flushPromises()

    expect(searchDocuments).toHaveBeenCalledWith('nginx')
    expect(vm.documents).toHaveLength(1)
    expect(vm.total).toBe(1)
    expect(vm.isSearching).toBe(true)
    // 搜索模式不调用 listDocuments
    expect(listDocuments).not.toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('P2-11：handleSearchInput 空值时回到分页列表（fetchDocuments）', async () => {
    vi.useFakeTimers()
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    ;(listDocuments as any).mockClear()

    vm.handleSearchInput('')
    vi.advanceTimersByTime(300)
    await flushPromises()

    expect(searchDocuments).not.toHaveBeenCalled()
    expect(listDocuments).toHaveBeenCalled()
    expect(vm.isSearching).toBe(false)
    vi.useRealTimers()
  })

  it('P2-11：doSearch 失败时 message.error', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    ;(searchDocuments as any).mockRejectedValue(new Error('network'))
    const wrapper = mountView()
    const vm = wrapper.vm as any
    await vm.doSearch('nginx')
    expect(mockMessage.error).toHaveBeenCalledWith('搜索失败')
    expect(vm.loading).toBe(false)
  })
})
