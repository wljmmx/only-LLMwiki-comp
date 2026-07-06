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

// mock @vue-flow 模块（避免实际渲染图、CSS 加载与 d3 依赖）
const mockFitView = vi.fn()
const mockOnNodeClick = vi.fn()
vi.mock('@vue-flow/core', () => ({
  VueFlow: {
    name: 'VueFlow',
    template: '<div class="vf-mock"><slot /></div>',
  },
  useVueFlow: () => ({
    onNodeClick: mockOnNodeClick,
    fitView: mockFitView,
  }),
}))
vi.mock('@vue-flow/background', () => ({
  Background: { name: 'Background', template: '<div class="bg-mock" />' },
}))
vi.mock('@vue-flow/controls', () => ({
  Controls: { name: 'Controls', template: '<div class="controls-mock" />' },
}))
vi.mock('@vue-flow/minimap', () => ({
  MiniMap: { name: 'MiniMap', template: '<div class="minimap-mock" />' },
}))

// mock @/api/graph 模块
vi.mock('@/api/graph', () => ({
  getGraphVisualize: vi.fn(),
  getGraphStats: vi.fn(),
  searchGraph: vi.fn(),
  getGraphEntity: vi.fn(),
}))

import {
  getGraphVisualize,
  getGraphStats,
  searchGraph,
  getGraphEntity,
} from '@/api/graph'
import GraphView from '@/views/GraphView.vue'
import '@/test/setup'

const sampleGraphData = {
  nodes: [
    { id: 'host-1', type: 'Host', group: 1 },
    { id: 'svc-1', type: 'Service', group: 2 },
  ],
  links: [{ source: 'host-1', target: 'svc-1', type: 'RUNS_ON' }],
  node_count: 2,
  link_count: 1,
}

const sampleStats = {
  total_entities: 50,
  total_relations: 80,
  by_type: [{ type: 'Host', count: 10 }],
}

const sampleEntityDetail = {
  entity: {
    name: 'host-1',
    entity_type: 'Host',
    confidence: 0.92,
    source_doc_id: 'doc-1',
    properties: { ip: '10.0.0.1' },
  },
  related: [
    {
      source: 'host-1',
      relation: 'RUNS_ON',
      target: 'svc-1',
      target_type: 'Service',
      confidence: 0.8,
    },
  ],
}

describe('GraphView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView() {
    return mount(GraphView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态：onMounted 触发后 loading true、graphData 空、stats null', () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(null)
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(true)
    expect(vm.graphData.nodes).toEqual([])
    expect(vm.graphData.links).toEqual([])
    expect(vm.stats).toBe(null)
    expect(vm.neo4jError).toBe('')
  })

  it('onMounted 调用 loadStats 与 loadGraph', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(sampleStats)
    const wrapper = mountView()
    await flushPromises()
    expect(getGraphStats).toHaveBeenCalledTimes(1)
    expect(getGraphVisualize).toHaveBeenCalledWith(undefined, 200)
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(false)
    expect(vm.stats).not.toBeNull()
    expect(vm.stats.total_entities).toBe(50)
  })

  it('loadGraph 成功后填充 graphData 与 vfNodes/vfEdges', async () => {
    ;(getGraphVisualize as any).mockResolvedValue(sampleGraphData)
    ;(getGraphStats as any).mockResolvedValue(sampleStats)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.graphData.nodes).toHaveLength(2)
    expect(vm.graphData.links).toHaveLength(1)
    expect(vm.vfNodes).toHaveLength(2)
    expect(vm.vfEdges).toHaveLength(1)
    expect(vm.vfEdges[0].source).toBe('host-1')
    expect(vm.vfEdges[0].target).toBe('svc-1')
    expect(vm.vfEdges[0].label).toBe('RUNS_ON')
  })

  it('loadGraph 返回 error 时设置 neo4jError 且清空 graphData', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({
      nodes: [],
      links: [],
      error: 'Neo4j 不可用',
      hint: '检查连接',
    })
    ;(getGraphStats as any).mockResolvedValue(null)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.neo4jError).toContain('Neo4j 不可用')
    expect(vm.neo4jError).toContain('检查连接')
    expect(vm.graphData.nodes).toEqual([])
  })

  it('loadGraph 失败时 message.error 且 loading 恢复 false', async () => {
    ;(getGraphVisualize as any).mockRejectedValue({
      response: { data: { detail: '网络错误' } },
    })
    ;(getGraphStats as any).mockResolvedValue(null)
    const wrapper = mountView()
    await flushPromises()
    expect(mockMessage.error).toHaveBeenCalledWith('网络错误')
    const vm = wrapper.vm as any
    expect(vm.loading).toBe(false)
  })

  it('loadStats 失败时不抛错（仅 console.warn），stats 保持 null', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockRejectedValue(new Error('stats failed'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.stats).toBe(null)
    expect(mockMessage.error).not.toHaveBeenCalled()
  })

  it('statCards computed 反映 stats 与 graphData', async () => {
    ;(getGraphVisualize as any).mockResolvedValue(sampleGraphData)
    ;(getGraphStats as any).mockResolvedValue(sampleStats)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    const cards = vm.statCards
    expect(cards).toHaveLength(4)
    expect(cards[0]).toEqual(expect.objectContaining({ label: '实体总数', value: 50 }))
    expect(cards[1]).toEqual(expect.objectContaining({ label: '关系总数', value: 80 }))
    expect(cards[2]).toEqual(expect.objectContaining({ label: '当前节点', value: 2 }))
    expect(cards[3]).toEqual(expect.objectContaining({ label: '当前边', value: 1 }))
  })

  it('doSearch 空关键字清空 searchResults 且不调用 searchGraph', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(null)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.searchKeyword = '   '
    await vm.doSearch()
    expect(searchGraph).not.toHaveBeenCalled()
    expect(vm.searchResults).toEqual([])
  })

  it('doSearch 成功填充 searchResults', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(null)
    ;(searchGraph as any).mockResolvedValue({
      query: 'nginx',
      results: [{ name: 'host-1', type: 'Host', confidence: 0.9 }],
      count: 1,
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.searchKeyword = 'nginx'
    await vm.doSearch()
    expect(searchGraph).toHaveBeenCalledWith('nginx', 20)
    expect(vm.searchResults).toHaveLength(1)
    expect(vm.searchResults[0].name).toBe('host-1')
  })

  it('doSearch 失败时 message.error', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(null)
    ;(searchGraph as any).mockRejectedValue({
      response: { data: { detail: '搜索异常' } },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.searchKeyword = 'nginx'
    await vm.doSearch()
    expect(mockMessage.error).toHaveBeenCalledWith('搜索异常')
  })

  it('openEntityDetail 成功打开抽屉并加载详情', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(null)
    ;(getGraphEntity as any).mockResolvedValue(sampleEntityDetail)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openEntityDetail('host-1')
    expect(getGraphEntity).toHaveBeenCalledWith('host-1')
    expect(vm.detailVisible).toBe(true)
    expect(vm.selectedNodeName).toBe('host-1')
    expect(vm.selectedEntity).not.toBeNull()
    expect(vm.selectedEntity.entity.entity_type).toBe('Host')
    expect(vm.detailLoading).toBe(false)
  })

  it('openEntityDetail 404 时 message.warning', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(null)
    ;(getGraphEntity as any).mockRejectedValue({ response: { status: 404 } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openEntityDetail('missing')
    expect(mockMessage.warning).toHaveBeenCalledWith('未找到实体: missing')
    expect(vm.detailLoading).toBe(false)
  })

  it('openEntityDetail 其他错误时 message.error', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(null)
    ;(getGraphEntity as any).mockRejectedValue({
      response: { data: { detail: '服务异常' } },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.openEntityDetail('host-1')
    expect(mockMessage.error).toHaveBeenCalledWith('服务异常')
  })

  it('layoutNodes 空数组返回空', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(null)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.layoutNodes([], [])).toEqual([])
  })

  it('layoutNodes 单节点返回带 id/position/data 的节点', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(null)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    const result = vm.layoutNodes([{ id: 'host-1', type: 'Host', group: 1 }], [])
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe('host-1')
    expect(result[0].position).toBeDefined()
    expect(result[0].data.nodeType).toBe('Host')
  })

  it('buildEdges 设置 animated 与 stroke 颜色', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(null)
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    const edges = vm.buildEdges([
      { source: 'a', target: 'b', type: 'RELATED_TO' },
      { source: 'c', target: 'd', type: 'USES' },
    ])
    expect(edges).toHaveLength(2)
    expect(edges[0].animated).toBe(true) // RELATED_TO
    expect(edges[1].animated).toBe(false) // USES
    expect(edges[0].style.stroke).toBe('#607d8b') // RELATED_TO color
    expect(edges[1].style.stroke).toBe('#18a058') // USES color
  })

  it('watch entityTypeFilter 触发 loadGraph（带类型参数）', async () => {
    ;(getGraphVisualize as any).mockResolvedValue({ nodes: [], links: [] })
    ;(getGraphStats as any).mockResolvedValue(null)
    const wrapper = mountView()
    await flushPromises()
    ;(getGraphVisualize as any).mockClear()
    const vm = wrapper.vm as any
    vm.entityTypeFilter = 'Host'
    await flushPromises()
    expect(getGraphVisualize).toHaveBeenCalledWith('Host', 200)
  })
})
