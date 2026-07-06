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
  // S16-2：后端 GET /llm-wiki/page/{slug} 实际返回的额外字段
  version?: number
  content_html?: string
  backlinks?: { source: string; display: string; count: number }[]
  outlinks?: { target: string; display: string; count: number }[]
}

// S16-2：PUT /llm-wiki/page/{slug} 请求体
export interface WikiPageUpdatePayload {
  content: string
  title?: string
  change_summary?: string
  expected_version?: number
  bypass_lock?: boolean
}

// S16-2：PUT /llm-wiki/page/{slug} 响应
export interface WikiPageUpdateResult {
  slug: string
  title: string
  version: number
  checksum: string
  created_at: string
  skipped: boolean
  reason?: string
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
