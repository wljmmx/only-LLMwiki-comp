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
  total_size_mb: number
  by_format: { format: string; cnt: number }[]
  by_status: { status: string; cnt: number }[]
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
  pending: number
  approved: number
  rejected: number
  modified: number
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
  // P1-15：可选字段，支持搜索结果点击跳转（文档结果带 doc_id，wiki 结果带 slug）
  doc_id?: string
  slug?: string
}

// P2-1.6：空结果兜底建议
export interface SearchSuggestions {
  similar_queries: string[]
  diagnosis: string
  upload_hint: string
  did_you_mean: string | null
}

export interface SearchResponse {
  query: string
  results: SearchResult[]
  count: number
  suggestions?: SearchSuggestions | null
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
  by_type: { type: string; count: number }[]
}

export interface BacklinkItem {
  slug: string
  title: string
  context: string
}

/** 搜索索引统计（与后端 search_engine.get_stats 对齐） */
export interface SearchStats {
  indexed_docs: number
  vectorized_docs: number
  numpy_enabled: boolean
}
