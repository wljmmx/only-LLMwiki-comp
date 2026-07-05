import api from './index'
import type { WikiPage, WikiIndex, BacklinkItem } from '@/types/api'

export function getWikiIndex() {
  return api.get<any, WikiIndex>('/llm-wiki/index')
}

export function listWikiPages() {
  return api.get<any, { pages: WikiPage[]; total: number }>('/llm-wiki/pages')
}

export function getWikiPage(slug: string) {
  return api.get<any, WikiPage>(`/llm-wiki/page/${slug}`)
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

export function checkDrift(docId: string) {
  return api.post<any, any>(`/llm-wiki/drift/check/${docId}`)
}

export function rebuildIndex() {
  return api.post<any, any>('/llm-wiki/index/rebuild')
}
