import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/index', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import api from '@/api/index'
import {
  listTemplates,
  getTemplate,
  createTemplate,
  updateTemplate,
  deleteTemplate,
  renderTemplate,
} from './templates'

describe('api/templates.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('listTemplates GET /templates 无分类传空 params', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ templates: [], count: 0 })
    await listTemplates()
    expect(api.get).toHaveBeenCalledWith('/templates', { params: {} })
  })

  it('listTemplates 携带 category 过滤', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ templates: [], count: 0 })
    await listTemplates('incident')
    expect(api.get).toHaveBeenCalledWith('/templates', { params: { category: 'incident' } })
  })

  it('getTemplate GET 编码 slug', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await getTemplate('nginx 502')
    expect(api.get).toHaveBeenCalledWith('/templates/nginx%20502')
  })

  it('createTemplate POST 携带默认 category/description', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ slug: 's', name: 'n' })
    await createTemplate({ slug: 's', name: 'n', content: 'c' })
    expect(api.post).toHaveBeenCalledWith('/templates', null, {
      params: {
        slug: 's',
        name: 'n',
        content: 'c',
        category: 'custom',
        description: '',
      },
    })
  })

  it('updateTemplate PUT 携带覆盖字段', async () => {
    ;(api.put as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await updateTemplate('s', { name: 'new' })
    expect(api.put).toHaveBeenCalledWith('/templates/s', null, { params: { name: 'new' } })
  })

  it('deleteTemplate DELETE 编码 slug', async () => {
    ;(api.delete as ReturnType<typeof vi.fn>).mockResolvedValue({ deleted: true, slug: 's' })
    await deleteTemplate('s')
    expect(api.delete).toHaveBeenCalledWith('/templates/s')
  })

  it('renderTemplate POST body 携带变量', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ slug: 's', rendered: '<p>', length: 3 })
    await renderTemplate('s', { host: 'h1' })
    expect(api.post).toHaveBeenCalledWith('/templates/s/render', { host: 'h1' })
  })
})