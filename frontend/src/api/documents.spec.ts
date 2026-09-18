import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/index', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import api from '@/api/index'
import {
  listDocuments,
  searchDocuments,
  getDocument,
  getDocumentContent,
  getDocumentStats,
  deleteDocument,
  compileToWiki,
  parseDocument,
  getPipelineStatus,
} from './documents'

describe('api/documents.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('listDocuments GET /documents 携带分页/过滤参数', async () => {
    const data = { documents: [], total: 0 }
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(data)
    await listDocuments({ limit: 10, offset: 20, format: 'md' })
    expect(api.get).toHaveBeenCalledWith('/documents', {
      params: { limit: 10, offset: 20, format: 'md' },
    })
  })

  it('searchDocuments GET /documents/search 携带 q 与默认 limit=50', async () => {
    const data = { query: 'nginx', results: [], count: 0 }
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(data)
    await searchDocuments('nginx')
    expect(api.get).toHaveBeenCalledWith('/documents/search', {
      params: { q: 'nginx', limit: 50 },
    })
  })

  it('getDocument / getDocumentContent / getDocumentStats 命中路径', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await getDocument('d1')
    expect(api.get).toHaveBeenCalledWith('/documents/d1')
    await getDocumentContent('d1')
    expect(api.get).toHaveBeenCalledWith('/documents/d1/content')
    await getDocumentStats()
    expect(api.get).toHaveBeenCalledWith('/documents/stats')
  })

  it('deleteDocument DELETE /documents/{id}', async () => {
    ;(api.delete as ReturnType<typeof vi.fn>).mockResolvedValue({ deleted: true })
    await deleteDocument('d1')
    expect(api.delete).toHaveBeenCalledWith('/documents/d1')
  })

  it('compileToWiki POST /llm-wiki/recompile/{id} 默认 force=true', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ pages_created: 1 })
    await compileToWiki('d1')
    expect(api.post).toHaveBeenCalledWith('/llm-wiki/recompile/d1', null, {
      params: { force: true },
    })
  })

  it('compileToWiki 支持 force=false', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await compileToWiki('d1', false)
    expect(api.post).toHaveBeenCalledWith('/llm-wiki/recompile/d1', null, {
      params: { force: false },
    })
  })

  it('parseDocument POST multipart 携带 Content-Type', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ doc_id: 'x', status: 'done' })
    const fd = new FormData()
    fd.append('file', 'raw')
    await parseDocument('md', fd)
    expect(api.post).toHaveBeenCalledWith('/parsers/parse/md', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  })

  it('getPipelineStatus GET 携带 pipeline-status 路径', async () => {
    const data = { doc_id: 'd1', current_status: 'compiled', steps: [] }
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(data)
    await getPipelineStatus('d1')
    expect(api.get).toHaveBeenCalledWith('/documents/d1/pipeline-status')
  })
})