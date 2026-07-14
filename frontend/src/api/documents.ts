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
  return api.get<unknown, { content: string; format: string; source: string }>(`/documents/${docId}/content`)
}

export function getDocumentStats() {
  return api.get<unknown, DocumentStats>('/documents/stats')
}

export function deleteDocument(docId: string) {
  return api.delete(`/documents/${docId}`)
}

/**
 * 编译单个文档为 Wiki（非流式，全流水线：解析 → 抽取 → LLM 编译 wiki 页面）
 * POST /llm-wiki/recompile/{docId}?force=true
 * 与 SSE 流式版本相比，适合批量编译场景（无实时进度展示）。
 */
export function compileToWiki(docId: string, force = true) {
  return api.post<unknown, { pages_created?: number; pages_updated?: number }>(
    `/llm-wiki/recompile/${docId}`,
    null,
    { params: { force } },
  )
}

export function parseDocument(fmt: string, formData: FormData) {
  return api.post<unknown, { doc_id: string; status: string }>(`/parsers/parse/${fmt}`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// P3-3: 流水线状态
export interface PipelineStepStatus {
  name: string
  label: string
  status: 'pending' | 'running' | 'done' | 'error'
  started_at?: string | null
  duration_ms?: number | null
  error?: string | null
}

export interface PipelineStatusResponse {
  doc_id: string
  current_status: string
  steps: PipelineStepStatus[]
  retryable: boolean
  failed_step: string | null
  title: string
  format: string
}

export function getPipelineStatus(docId: string) {
  return api.get<unknown, PipelineStatusResponse>(`/documents/${docId}/pipeline-status`)
}
