/**
 * P2-6: 最近访问 Wiki 页面追踪
 *
 * 使用 localStorage 持久化最近访问的 Wiki 页面（最多 10 条），
 * 在侧边栏「最近访问」区域展示，支持快速跳转。
 */
import { ref } from 'vue'

const STORAGE_KEY = 'opskg:recentPages'
const MAX_ITEMS = 10

export interface RecentPage {
  slug: string
  title: string
  type: string
  accessedAt: number
}

function loadRecent(): RecentPage[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed as RecentPage[]
  } catch {
    return []
  }
}

function saveRecent(pages: RecentPage[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(pages))
  } catch { /* ignore */ }
}

const recentPages = ref<RecentPage[]>(loadRecent())

/** 记录一次页面访问（去重 + 时间戳更新 + 上限裁剪） */
export function useRecentPages() {
  function trackPage(slug: string, title: string, type: string) {
    const pages = [...recentPages.value]
    // 移除旧条目（去重）
    const idx = pages.findIndex((p) => p.slug === slug)
    if (idx >= 0) pages.splice(idx, 1)
    // 插入到头部
    pages.unshift({ slug, title, type, accessedAt: Date.now() })
    // 裁剪到上限
    if (pages.length > MAX_ITEMS) pages.length = MAX_ITEMS
    recentPages.value = pages
    saveRecent(pages)
  }

  return { recentPages, trackPage }
}