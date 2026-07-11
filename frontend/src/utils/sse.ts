/**
 * P4-3: 共享 SSE 流式解析工具
 *
 * 统一 wiki.ts 与 mcp.ts 中重复的 SSE 解析逻辑。
 * 用 fetch + ReadableStream 消费 SSE（EventSource 不支持 POST）。
 */

import { getAuthToken } from '@/api/index'

export interface SseEvent {
  /** 事件类型（SSE event: 行的值，缺省为 'message'） */
  type: string
  /** 事件数据（已尝试 JSON.parse，失败则保留原始字符串） */
  data: unknown
}

export interface SseStreamOptions {
  /** 请求 URL（由调用方拼装完整 URL，通常为 `${getApiBaseUrl()}/...`） */
  url: string
  /** 请求体（会被 JSON.stringify） */
  body: unknown
  /** 外部 AbortSignal（可选） */
  signal?: AbortSignal
  /** 是否在流结束时发送合成 done 事件（默认 false） */
  emitSyntheticDone?: boolean
}

/**
 * 发起 SSE POST 请求，逐事件回调。
 *
 * - 自动注入 Authorization 头（getAuthToken）
 * - 自动设置 Content-Type: application/json + Accept: text/event-stream
 * - 按 SSE 规范解析 event:/data: 行（lenient：允许冒号后无空格）
 * - 多行 data: 拼接为单个 payload
 * - HTTP 错误抛出 Error（经 .catch 路由到 onError）
 *
 * @returns AbortController（可调用 .abort() 取消）
 */
export function streamSse(
  options: SseStreamOptions,
  onEvent: (ev: SseEvent) => void,
  onError?: (message: string) => void,
): AbortController {
  const controller = new AbortController()
  const { url, body, signal, emitSyntheticDone = false } = options

  // 转发外部 signal 的 abort 到内部 controller
  if (signal) {
    signal.addEventListener('abort', () => controller.abort())
  }

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  const token = getAuthToken()
  if (token) headers.Authorization = `Bearer ${token}`

  fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status}`)
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // 按空行分割事件块（indexOf 风格，避免无谓 split）
        let sepIdx: number
        while ((sepIdx = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, sepIdx)
          buffer = buffer.slice(sepIdx + 2)

          let eventType = 'message'
          const dataLines: string[] = []
          for (const line of block.split('\n')) {
            // lenient：允许冒号后无空格（spec 允许冒号后有一个可选空格）
            if (line.startsWith('event:')) {
              eventType = line.slice(6).trim()
            } else if (line.startsWith('data:')) {
              dataLines.push(line.slice(5).trim())
            }
          }

          // 空帧守卫：无 data 行则跳过（对齐 wiki.ts 行为）
          if (dataLines.length === 0) continue

          const dataStr = dataLines.join('\n')
          let data: unknown = dataStr
          try {
            data = JSON.parse(dataStr)
          } catch {
            // 保留为原始字符串
          }

          onEvent({ type: eventType, data })
        }
      }

      if (emitSyntheticDone) {
        onEvent({ type: 'done', data: null })
      }
    })
    .catch((err) => {
      if (controller.signal.aborted) return
      onError?.(err?.message || 'SSE 流式请求失败')
    })

  return controller
}
