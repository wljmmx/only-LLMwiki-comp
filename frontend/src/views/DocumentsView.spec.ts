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
  deleteDocument: vi.fn(),
  parseDocument: vi.fn(),
  getDocumentContent: vi.fn(),
}))

import { listDocuments, deleteDocument, getDocumentContent } from '@/api/documents'
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
    // handleDelete 用 window.confirm，mock 为 true
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    ;(listDocuments as any).mockClear()
    await vm.handleDelete(sampleDoc)
    expect(deleteDocument).toHaveBeenCalledWith('d1')
    expect(mockMessage.success).toHaveBeenCalled()
    expect(listDocuments).toHaveBeenCalled() // 刷新
  })

  it('handleDelete 用户取消确认时不调用 deleteDocument', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    ;(deleteDocument as any).mockResolvedValue({ deleted: true })
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleDelete(sampleDoc)
    expect(deleteDocument).not.toHaveBeenCalled()
  })

  it('handleDelete 失败时 message.error', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [sampleDoc], stats: { total: 1 } })
    ;(deleteDocument as any).mockRejectedValue(new Error('forbidden'))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleDelete(sampleDoc)
    expect(mockMessage.error).toHaveBeenCalledWith('删除失败')
  })

  it('filteredDocuments 按 searchText 过滤文件名', async () => {
    ;(listDocuments as any).mockResolvedValue({
      documents: [
        { ...sampleDoc, doc_id: 'd1', filename: 'nginx-502.md' },
        { ...sampleDoc, doc_id: 'd2', filename: 'apache.md' },
      ],
      stats: { total: 2 },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.filteredDocuments).toHaveLength(2)
    vm.searchText = 'nginx'
    expect(vm.filteredDocuments).toHaveLength(1)
    expect(vm.filteredDocuments[0].filename).toBe('nginx-502.md')
  })

  it('formatFileSize：0/KB/MB/GB 转换', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.formatFileSize(0)).toBe('0 B')
    expect(vm.formatFileSize(1024)).toBe('1 KB')
    expect(vm.formatFileSize(1048576)).toBe('1 MB')
    expect(vm.formatFileSize(1073741824)).toBe('1 GB')
  })

  it('formatDate：YYYY-MM-DD HH:mm 格式', async () => {
    ;(listDocuments as any).mockResolvedValue({ documents: [], stats: { total: 0 } })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    const result = vm.formatDate('2026-07-01T10:30:00Z')
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
  })
})
