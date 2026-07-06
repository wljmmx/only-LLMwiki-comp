import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/index', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import api from '@/api/index'
import {
  getGraphVisualize,
  getGraphStats,
  searchGraph,
  getGraphEntity,
  getGraphByType,
} from './graph'

describe('api/graph.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getGraphVisualize 传递 entityType 与 limit 参数', async () => {
    const data = { nodes: [], links: [], node_count: 0, link_count: 0 }
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(data)

    const res = await getGraphVisualize('Host', 50)

    expect(api.get).toHaveBeenCalledWith('/graph/visualize', {
      params: { entity_type: 'Host', limit: 50 },
    })
    expect(res).toEqual(data)
  })

  it('getGraphVisualize 不传 entityType 时 entity_type 为 undefined', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      nodes: [],
      links: [],
      node_count: 0,
      link_count: 0,
    })

    await getGraphVisualize()

    expect(api.get).toHaveBeenCalledWith('/graph/visualize', {
      params: { entity_type: undefined, limit: 200 },
    })
  })

  it('getGraphVisualize 默认 limit=200', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      nodes: [],
      links: [],
      node_count: 0,
      link_count: 0,
    })

    await getGraphVisualize('Service')

    expect(api.get).toHaveBeenCalledWith('/graph/visualize', {
      params: { entity_type: 'Service', limit: 200 },
    })
  })

  it('getGraphStats 调用 /graph/stats', async () => {
    const stats = { total_entities: 10, total_relations: 5, by_type: [] }
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(stats)

    const res = await getGraphStats()

    expect(api.get).toHaveBeenCalledWith('/graph/stats')
    expect(res.total_entities).toBe(10)
  })

  it('searchGraph 传递 q 与 limit', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      query: 'nginx',
      results: [],
      count: 0,
    })

    await searchGraph('nginx', 5)

    expect(api.get).toHaveBeenCalledWith('/graph/search', {
      params: { q: 'nginx', limit: 5 },
    })
  })

  it('getGraphEntity 对实体 name 进行 URL 编码', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ entity: {}, related: [] })

    await getGraphEntity('web/prod-01')

    expect(api.get).toHaveBeenCalledWith('/graph/entity/web%2Fprod-01')
  })

  it('getGraphByType 传递 limit 参数', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      entity_type: 'Host',
      results: [],
      count: 0,
    })

    await getGraphByType('Host', 10)

    expect(api.get).toHaveBeenCalledWith('/graph/by-type/Host', {
      params: { limit: 10 },
    })
  })
})
