import api from './index'
import type { SearchResponse } from '@/types/api'

export function searchKnowledge(q: string, limit = 20) {
  return api.get<any, SearchResponse>('/search', { params: { q, limit } })
}

export function getSearchStats() {
  return api.get<any, { total_documents: number; total_indexed: number }>('/search/stats')
}
