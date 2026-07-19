/** P4-2: SSE 订阅 composable — 通用 SSE 事件流处理 */
import { ref, onUnmounted } from 'vue'
import { getApiBaseUrl, getAuthToken } from '@/api/index'

// P2: 自动重连 — 指数退避参数
const MAX_RECONNECT_DELAY = 30000
let reconnectDelay = 1000
let reconnectTimer: ReturnType<typeof setTimeout> | null = null

function scheduleReconnect(connectFn: () => void) {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => {
    connectFn()
    reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY)
  }, reconnectDelay)
}

export interface SseEvent {
  type: string
  data: Record<string, any>
}

export interface UseSseOptions {
  onEvent?: (event: SseEvent) => void
  onError?: (error: string) => void
  onDone?: () => void
  onReconnecting?: (attempt: number, maxAttempts: number) => void
  autoReconnect?: boolean
  maxReconnectAttempts?: number
  reconnectBaseDelay?: number
}

export function useSse() {
  const connected = ref(false)
  const error = ref<string | null>(null)
  let abortController: AbortController | null = null
  let isUnsubscribed = false

  function subscribe(
    endpoint: string,
    options: UseSseOptions = {},
  ): () => void {
    const {
      autoReconnect = false,
      maxReconnectAttempts = 5,
      reconnectBaseDelay = 1000,
    } = options

    isUnsubscribed = false
    let reconnectAttempt = 0

    function connect() {
      if (isUnsubscribed) return

      const baseUrl = getApiBaseUrl()
      const url = `${baseUrl}${endpoint}`
      const token = getAuthToken()

      abortController = new AbortController()
      const headers: Record<string, string> = {
        'Accept': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
      }
      if (token) {
        headers['Authorization'] = `Bearer ${token}`
      }

      console.log('[SSE] Connecting to:', url)

      fetch(url, {
        method: 'POST',
        headers,
        signal: abortController.signal,
      }).then(async (response) => {
        if (!response.ok) {
          const errMsg = `SSE 连接失败: ${response.status} ${response.statusText}`
          error.value = errMsg
          console.error('[SSE]', errMsg)
          options.onError?.(errMsg)
          tryReconnect()
          return
        }
        connected.value = true
        error.value = null
        reconnectAttempt = 0
        console.log('[SSE] Connected')

        const reader = response.body?.getReader()
        const decoder = new TextDecoder()
        if (!reader) {
          error.value = '响应体为空'
          options.onError?.('响应体为空')
          tryReconnect()
          return
        }

        let buffer = ''
        let receivedDone = false
        try {
          while (true) {
            const { done, value } = await reader.read()
            if (done) {
              console.log('[SSE] Connection closed')
              break
            }
            buffer += decoder.decode(value, { stream: true })
            const events = buffer.split('\n\n')
            buffer = events.pop() || ''
            for (const raw of events) {
              const parsed = _parseSseEvent(raw)
              if (parsed) {
                options.onEvent?.(parsed)
                if (parsed.type === 'error') {
                  error.value = parsed.data?.message || '未知错误'
                  options.onError?.(error.value ?? '未知错误')
                }
                if (parsed.type === 'done') {
                  receivedDone = true
                  options.onDone?.()
                }
              }
            }
          }
          // 服务器流结束但未收到 done 事件 → 视为意外断开，触发重连
          if (!receivedDone) {
            tryReconnect()
          }
        } catch (err: any) {
          if (err.name !== 'AbortError') {
            error.value = err.message
            console.error('[SSE] Error:', err.message)
            options.onError?.(err.message)
            tryReconnect()
          }
        }
        connected.value = false
      }).catch((err: any) => {
        if (err.name !== 'AbortError') {
          error.value = err.message
          console.error('[SSE] Fetch error:', err.message)
          options.onError?.(err.message)
          tryReconnect()
        }
      })
    }

    function tryReconnect() {
      if (!autoReconnect || isUnsubscribed) return
      if (reconnectAttempt >= maxReconnectAttempts) {
        console.log('[SSE] Max reconnect attempts reached')
        return
      }
      const delay = Math.min(
        reconnectBaseDelay * Math.pow(2, reconnectAttempt),
        30000,
      )
      reconnectAttempt++
      options.onReconnecting?.(reconnectAttempt, maxReconnectAttempts)
      console.log(
        `[SSE] Reconnecting in ${delay}ms (attempt ${reconnectAttempt}/${maxReconnectAttempts})`,
      )
      setTimeout(connect, delay)
    }

    connect()

    return () => {
      isUnsubscribed = true
      abortController?.abort()
      connected.value = false
    }
  }

  function unsubscribe() {
    isUnsubscribed = true
    abortController?.abort()
    connected.value = false
  }

  onUnmounted(() => {
    unsubscribe()
  })

  return { subscribe, unsubscribe, connected, error }
}

function _parseSseEvent(raw: string): SseEvent | null {
  const lines = raw.split('\n')
  let eventType = 'message'
  let dataStr = ''
  for (const line of lines) {
    if (line.startsWith('event: ')) {
      eventType = line.slice(7).trim()
    } else if (line.startsWith('data: ')) {
      dataStr = line.slice(6)
    }
  }
  if (!dataStr) return null
  try {
    return { type: eventType, data: JSON.parse(dataStr) }
  } catch {
    return { type: eventType, data: { raw: dataStr } }
  }
}