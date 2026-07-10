import { describe, it, expect } from 'vitest'
import {
  parseFrontmatter,
  serializeFrontmatter,
  buildFrontmatter,
  WIKI_PAGE_TYPES,
} from './frontmatter'

describe('utils/frontmatter.ts', () => {
  // ────────── parseFrontmatter ──────────

  describe('parseFrontmatter', () => {
    it('解析标准 frontmatter + body', () => {
      const content = `---
slug: nginx-502
title: Nginx 502 故障排查
type: incident
tags: [nginx, 502, upstream]
---
# Nginx 502

排查步骤...`
      const result = parseFrontmatter(content)
      expect(result.hasFrontmatter).toBe(true)
      expect(result.slug).toBe('nginx-502')
      expect(result.title).toBe('Nginx 502 故障排查')
      expect(result.type).toBe('incident')
      expect(result.tags).toEqual(['nginx', '502', 'upstream'])
      expect(result.body).toContain('# Nginx 502')
      expect(result.body).toContain('排查步骤...')
    })

    it('保留未知字段（sources/created_at 等）', () => {
      const content = `---
slug: test
title: Test
type: concept
tags: [a]
created_at: 2026-07-05T10:00:00Z
review_status: auto
---
body`
      const result = parseFrontmatter(content)
      expect(result.rest.created_at).toBe('2026-07-05T10:00:00Z')
      expect(result.rest.review_status).toBe('auto')
    })

    it('无 frontmatter → hasFrontmatter=false，全部作为 body', () => {
      const content = '# 只有正文\n\n无 frontmatter'
      const result = parseFrontmatter(content)
      expect(result.hasFrontmatter).toBe(false)
      expect(result.body).toBe(content)
      expect(result.slug).toBe('')
    })

    it('空内容 → hasFrontmatter=false', () => {
      const result = parseFrontmatter('')
      expect(result.hasFrontmatter).toBe(false)
      expect(result.body).toBe('')
    })

    it('YAML 格式错误 → 退化为纯 body，不丢数据', () => {
      const content = `---
slug: [unclosed
---
body`
      const result = parseFrontmatter(content)
      expect(result.hasFrontmatter).toBe(false)
      // 原始内容保留在 body 中
      expect(result.body).toBe(content)
    })

    it('tags 为空数组', () => {
      const content = `---
slug: test
title: Test
type: concept
tags: []
---
body`
      const result = parseFrontmatter(content)
      expect(result.tags).toEqual([])
    })

    it('tags 缺失 → 空数组', () => {
      const content = `---
slug: test
title: Test
type: concept
---
body`
      const result = parseFrontmatter(content)
      expect(result.tags).toEqual([])
    })

    it('type 缺失 → 默认 concept', () => {
      const content = `---
slug: test
title: Test
---
body`
      const result = parseFrontmatter(content)
      expect(result.type).toBe('concept')
    })
  })

  // ────────── serializeFrontmatter ──────────

  describe('serializeFrontmatter', () => {
    it('序列化基本字段 + body', () => {
      const parsed = buildFrontmatter(
        { slug: 'test', title: 'Test', type: 'concept', tags: ['a', 'b'] },
        {},
        '# 正文',
      )
      const result = serializeFrontmatter(parsed)
      expect(result).toContain('slug: test')
      expect(result).toContain('title: Test')
      expect(result).toContain('type: concept')
      expect(result).toContain('[a, b]')
      expect(result).toContain('# 正文')
      expect(result.startsWith('---\n')).toBe(true)
    })

    it('序列化保留未知字段', () => {
      const parsed = buildFrontmatter(
        { slug: 'test', title: 'Test', type: 'concept', tags: [] },
        { created_at: '2026-07-05T10:00:00Z', review_status: 'auto' },
        'body',
      )
      const result = serializeFrontmatter(parsed)
      // js-yaml 可能对日期格式加引号，用正则匹配
      expect(result).toMatch(/created_at: ['"]?2026-07-05T10:00:00Z['"]?/)
      expect(result).toContain('review_status: auto')
    })

    it('序列化中文不被转义', () => {
      const parsed = buildFrontmatter(
        { slug: 'test', title: '中文标题', type: 'concept', tags: ['标签'] },
        {},
        '正文',
      )
      const result = serializeFrontmatter(parsed)
      expect(result).toContain('中文标题')
      expect(result).toContain('标签')
    })

    it('parse → serialize 往返保持字段一致', () => {
      const original = `---
slug: nginx-502
title: Nginx 502 故障排查
type: incident
tags: [nginx, 502]
created_at: 2026-07-05T10:00:00Z
---
# 正文内容`
      const parsed = parseFrontmatter(original)
      const serialized = serializeFrontmatter(parsed)
      const reparsed = parseFrontmatter(serialized)

      expect(reparsed.slug).toBe(parsed.slug)
      expect(reparsed.title).toBe(parsed.title)
      expect(reparsed.type).toBe(parsed.type)
      expect(reparsed.tags).toEqual(parsed.tags)
      expect(reparsed.rest.created_at).toBe(parsed.rest.created_at)
      expect(reparsed.body).toBe(parsed.body)
    })
  })

  // ────────── WIKI_PAGE_TYPES ──────────

  describe('WIKI_PAGE_TYPES', () => {
    it('包含 6 种页面类型', () => {
      expect(WIKI_PAGE_TYPES).toEqual([
        'entity',
        'concept',
        'incident',
        'runbook',
        'service',
        'host',
      ])
    })
  })
})
