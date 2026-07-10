import api, { getApiBaseUrl, getAuthToken } from './index'
import type {
  WikiPage,
  WikiIndex,
  BacklinkItem,
  WikiPageUpdatePayload,
  WikiPageUpdateResult,
} from '@/types/api'

export function getWikiIndex() {
  return api.get<any, WikiIndex>('/llm-wiki/index')
}

export function listWikiPages() {
  return api.get<any, { pages: WikiPage[]; total: number }>('/llm-wiki/pages')
}

export function getWikiPage(slug: string) {
  return api.get<any, WikiPage>(`/llm-wiki/page/${slug}`)
}

// S16-2：用户直接编辑 wiki page
export function updateWikiPage(slug: string, payload: WikiPageUpdatePayload) {
  return api.put<any, WikiPageUpdateResult>(`/llm-wiki/page/${slug}`, payload)
}

export function getWikiBacklinks(slug: string) {
  return api.get<any, BacklinkItem[]>(`/llm-wiki/backlinks/${slug}`)
}

export function getWikiOrphans() {
  return api.get<any, { pages: WikiPage[] }>('/llm-wiki/orphans')
}

export function getWikiStale() {
  return api.get<any, { pages: WikiPage[] }>('/llm-wiki/stale')
}

// S7-6: Wiki Q&A
export interface WikiQueryResult {
  question: string
  answer: string
  cited_slugs: string[]
  recalled_pages: { slug: string; title: string; type: string; score: number }[]
  insufficient_knowledge: boolean
  error: string | null
}

export function queryWiki(question: string, recallLimit = 5, expandBacklinks = true) {
  return api.post<any, WikiQueryResult>('/llm-wiki/query', {
    question,
    recall_limit: recallLimit,
    expand_backlinks: expandBacklinks,
  })
}

// P1-4: 流式问答回调
export interface WikiQueryStreamCallbacks {
  /** meta 事件：召回页面 + cited_slugs（知识不足时含 answer） */
  onMeta: (data: {
    recalled_pages: { slug: string; title: string; type: string; score: number }[]
    cited_slugs: string[]
    insufficient_knowledge: boolean
    answer?: string
  }) => void
  /** delta 事件：增量回答文本 */
  onDelta: (text: string) => void
  /** done 事件：流式结束（含可选 writebacks） */
  onDone: (data: { writebacks: unknown[] }) => void
  /** error 事件：流式出错 */
  onError?: (message: string) => void
}

/**
 * P1-4: 流式 Wiki 问答
 *
 * 用 fetch + ReadableStream 消费 SSE（EventSource 不支持 POST）。
 * 手动解析 `event: <type>\ndata: <json>\n\n` 格式。
 *
 * @returns AbortController（可调用 .abort() 取消）
 */
export function queryWikiStream(
  question: string,
  callbacks: WikiQueryStreamCallbacks,
  options: { recallLimit?: number; expandBacklinks?: boolean } = {},
): AbortController {
  const controller = new AbortController()
  const { recallLimit = 5, expandBacklinks = true } = options

  const url = `${getApiBaseUrl()}/llm-wiki/query/stream`
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  const token = getAuthToken()
  if (token) headers.Authorization = `Bearer ${token}`

  fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      question,
      recall_limit: recallLimit,
      expand_backlinks: expandBacklinks,
    }),
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status}`)
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      const dispatch = (eventType: string, dataStr: string) => {
        if (!eventType || !dataStr) return
        try {
          const data = JSON.parse(dataStr)
          switch (eventType) {
            case 'meta':
              callbacks.onMeta(data)
              break
            case 'delta':
              callbacks.onDelta(data.text || '')
              break
            case 'done':
              callbacks.onDone(data)
              break
            case 'error':
              callbacks.onError?.(data.message || '流式查询出错')
              break
          }
        } catch {
          /* 忽略解析错误 */
        }
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // 按空行分割事件块
        let sepIdx: number
        while ((sepIdx = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, sepIdx)
          buffer = buffer.slice(sepIdx + 2)
          let eventType = ''
          let dataLines: string[] = []
          for (const line of block.split('\n')) {
            if (line.startsWith('event: ')) eventType = line.slice(7).trim()
            else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
          }
          dispatch(eventType, dataLines.join('\n'))
        }
      }
    })
    .catch((err) => {
      if (controller.signal.aborted) return
      callbacks.onError?.(err?.message || '流式查询失败')
    })

  return controller
}

// S7-7: Lint 健康检查
export interface LintIssue {
  type: string
  severity: string
  slug: string
  title: string
  message: string
  detail?: string
}

export interface LintReport {
  pages_checked: number
  total_issues: number
  by_type: Record<string, number>
  by_severity: Record<string, number>
  issues: LintIssue[]
}

export function runWikiLint(includeStale = true) {
  return api.post<any, LintReport>('/llm-wiki/lint', null, {
    params: { include_stale: includeStale },
  })
}

export function getLintSuggestions(limit = 20) {
  return api.get<any, { count: number; suggestions: any[] }>('/llm-wiki/lint/suggestions', {
    params: { limit },
  })
}

// S7-8: 漂移监控
export function recompileStale(pushReview = true) {
  return api.post<any, any>('/llm-wiki/recompile-stale', null, {
    params: { push_review: pushReview },
  })
}

// 单文档编译为 Wiki（全流水线：解析 → 抽取 → LLM 编译 wiki 页面）
export function recompileDocument(docId: string, force = true) {
  return api.post<any, any>(`/llm-wiki/recompile/${docId}`, null, {
    params: { force },
  })
}

export function checkDrift(docId: string) {
  return api.post<any, any>(`/llm-wiki/drift/check/${docId}`)
}

export function rebuildIndex() {
  return api.post<any, any>('/llm-wiki/index/rebuild')
}
