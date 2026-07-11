import api from './index'
import type { SearchResponse, SearchStats } from '@/types/api'

export function searchKnowledge(q: string, limit = 20) {
  return api.get<unknown, SearchResponse>('/search', { params: { q, limit } })
}

export function getSearchStats() {
  return api.get<unknown, SearchStats>('/search/stats')
}
