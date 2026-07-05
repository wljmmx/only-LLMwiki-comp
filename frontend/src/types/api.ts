export interface DocumentMeta {
  id: string
  title: string
  filename: string
  format: string
  size: number
  checksum: string
  status: 'uploaded' | 'parsing' | 'parsed' | 'failed'
  created_at: string
  updated_at: string
  metadata?: Record<string, any>
}

export interface DocumentListResponse {
  documents: DocumentMeta[]
  stats: DocumentStats
  limit: number
  offset: number
}

export interface DocumentStats {
  total: number
  by_format: Record<string, number>
  by_status: Record<string, number>
}

export interface ReviewItem {
  id: string
  type: string
  title: string
  status: 'pending' | 'approved' | 'rejected'
  source_doc_id: string
  created_at: string
  content?: string
  reason?: string
}

export interface ReviewStats {
  total: number
  pending: number
  approved: number
  rejected: number
}

export interface ReviewQueueResponse {
  items: ReviewItem[]
  stats: ReviewStats
  total: number
}

export interface SearchResult {
  id: string
  title: string
  snippet: string
  score: number
  type: string
}

export interface SearchResponse {
  query: string
  results: SearchResult[]
  count: number
}

export interface WikiPage {
  slug: string
  title: string
  type: string
  tags: string[]
  content: string
  created_at: string
  updated_at: string
}

export interface WikiIndex {
  total_pages: number
  by_type: Record<string, number>
  recent: { slug: string; title: string; updated_at: string }[]
}

export interface GraphStats {
  total_entities: number
  total_relations: number
  by_entity_type: Record<string, number>
}

export interface BacklinkItem {
  slug: string
  title: string
  context: string
}
