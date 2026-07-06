import axios from 'axios'
import { getApiBaseUrl } from './index'

// ────────── Export（F12 导出中心） ──────────

export type ExportFormat = 'markdown' | 'html' | 'text' | 'pdf'

export interface ExportPayload {
  title: string
  content: string
  format: ExportFormat
}

export interface ExportFormatOption {
  label: string
  value: ExportFormat
  mime: string
  ext: string
  desc: string
}

export const exportFormatOptions: ExportFormatOption[] = [
  { label: 'Markdown (.md)', value: 'markdown', mime: 'text/markdown', ext: 'md', desc: '保留原始 Markdown 语法' },
  { label: 'HTML (.html)', value: 'html', mime: 'text/html', ext: 'html', desc: '渲染为带样式的 HTML，可在浏览器直接打开' },
  { label: '纯文本 (.txt)', value: 'text', mime: 'text/plain', ext: 'txt', desc: '去除 Markdown 标记的纯文本' },
  { label: 'PDF (.pdf)', value: 'pdf', mime: 'application/pdf', ext: 'pdf', desc: '需服务端安装 wkhtmltopdf' },
]

function getAuthToken(): string | null {
  if (typeof localStorage === 'undefined') return null
  return localStorage.getItem('opskg_token')
}

/**
 * 导出文档为指定格式（同步返回二进制流）
 * POST /export  body: { title, content, format }
 * 返回: { blob, filename }
 */
export async function exportDocument(payload: ExportPayload): Promise<{ blob: Blob; filename: string }> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getAuthToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await axios.post(`${getApiBaseUrl()}/export`, payload, {
    responseType: 'blob',
    headers,
  })

  // 从 Content-Disposition 解析文件名（RFC 5987 编码）
  const cd = res.headers['content-disposition'] || ''
  let filename = ''
  // 优先 filename*=UTF-8''<encoded>
  const starMatch = cd.match(/filename\*=UTF-8''([^;]+)/i)
  if (starMatch) {
    try {
      filename = decodeURIComponent(starMatch[1])
    } catch {
      filename = starMatch[1]
    }
  } else {
    const plainMatch = cd.match(/filename="?([^";]+)"?/i)
    if (plainMatch) filename = plainMatch[1]
  }

  if (!filename) {
    const ext = exportFormatOptions.find((o) => o.value === payload.format)?.ext || 'md'
    const safeTitle = payload.title.replace(/[\/\\]/g, '_').slice(0, 50) || 'export'
    filename = `${safeTitle}.${ext}`
  }

  return { blob: res.data as Blob, filename }
}

/** 浏览器触发下载 */
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
