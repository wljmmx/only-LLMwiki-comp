import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/index', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import api from '@/api/index'
import {
  listVersions,
  getVersion,
  diffVersions,
  saveVersion,
  rollbackVersion,
  listWikiDocs,
} from './versions'

describe('api/versions.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('listVersions GET /versions/{docKey} 编码斜杠', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ doc_key: 'a', versions: [], count: 0 })
    await listVersions('wiki/a')
    expect(api.get).toHaveBeenCalledWith('/versions/wiki%2Fa')
  })

  it('getVersion GET /versions/{docKey}/{version}', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await getVersion('wiki/a', 3)
    expect(api.get).toHaveBeenCalledWith('/versions/wiki%2Fa/3')
  })

  it('diffVersions GET 拼接 v1/v2', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await diffVersions('wiki/a', 1, 3)
    expect(api.get).toHaveBeenCalledWith('/versions/wiki%2Fa/diff/1/3')
  })

  it('saveVersion POST 以 query 参数传递 title/content/change_summary/author', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ doc_key: 'a', version: 2, checksum: 'c' })
    await saveVersion('wiki/a', {
      title: 't',
      content: 'body',
      change_summary: 'summary',
      author: 'alice',
    })
    expect(api.post).toHaveBeenCalledWith('/versions/wiki%2Fa/save', null, {
      params: {
        title: 't',
        content: 'body',
        change_summary: 'summary',
        author: 'alice',
      },
    })
  })

  it('rollbackVersion POST rollback 路径', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ doc_key: 'a', version: 4, checksum: 'c' })
    await rollbackVersion('wiki/a', 1)
    expect(api.post).toHaveBeenCalledWith('/versions/wiki%2Fa/rollback/1')
  })

  it('listWikiDocs GET /wiki 携带 limit/offset 默认值', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ documents: [], count: 0 })
    await listWikiDocs()
    expect(api.get).toHaveBeenCalledWith('/wiki', { params: { limit: 50, offset: 0 } })
  })
})