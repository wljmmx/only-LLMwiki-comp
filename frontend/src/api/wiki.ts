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
