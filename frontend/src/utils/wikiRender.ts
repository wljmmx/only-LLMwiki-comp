import { marked } from 'marked'
import DOMPurify from 'dompurify'

/**
 * 将 `[[slug]]` 和 `[[slug|text]]` 双向链接转换为标准 Markdown 链接
 * 然后用 marked 渲染为 HTML，最后经 DOMPurify sanitize 防 XSS
 *
 * P0-4: 所有 v-html 渲染必须经此函数，确保恶意 wiki 内容无法执行脚本
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
  const rawHtml = marked.parse(text, { async: false }) as string

  // P0-4: DOMPurify sanitize —— 移除脚本/事件处理器/危险属性
  // 允许 target 属性（外链）与 class（代码高亮），其余沿用安全默认
  return DOMPurify.sanitize(rawHtml, {
    ADD_ATTR: ['target'],
    // 允许 code/pre 上的 class（marked 语法高亮标记）
    ALLOWED_ATTR: ['href', 'src', 'alt', 'title', 'class', 'target', 'rel', 'id', 'colspan', 'rowspan'],
  })
}

/**
 * 从 hash 路由中提取 slug（#/wiki/slug → slug）
 */
export function parseSlugFromHash(hash: string): string | null {
  const match = hash.match(/^#\/wiki\/(.+)$/)
  return match ? match[1] : null
}
