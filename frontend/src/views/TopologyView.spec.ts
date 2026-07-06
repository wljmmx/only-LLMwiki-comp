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

vi.mock('@/api/aiops', () => ({
  getTopology: vi.fn(),
  rebuildTopology: vi.fn(),
  getNodeNeighbors: vi.fn(),
  getImpactAnalysis: vi.fn(),
}))

import {
  getTopology,
  rebuildTopology,
  getNodeNeighbors,
  getImpactAnalysis,
} from '@/api/aiops'
import TopologyView from '@/views/TopologyView.vue'
import '@/test/setup'

const sampleNodes = [
  { name: 'web-prod-01', type: 'Host' },
  { name: 'nginx', type: 'Service' },
  { name: 'order-service', type: 'Service' },
]

const sampleEdges = [
  { source: 'nginx', target: 'web-prod-01', relation: 'RUNS_ON' },
  { source: 'order-service', target: 'nginx', relation: 'DEPENDS_ON' },
]

describe('TopologyView.vue', () => {
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
    return mount(TopologyView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态：onMounted 触发后 loading true、topology 空、过滤项为初始值', () => {
    ;(getTopology as any).mockResolvedValue({ nodes: [], edges: [] })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(true)
    expect(vm.topology).toEqual({ nodes: [], edges: [] })
    expect(vm.nodeTypeFilter).toBe(null)
    expect(vm.relationFilter).toBe(null)
    expect(vm.searchKeyword).toBe('')
    expect(vm.detailVisible).toBe(false)
    expect(vm.impactVisible).toBe(false)
  })

  it('onMounted 调用 loadTopology（无过滤参数）', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: sampleNodes, edges: sampleEdges })
    const wrapper = mountView()
    await flushPromises()
    expect(getTopology).toHaveBeenCalledWith(undefined, undefined)
    const vm = wrapper.vm as any
    expect(vm.topology.nodes).toHaveLength(3)
    expect(vm.topology.edges).toHaveLength(2)
    expect(vm.loading).toBe(false)
  })

  it('loadTopology 失败时 message.error', async () => {
    ;(getTopology as any).mockRejectedValue(new Error('network'))
    const wrapper = mountView()
    await flushPromises()
    expect(mockMessage.error).toHaveBeenCalledWith('network')
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(false)
  })

  it('loadTopology 带 nodeType / relation 过滤参数', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: [], edges: [] })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.nodeTypeFilter = 'Host'
    vm.relationFilter = 'RUNS_ON'
    await vm.loadTopology()
    expect(getTopology).toHaveBeenCalledWith('Host', 'RUNS_ON')
  })

  it('handleRebuild 成功后刷新拓扑', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: [], edges: [] })
    ;(rebuildTopology as any).mockResolvedValue({ nodes: sampleNodes, edges: sampleEdges })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    ;(getTopology as any).mockClear()
    await vm.handleRebuild()
    expect(rebuildTopology).toHaveBeenCalledWith(100)
    expect(mockMessage.success).toHaveBeenCalledWith('重建完成: 3 节点, 2 关系')
    expect(getTopology).toHaveBeenCalled()
    expect(vm.rebuilding).toBe(false)
  })

  it('handleRebuild 失败时 message.error', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: [], edges: [] })
    ;(rebuildTopology as any).mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleRebuild()
    expect(mockMessage.error).toHaveBeenCalledWith('fail')
    expect(vm.rebuilding).toBe(false)
  })

  it('handleRebuild 重复调用直接 return（rebuilding 守卫）', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: [], edges: [] })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.rebuilding = true
    await vm.handleRebuild()
    expect(rebuildTopology).not.toHaveBeenCalled()
  })

  it('openNodeDetail 并行加载 neighbors / impact', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: [], edges: [] })
    ;(getNodeNeighbors as any).mockResolvedValue({
      node: 'nginx',
      neighbors: [
        { name: 'web-prod-01', type: 'Host', direction: 'upstream', relation: 'RUNS_ON' },
      ],
    })
    ;(getImpactAnalysis as any).mockResolvedValue({
      node: 'nginx',
      upstream_affected: ['web-prod-01'],
      downstream_affected: ['order-service'],
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openNodeDetail({ name: 'nginx', type: 'Service' })
    expect(getNodeNeighbors).toHaveBeenCalledWith('nginx', 1)
    expect(getImpactAnalysis).toHaveBeenCalledWith('nginx')
    expect(vm.detailVisible).toBe(true)
    expect(vm.selectedNode.name).toBe('nginx')
    expect(vm.neighbors.neighbors).toHaveLength(1)
    expect(vm.impact.upstream_affected).toHaveLength(1)
    expect(vm.detailLoading).toBe(false)
  })

  it('openNodeDetail 子请求失败时仍关闭 loading（catch null）', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: [], edges: [] })
    ;(getNodeNeighbors as any).mockRejectedValue(new Error('fail'))
    ;(getImpactAnalysis as any).mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openNodeDetail({ name: 'nginx', type: 'Service' })
    expect(vm.neighbors).toBe(null)
    expect(vm.impact).toBe(null)
    expect(vm.detailLoading).toBe(false)
  })

  it('runImpactForNode 成功填充 impactResult', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: [], edges: [] })
    ;(getImpactAnalysis as any).mockResolvedValue({
      node: 'nginx',
      upstream_affected: ['web-prod-01'],
      downstream_affected: [],
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.runImpactForNode('nginx')
    expect(getImpactAnalysis).toHaveBeenCalledWith('nginx')
    expect(vm.impactVisible).toBe(true)
    expect(vm.impactForNode).toBe('nginx')
    expect(vm.impactResult.upstream_affected).toHaveLength(1)
    expect(vm.impactLoading).toBe(false)
  })

  it('runImpactForNode 失败时 message.error', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: [], edges: [] })
    ;(getImpactAnalysis as any).mockRejectedValue(new Error('fail'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.runImpactForNode('nginx')
    expect(mockMessage.error).toHaveBeenCalledWith('fail')
    expect(vm.impactLoading).toBe(false)
  })

  it('filterNodes 按类型与关键字过滤', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: sampleNodes, edges: sampleEdges })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.filterNodes(sampleNodes)).toHaveLength(3)
    vm.nodeTypeFilter = 'Host'
    expect(vm.filterNodes(sampleNodes)).toHaveLength(1)
    expect(vm.filterNodes(sampleNodes)[0].name).toBe('web-prod-01')
    vm.nodeTypeFilter = null
    vm.searchKeyword = 'nginx'
    expect(vm.filterNodes(sampleNodes)).toHaveLength(1)
    expect(vm.filterNodes(sampleNodes)[0].name).toBe('nginx')
  })

  it('layoutNodes 按类型分层布局（Host 顶层 / Service 中层）', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: sampleNodes, edges: sampleEdges })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    const { placed, byType } = vm.layoutNodes
    expect(placed['web-prod-01'].y).toBe(60) // Host layer
    expect(placed['nginx'].y).toBe(240) // Service layer
    expect(placed['order-service'].y).toBe(240) // Service layer
    expect(byType.Host).toHaveLength(1)
    expect(byType.Service).toHaveLength(2)
  })

  it('filteredEdges 按 relation 过滤并剔除未放置节点', async () => {
    ;(getTopology as any).mockResolvedValue({ nodes: sampleNodes, edges: sampleEdges })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.filteredEdges).toHaveLength(2)
    vm.relationFilter = 'RUNS_ON'
    expect(vm.filteredEdges).toHaveLength(1)
    expect(vm.filteredEdges[0].relation).toBe('RUNS_ON')
    vm.relationFilter = 'USES'
    expect(vm.filteredEdges).toHaveLength(0)
  })
})
