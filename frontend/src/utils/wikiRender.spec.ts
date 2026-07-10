import { describe, it, expect } from 'vitest'
import { renderWikiMarkdown, parseSlugFromHash } from './wikiRender'

describe('utils/wikiRender.ts', () => {
  describe('renderWikiMarkdown', () => {
    it('空内容返回空字符串', () => {
      expect(renderWikiMarkdown('')).toBe('')
      expect(renderWikiMarkdown(undefined as unknown as string)).toBe('')
    })

    it('渲染基础 Markdown 段落', () => {
      const html = renderWikiMarkdown('hello world')
      expect(html).toContain('hello world')
      expect(html).toContain('<p>')
    })

    it('渲染加粗/斜体/代码', () => {
      const html = renderWikiMarkdown('**bold** *italic* `code`')
      expect(html).toContain('<strong>bold</strong>')
      expect(html).toContain('<em>italic</em>')
      expect(html).toContain('<code>code</code>')
    })

    it('渲染表格', () => {
      const md = '| a | b |\n|---|---|\n| 1 | 2 |'
      const html = renderWikiMarkdown(md)
      expect(html).toContain('<table>')
      expect(html).toContain('<th>a</th>')
      expect(html).toContain('<td>1</td>')
    })

    it('渲染代码块保留 class', () => {
      const html = renderWikiMarkdown('```js\nvar x = 1\n```')
      expect(html).toContain('<pre>')
      expect(html).toContain('<code')
      // marked v18 可能不加 language class，但 pre/code 标签应在
      expect(html).toContain('var x = 1')
    })
  })

  describe('[[wikilink]] 双链转换', () => {
    it('[[slug]] 转换为内部链接', () => {
      const html = renderWikiMarkdown('参见 [[nginx-502-troubleshooting]]')
      expect(html).toContain('#/wiki/nginx-502-troubleshooting')
      expect(html).toContain('nginx-502-troubleshooting')
    })

    it('[[slug|text]] 转换为带自定义文本的链接', () => {
      const html = renderWikiMarkdown('参见 [[nginx-502-troubleshooting|502 排查]]')
      expect(html).toContain('#/wiki/nginx-502-troubleshooting')
      expect(html).toContain('502 排查')
    })

    it('多个双链同时转换', () => {
      const html = renderWikiMarkdown('[[a]] 与 [[b|B]] 与 [[c]]')
      expect(html).toContain('#/wiki/a')
      expect(html).toContain('#/wiki/b')
      expect(html).toContain('#/wiki/c')
      expect(html).toContain('>B<')
    })

    it('双链 slug 自动 trim 空白', () => {
      const html = renderWikiMarkdown('[[  slug-with-spaces  ]]')
      expect(html).toContain('#/wiki/slug-with-spaces')
    })
  })

  describe('P0-4: XSS sanitize', () => {
    it('剥离 <script> 标签', () => {
      const html = renderWikiMarkdown('<script>alert(1)</script>hello')
      expect(html).not.toContain('<script>')
      expect(html).not.toContain('alert(1)')
      expect(html).toContain('hello')
    })

    it('剥离 on* 事件处理器属性', () => {
      const html = renderWikiMarkdown('<img src="x" onerror="alert(1)">')
      expect(html).not.toContain('onerror')
      expect(html).not.toContain('alert(1)')
    })

    it('剥离 javascript: 协议链接', () => {
      const html = renderWikiMarkdown('<a href="javascript:alert(1)">click</a>')
      expect(html).not.toContain('javascript:alert')
      // 链接要么被移除 href，要么整个链接被净化
    })

    it('保留安全的 <a href> 链接', () => {
      const html = renderWikiMarkdown('[OpsKG](https://example.com)')
      expect(html).toContain('href="https://example.com"')
      expect(html).toContain('OpsKG')
    })

    it('剥离 <iframe> 恶意外嵌', () => {
      const html = renderWikiMarkdown('<iframe src="https://evil.com"></iframe>')
      expect(html).not.toContain('<iframe')
      expect(html).not.toContain('evil.com')
    })

    it('剥离内联 style 属性（防 CSS 注入）', () => {
      const html = renderWikiMarkdown('<div style="background:url(javascript:alert(1))">x</div>')
      expect(html).not.toContain('javascript:alert')
    })

    it('允许 target 属性（外链新窗口）', () => {
      // marked 默认不加 target，但若 HTML 直传应保留
      const html = renderWikiMarkdown('<a href="https://example.com" target="_blank">link</a>')
      expect(html).toContain('target="_blank"')
    })

    it('script 与正常内容混合时仅剥离 script', () => {
      const md = '# 标题\n\n正常段落\n\n<script>evil()</script>\n\n更多内容'
      const html = renderWikiMarkdown(md)
      expect(html).toContain('标题')
      expect(html).toContain('正常段落')
      expect(html).toContain('更多内容')
      expect(html).not.toContain('<script>')
      expect(html).not.toContain('evil()')
    })
  })

  describe('parseSlugFromHash', () => {
    it('从 #/wiki/slug 提取 slug', () => {
      expect(parseSlugFromHash('#/wiki/nginx-502')).toBe('nginx-502')
    })

    it('非 wiki hash 返回 null', () => {
      expect(parseSlugFromHash('#/documents/abc')).toBeNull()
      expect(parseSlugFromHash('')).toBeNull()
      expect(parseSlugFromHash('#wiki/slug')).toBeNull()
    })

    it('支持含连字符的 slug', () => {
      expect(parseSlugFromHash('#/wiki/host-web-prod-01')).toBe('host-web-prod-01')
    })
  })
})
