import api from './index'
import type { DocumentListResponse, DocumentMeta, DocumentStats } from '@/types/api'

export function listDocuments(params?: {
  limit?: number
  offset?: number
  format?: string
  status?: string
}) {
  return api.get<unknown, DocumentListResponse>('/documents', { params })
}

/**
 * 服务端搜索文档（按文件名/标题/doc_id）
 * GET /documents/search?q=&limit=
 * 后端 LIKE 搜索，跨全表（不受分页限制），返回 { query, results, count }
 */
export function searchDocuments(q: string, limit = 50) {
  return api.get<unknown, { query: string; results: DocumentMeta[]; count: number }>(
    '/documents/search',
    { params: { q, limit } },
  )
}

export function getDocument(docId: string) {
  return api.get<unknown, DocumentMeta>(`/documents/${docId}`)
}

export function getDocumentContent(docId: string) {
  return api.get<unknown, { content: string; format: string }>(`/documents/${docId}/content`)
}

export function getDocumentStats() {
  return api.get<unknown, DocumentStats>('/documents/stats')
}

export function deleteDocument(docId: string) {
  return api.delete(`/documents/${docId}`)
}

export function parseDocument(fmt: string, formData: FormData) {
  return api.post<unknown, { doc_id: string; status: string }>(`/parsers/parse/${fmt}`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
