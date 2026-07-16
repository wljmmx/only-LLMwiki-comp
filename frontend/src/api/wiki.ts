import api, { getApiBaseUrl } from './index'
import { streamSse } from '@/utils/sse'
import type {
  WikiPage,
  WikiIndex,
  BacklinkItem,
  WikiPageUpdatePayload,
  WikiPageUpdateResult,
  CompileTraceResponse,
} from '@/types/api'

export function getWikiIndex() {
  return api.get<unknown, WikiIndex>('/llm-wiki/index')
}

export function listWikiPages() {
  return api.get<unknown, { pages: WikiPage[]; total: number }>('/llm-wiki/pages')
}

export function getWikiPage(slug: string) {
  return api.get<unknown, WikiPage>(`/llm-wiki/page/${slug}`)
}

// S16-2：用户直接编辑 wiki page
export function updateWikiPage(slug: string, payload: WikiPageUpdatePayload) {
  return api.put<any, WikiPageUpdateResult>(`/llm-wiki/page/${slug}`, payload)
}

export function getWikiBacklinks(slug: string) {
  return api.get<unknown, BacklinkItem[]>(`/llm-wiki/backlinks/${slug}`)
}

export function getWikiOrphans() {
  return api.get<unknown, { pages: WikiPage[] }>('/llm-wiki/orphans')
}

export function getWikiStale() {
  return api.get<unknown, { pages: WikiPage[] }>('/llm-wiki/stale')
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

export function queryWiki(
  question: string,
  recallLimit = 5,
  expandBacklinks = true,
  history?: { role: 'user' | 'assistant'; content: string }[],
) {
  return api.post<unknown, WikiQueryResult>('/llm-wiki/query', {
    question,
    recall_limit: recallLimit,
    expand_backlinks: expandBacklinks,
    history,
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

/** P2-13b：多轮会话历史条目（仅 role+content，由前端维护） */
export interface ChatHistoryEntry {
  role: 'user' | 'assistant'
  content: string
}

/**
 * P1-4: 流式 Wiki 问答
 *
 * 用 fetch + ReadableStream 消费 SSE（EventSource 不支持 POST）。
 * SSE 帧解析由共享工具 streamSse 负责（P4-3 抽取）。
 *
 * P2-13b：options.history 传入多轮会话历史，后端注入 LLM messages 实现追问/指代。
 *
 * @returns AbortController（可调用 .abort() 取消）
 */
export function queryWikiStream(
  question: string,
  callbacks: WikiQueryStreamCallbacks,
  options: {
    recallLimit?: number
    expandBacklinks?: boolean
    history?: ChatHistoryEntry[]
  } = {},
): AbortController {
  const { recallLimit = 5, expandBacklinks = true, history } = options

  // wiki 依赖服务端显式 done 事件，不发送合成 done
  return streamSse(
    {
      url: `${getApiBaseUrl()}/llm-wiki/query/stream`,
      body: {
        question,
        recall_limit: recallLimit,
        expand_backlinks: expandBacklinks,
        history,
      },
      emitSyntheticDone: false,
    },
    (ev) => {
      switch (ev.type) {
        case 'meta':
          callbacks.onMeta(ev.data as any)
          break
        case 'delta':
          callbacks.onDelta((ev.data as any)?.text || '')
          break
        case 'done':
          callbacks.onDone(ev.data as any)
          break
        case 'error':
          callbacks.onError?.((ev.data as any)?.message || '流式查询出错')
          break
      }
    },
    callbacks.onError,
  )
}

// S7-7: Lint 健康检查
export interface LintIssue {
  /** P1-12b: 稳定标识 sha1(type|slug|message)[:16]，用于忽略/恢复 */
  issue_key: string
  type: string
  severity: string
  slug: string
  message: string
  detail?: Record<string, any>
}

export interface LintReport {
  pages_checked: number
  total_issues: number
  /** P1-12b: 已被忽略、从 total_issues 中扣除的数量 */
  ignored_count: number
  by_type: Record<string, number>
  by_severity: Record<string, number>
  issues: LintIssue[]
}

// P1-12b: 已忽略的 lint issue 条目
export interface LintIgnoreEntry {
  issue_key: string
  type: string
  slug: string
  message: string
  reason: string
  ignored_by: string
  created_at: string
}

export function runWikiLint(includeStale = true) {
  return api.post<unknown, LintReport>('/llm-wiki/lint', null, {
    params: { include_stale: includeStale },
  })
}

export function getLintSuggestions(limit = 20) {
  return api.get<unknown, { count: number; suggestions: any[] }>('/llm-wiki/lint/suggestions', {
    params: { limit },
  })
}

// P1-12b: 忽略 / 取消忽略 lint issue
export function ignoreLintIssue(
  issue_key: string,
  payload: { type: string; slug: string; message: string; reason?: string },
) {
  return api.post<unknown, { issue_key: string; ignored: boolean }>(
    '/llm-wiki/lint/ignore',
    { issue_key, ...payload },
  )
}

export function unignoreLintIssue(issue_key: string) {
  return api.delete<unknown, { issue_key: string; unignored: boolean }>(
    `/llm-wiki/lint/ignore/${issue_key}`,
  )
}

export function listIgnoredLintIssues() {
  return api.get<unknown, { count: number; items: LintIgnoreEntry[] }>(
    '/llm-wiki/lint/ignored',
  )
}

// S7-8: 漂移监控
export function recompileStale(pushReview = true) {
  return api.post<unknown, any>('/llm-wiki/recompile-stale', null, {
    params: { push_review: pushReview },
  })
}

// 单文档编译为 Wiki（全流水线：解析 → 抽取 → LLM 编译 wiki 页面）
export function recompileDocument(docId: string, force = true) {
  return api.post<unknown, any>(`/llm-wiki/recompile/${docId}`, null, {
    params: { force },
  })
}

export function checkDrift(docId: string) {
  return api.post<unknown, any>(`/llm-wiki/drift/check/${docId}`)
}

export function rebuildIndex() {
  return api.post<unknown, any>('/llm-wiki/index/rebuild')
}

// 管道追踪：获取文档编译的章节级 LLM 处理前后对比
export function getCompileTrace(docId: string, force = false) {
  return api.get<unknown, CompileTraceResponse>(`/llm-wiki/compile-trace/${docId}`, {
    params: { force },
  })
}
