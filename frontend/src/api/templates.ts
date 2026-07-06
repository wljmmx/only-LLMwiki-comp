import api from './index'

// ────────── Templates（F10 模板管理） ──────────

export interface Template {
  id?: number
  slug: string
  name: string
  category: string
  description?: string
  content: string
  is_builtin?: number | boolean
  created_at?: string
  updated_at?: string
}

export interface TemplateListResponse {
  templates: Template[]
  count: number
}

export interface RenderResult {
  slug: string
  rendered: string
  length: number
}

/**
 * 列出模板（可选按分类过滤）
 * GET /templates?category=xxx
 */
export function listTemplates(category?: string) {
  return api.get<any, TemplateListResponse>('/templates', {
    params: category ? { category } : {},
  })
}

/**
 * 获取单个模板
 * GET /templates/{slug}
 */
export function getTemplate(slug: string) {
  return api.get<any, Template>(`/templates/${encodeURIComponent(slug)}`)
}

/**
 * 创建自定义模板
 * POST /templates?slug=&name=&content=&category=&description=
 */
export function createTemplate(payload: {
  slug: string
  name: string
  content: string
  category?: string
  description?: string
}) {
  return api.post<any, Template>('/templates', null, {
    params: {
      slug: payload.slug,
      name: payload.name,
      content: payload.content,
      category: payload.category ?? 'custom',
      description: payload.description ?? '',
    },
  })
}

/**
 * 更新模板（内置模板不可改 content）
 * PUT /templates/{slug}
 */
export function updateTemplate(
  slug: string,
  payload: {
    name?: string
    content?: string
    category?: string
    description?: string
  },
) {
  return api.put<any, Template>(
    `/templates/${encodeURIComponent(slug)}`,
    null,
    { params: payload },
  )
}

/**
 * 删除模板（仅自定义）
 * DELETE /templates/{slug}
 */
export function deleteTemplate(slug: string) {
  return api.delete<any, { deleted: boolean; slug: string }>(
    `/templates/${encodeURIComponent(slug)}`,
  )
}

/**
 * 渲染模板
 * POST /templates/{slug}/render  body: { variables: {...} }
 */
export function renderTemplate(slug: string, variables: Record<string, any>) {
  return api.post<any, RenderResult>(
    `/templates/${encodeURIComponent(slug)}/render`,
    variables,
  )
}
