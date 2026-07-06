import api from './index'
import type { DocumentListResponse, DocumentMeta, DocumentStats } from '@/types/api'

export function listDocuments(params?: {
  limit?: number
  offset?: number
  format?: string
  status?: string
}) {
  return api.get<any, DocumentListResponse>('/documents', { params })
}

export function getDocument(docId: string) {
  return api.get<any, DocumentMeta>(`/documents/${docId}`)
}

export function getDocumentContent(docId: string) {
  return api.get<any, { content: string; format: string }>(`/documents/${docId}/content`)
}

export function getDocumentStats() {
  return api.get<any, DocumentStats>('/documents/stats')
}

export function deleteDocument(docId: string) {
  return api.delete(`/documents/${docId}`)
}

export function parseDocument(fmt: string, formData: FormData) {
  return api.post<any, { doc_id: string; status: string }>(`/parsers/parse/${fmt}`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
