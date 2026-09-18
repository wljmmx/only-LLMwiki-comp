import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/index', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import api from '@/api/index'
import { searchKnowledge, getSearchStats } from './search'

describe('api/search.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('searchKnowledge GET /search 携带 q 与默认 limit=20', async () => {
    const data = { query: 'nginx', results: [], count: 0 }
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(data)
    await searchKnowledge('nginx')
    expect(api.get).toHaveBeenCalledWith('/search', { params: { q: 'nginx', limit: 20 } })
  })

  it('searchKnowledge 支持自定义 limit', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await searchKnowledge('er', 100)
    expect(api.get).toHaveBeenCalledWith('/search', { params: { q: 'er', limit: 100 } })
  })

  it('getSearchStats GET /search/stats', async () => {
    const data = { total_docs: 3, total_pages: 12 }
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(data)
    await getSearchStats()
    expect(api.get).toHaveBeenCalledWith('/search/stats')
  })
})