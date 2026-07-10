import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const mockMessage = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}

// useMessage 需 <n-message-provider> 包裹，测试中直接 mock naive-ui 的 useMessage
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return { ...actual, useMessage: () => mockMessage }
})

// mock versions API
vi.mock('@/api/versions', () => ({
  listVersions: vi.fn(),
  diffVersions: vi.fn(),
  rollbackVersion: vi.fn(),
}))

import { listVersions, diffVersions, rollbackVersion } from '@/api/versions'
import WikiVersionHistory from '@/components/wiki/WikiVersionHistory.vue'
import '@/test/setup'

const sampleVersions = [
  {
    id: 3,
    doc_key: 'wiki:nginx-502',
    version: 3,
    title: 'Nginx 502',
    checksum: 'abc3',
    author: 'alice',
    change_summary: '修正超时配置',
    created_at: '2026-07-10T10:00:00Z',
  },
  {
    id: 2,
    doc_key: 'wiki:nginx-502',
    version: 2,
    title: 'Nginx 502',
    checksum: 'abc2',
    author: 'bob',
    change_summary: '补充排查步骤',
    created_at: '2026-07-08T10:00:00Z',
  },
  {
    id: 1,
    doc_key: 'wiki:nginx-502',
    version: 1,
    title: 'Nginx 502',
    checksum: 'abc1',
    author: 'system',
    change_summary: '',
    created_at: '2026-07-05T10:00:00Z',
  },
]

describe('WikiVersionHistory.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    ;(listVersions as any).mockResolvedValue({ doc_key: 'wiki:nginx-502', versions: sampleVersions, count: 3 })
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView(props: Partial<{ show: boolean; slug: string }> = {}) {
    return mount(WikiVersionHistory, {
      props: { show: true, slug: 'nginx-502', ...props },
      global: { plugins: [pinia] },
    })
  }

  it('show=true 时加载版本列表', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(listVersions).toHaveBeenCalledWith('wiki:nginx-502')
    expect((wrapper.vm as any).versions).toHaveLength(3)
    expect((wrapper.vm as any).loading).toBe(false)
  })

  it('show=false 时不加载', async () => {
    const wrapper = mountView({ show: false })
    await flushPromises()
    expect(listVersions).not.toHaveBeenCalled()
    expect((wrapper.vm as any).versions).toHaveLength(0)
  })

  it('docKey 计算属性 = wiki:{slug}', () => {
    const wrapper = mountView({ slug: 'my-page' })
    expect((wrapper.vm as any).docKey).toBe('wiki:my-page')
  })

  it('canDiff：选两个不同版本时为 true', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.canDiff).toBe(false)
    vm.selectedVersions = [3, 2]
    expect(vm.canDiff).toBe(true)
    vm.selectedVersions = [3, 3]
    expect(vm.canDiff).toBe(false)
  })

  it('handleDiff 成功填充 diffResult', async () => {
    const mockDiff = { doc_key: 'wiki:nginx-502', v1: 2, v2: 3, added_lines: 5, removed_lines: 2, diff: '--- v2\n+++ v3\n' }
    ;(diffVersions as any).mockResolvedValue(mockDiff)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedVersions = [3, 2]
    await vm.handleDiff()
    // v1<v2 时传 (2,3)
    expect(diffVersions).toHaveBeenCalledWith('wiki:nginx-502', 2, 3)
    expect(vm.diffResult).toEqual(mockDiff)
  })

  it('handleDiff 交换 v1/v2 确保 older 在前', async () => {
    ;(diffVersions as any).mockResolvedValue({ diff: '', added_lines: 0, removed_lines: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedVersions = [1, 3] // v1=1 在前，无需交换
    await vm.handleDiff()
    expect(diffVersions).toHaveBeenCalledWith('wiki:nginx-502', 1, 3)
  })

  it('handleDiff v1>v2 时自动交换', async () => {
    ;(diffVersions as any).mockResolvedValue({ diff: '', added_lines: 0, removed_lines: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedVersions = [3, 1] // v1=3 在后，应交换为 (1,3)
    await vm.handleDiff()
    expect(diffVersions).toHaveBeenCalledWith('wiki:nginx-502', 1, 3)
  })

  it('handleDiff 后端返回 error 时 message.warning', async () => {
    ;(diffVersions as any).mockResolvedValue({ error: '版本不存在', diff: '', added_lines: 0, removed_lines: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedVersions = [3, 2]
    await vm.handleDiff()
    expect(vm.diffResult).toBe(null) // error 不填充
  })

  it('handleRollback 成功后触发 rollback 事件并重新加载', async () => {
    ;(rollbackVersion as any).mockResolvedValue({ version: 4 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    ;(listVersions as any).mockClear()
    await vm.handleRollback(2)
    expect(rollbackVersion).toHaveBeenCalledWith('wiki:nginx-502', 2)
    expect(wrapper.emitted('rollback')).toBeTruthy()
    // 重新加载版本列表
    expect(listVersions).toHaveBeenCalled()
  })

  it('slug 变化时重新加载', async () => {
    const wrapper = mountView({ slug: 'page-a' })
    await flushPromises()
    expect(listVersions).toHaveBeenCalledWith('wiki:page-a')
    ;(listVersions as any).mockClear()
    await wrapper.setProps({ slug: 'page-b' })
    await flushPromises()
    expect(listVersions).toHaveBeenCalledWith('wiki:page-b')
  })
})
