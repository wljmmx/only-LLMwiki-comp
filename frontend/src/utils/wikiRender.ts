import { marked } from 'marked'

/**
 * 将 `[[slug]]` 和 `[[slug|text]]` 双向链接转换为标准 Markdown 链接
 * 然后用 marked 渲染为 HTML
 */
export function renderWikiMarkdown(content: string): string {
  if (!content) return ''

  let text = content

  // [[slug|text]] → [text](#/wiki/slug)
  text = text.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, (_match, slug, label) => {
    const s = slug.trim()
    const l = label.trim()
    return `[${l}](#/wiki/${s})`
  })

  // [[slug]] → [slug](#/wiki/slug)
  text = text.replace(/\[\[([^\]]+)\]\]/g, (_match, slug) => {
    const s = slug.trim()
    return `[${s}](#/wiki/${s})`
  })

  // 用 marked 渲染剩余 Markdown
  return marked.parse(text, { async: false }) as string
}

/**
 * 从 hash 路由中提取 slug（#/wiki/slug → slug）
 */
export function parseSlugFromHash(hash: string): string | null {
  const match = hash.match(/^#\/wiki\/(.+)$/)
  return match ? match[1] : null
}
