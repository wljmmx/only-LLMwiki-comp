/**
 * P1-5: Frontmatter 解析与序列化工具
 *
 * Wiki 页面格式：---\n<YAML>\n---\n\n<body markdown>
 * 结构化编辑需要把 frontmatter 拆分为独立字段，同时保留未知字段（sources/created_at 等）。
 *
 * 使用 js-yaml 进行可靠的 YAML 解析/序列化（后端 PyYAML 产出兼容）。
 */
import { load as yamlLoad, dump as yamlDump } from 'js-yaml'

/** Wiki 页面类型词表（与 AGENTS.md 对齐） */
export const WIKI_PAGE_TYPES = [
  'entity',
  'concept',
  'incident',
  'runbook',
  'service',
  'host',
] as const

export type WikiPageType = (typeof WIKI_PAGE_TYPES)[number]

export interface ParsedFrontmatter {
  /** 结构化字段（用户可编辑） */
  slug: string
  title: string
  type: string
  tags: string[]
  /** 保留的未知字段（sources/created_at/updated_at/review_status 等） */
  rest: Record<string, unknown>
  /** 正文 Markdown（不含 frontmatter） */
  body: string
  /** 是否含合法 frontmatter */
  hasFrontmatter: boolean
}

/**
 * 解析 Markdown 内容，拆分 frontmatter 与正文
 *
 * 容错策略：
 * - 无 frontmatter（不以 --- 开头）→ hasFrontmatter=false，全部作为 body
 * - frontmatter 解析失败 → hasFrontmatter=false，原始内容作为 body（不丢数据）
 */
export function parseFrontmatter(content: string): ParsedFrontmatter {
  const empty: ParsedFrontmatter = {
    slug: '',
    title: '',
    type: 'concept',
    tags: [],
    rest: {},
    body: content,
    hasFrontmatter: false,
  }

  if (!content || !content.startsWith('---')) {
    return empty
  }

  // 找到闭合的 ---
  const endMatch = content.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/)
  if (!endMatch) {
    return empty
  }

  const yamlStr = endMatch[1]
  const body = endMatch[2] || ''

  let meta: Record<string, unknown>
  try {
    meta = (yamlLoad(yamlStr) as Record<string, unknown>) || {}
  } catch {
    // YAML 解析失败 → 退化为纯 body，不丢数据
    return empty
  }

  const slug = typeof meta.slug === 'string' ? meta.slug : ''
  const title = typeof meta.title === 'string' ? meta.title : ''
  const type = typeof meta.type === 'string' ? meta.type : 'concept'
  const tags = Array.isArray(meta.tags)
    ? meta.tags
        .map((t) => (typeof t === 'number' ? String(t) : t))
        .filter((t): t is string => typeof t === 'string')
    : []

  // 保留未知字段
  const { slug: _s, title: _t, type: _ty, tags: _tg, ...rest } = meta

  return {
    slug,
    title,
    type,
    tags,
    rest: rest as Record<string, unknown>,
    body,
    hasFrontmatter: true,
  }
}

/**
 * 序列化结构化字段 + 正文为完整 Markdown 内容
 *
 * 字段顺序：slug, title, type, tags, ...rest（与 AGENTS.md 骨架对齐）
 */
export function serializeFrontmatter(parsed: ParsedFrontmatter): string {
  const meta: Record<string, unknown> = {
    slug: parsed.slug,
    title: parsed.title,
    type: parsed.type,
    tags: parsed.tags,
    ...parsed.rest,
  }

  const yamlStr = yamlDump(meta, {
    // 不把中文转 \uXXXX
    noRefs: true,
    lineWidth: -1,
    // 保持数组内联格式（tags: [a, b, c]）
    flowLevel: 1,
  })

  const body = parsed.body.startsWith('\n') ? parsed.body : '\n' + parsed.body
  return `---\n${yamlStr}---${body}`
}

/**
 * 从结构化字段构建 ParsedFrontmatter
 *
 * 便于 WikiEditor 中表单字段 → 完整内容的转换
 */
export function buildFrontmatter(
  fields: { slug: string; title: string; type: string; tags: string[] },
  rest: Record<string, unknown>,
  body: string,
): ParsedFrontmatter {
  return {
    ...fields,
    rest,
    body,
    hasFrontmatter: true,
  }
}
