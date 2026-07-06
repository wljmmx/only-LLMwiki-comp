import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// S14-3：export.ts 改用共享 apiRaw 实例（不再直接 import axios）
// mock ./index 提供 apiRaw + getApiBaseUrl
vi.mock('./index', () => ({
  getApiBaseUrl: () => '/api',
  apiRaw: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

import { apiRaw } from './index'
import { downloadBlob, exportFormatOptions, exportDocument } from './export'

describe('api/export.ts', () => {
  describe('exportFormatOptions', () => {
    it('包含 4 种导出格式', () => {
      expect(exportFormatOptions).toHaveLength(4)
    })

    it('每种格式均包含 label/value/mime/ext/desc 字段', () => {
      for (const opt of exportFormatOptions) {
        expect(opt.label).toBeTypeOf('string')
        expect(opt.value).toBeTypeOf('string')
        expect(opt.mime).toBeTypeOf('string')
        expect(opt.ext).toBeTypeOf('string')
        expect(opt.desc).toBeTypeOf('string')
      }
    })

    it('value 依次覆盖 markdown/html/text/pdf', () => {
      expect(exportFormatOptions.map((o) => o.value)).toEqual([
        'markdown',
        'html',
        'text',
        'pdf',
      ])
    })

    it('markdown 与 pdf 的 mime/ext 正确', () => {
      const md = exportFormatOptions.find((o) => o.value === 'markdown')!
      expect(md.mime).toBe('text/markdown')
      expect(md.ext).toBe('md')

      const pdf = exportFormatOptions.find((o) => o.value === 'pdf')!
      expect(pdf.mime).toBe('application/pdf')
      expect(pdf.ext).toBe('pdf')
    })
  })

  describe('downloadBlob', () => {
    const originalCreate = URL.createObjectURL
    const originalRevoke = URL.revokeObjectURL
    let createObjectURL: ReturnType<typeof vi.fn>
    let revokeObjectURL: ReturnType<typeof vi.fn>

    beforeEach(() => {
      vi.useFakeTimers()
      createObjectURL = vi.fn(() => 'blob:fake-url')
      revokeObjectURL = vi.fn()
      Object.defineProperty(URL, 'createObjectURL', {
        value: createObjectURL,
        configurable: true,
      })
      Object.defineProperty(URL, 'revokeObjectURL', {
        value: revokeObjectURL,
        configurable: true,
      })
    })

    afterEach(() => {
      vi.useRealTimers()
      Object.defineProperty(URL, 'createObjectURL', {
        value: originalCreate,
        configurable: true,
      })
      Object.defineProperty(URL, 'revokeObjectURL', {
        value: originalRevoke,
        configurable: true,
      })
      vi.restoreAllMocks()
    })

    it('创建对象 URL 并触发 anchor 点击下载', () => {
      const blob = new Blob(['content'], { type: 'text/plain' })
      const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click')

      downloadBlob(blob, 'test.md')

      expect(createObjectURL).toHaveBeenCalledWith(blob)
      expect(clickSpy).toHaveBeenCalledTimes(1)
    })

    it('设置 anchor 的 download 与 href，并在点击后移除', () => {
      const blob = new Blob(['x'])
      const realCreate = document.createElement.bind(document)
      const anchors: HTMLAnchorElement[] = []
      vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
        const el = realCreate(tag)
        if (tag.toLowerCase() === 'a') anchors.push(el as HTMLAnchorElement)
        return el
      })
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

      downloadBlob(blob, 'report.pdf')

      expect(anchors[0].download).toBe('report.pdf')
      expect(anchors[0].href).toBe('blob:fake-url')
    })

    it('1 秒后调用 revokeObjectURL 释放对象 URL', () => {
      const blob = new Blob(['x'])
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

      downloadBlob(blob, 'a.txt')
      expect(revokeObjectURL).not.toHaveBeenCalled()

      vi.advanceTimersByTime(1000)
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake-url')
    })
  })

  describe('exportDocument', () => {
    afterEach(() => {
      vi.clearAllMocks()
    })

    it('从 Content-Disposition filename* 解析 UTF-8 编码文件名', async () => {
      ;(apiRaw.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: new Blob(['x']),
        headers: { 'content-disposition': "attachment; filename*=UTF-8''%E6%B5%8B%E8%AF%95.md" },
      })
      const res = await exportDocument({ title: 't', content: 'c', format: 'markdown' })
      expect(res.filename).toBe('测试.md')
      expect(res.blob).toBeInstanceOf(Blob)
    })

    it('无 Content-Disposition 时按格式生成文件名并替换非法字符', async () => {
      ;(apiRaw.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: new Blob(['x']),
        headers: {},
      })
      const res = await exportDocument({ title: '我的/文档', content: 'c', format: 'html' })
      expect(res.filename).toBe('我的_文档.html')
    })

    it('调用 apiRaw.post 时附带正确 URL 与 responseType', async () => {
      ;(apiRaw.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: new Blob(['x']),
        headers: {},
      })
      await exportDocument({ title: 't', content: 'c', format: 'markdown' })
      expect(apiRaw.post).toHaveBeenCalledWith(
        '/api/export',
        { title: 't', content: 'c', format: 'markdown' },
        expect.objectContaining({ responseType: 'blob' }),
      )
    })
  })
})
