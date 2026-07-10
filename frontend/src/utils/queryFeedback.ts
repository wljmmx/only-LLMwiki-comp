/**
 * P2-13a: Wiki 问答反馈机制（纯前端）
 *
 * 后端无反馈端点，反馈仅在前端按"问题指纹"持久化到 localStorage。
 * 指纹 = question + 首个 cited_slug（或 recalled slug）的轻量 hash，
 * 让同一问题+来源的反馈稳定，避免简单 trim 差异导致无法召回。
 *
 * 数据结构（localStorage key: "opskg:wiki:feedback"）：
 *   { [fingerprint]: { rating: 'up' | 'down', ts: number } }
 *
 * 容量控制：保留最近 200 条，超出按 ts 升序裁剪。
 */
const STORAGE_KEY = 'opskg:wiki:feedback'
const MAX_ENTRIES = 200

export type FeedbackRating = 'up' | 'down'

export interface FeedbackEntry {
  rating: FeedbackRating
  ts: number
}

export interface FeedbackStore {
  [fingerprint: string]: FeedbackEntry
}

/** 简单稳定的字符串 hash（FNV-1a 32bit → hex） */
function hashStr(s: string): string {
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}

/** 计算问答指纹：question 归一化 + 首个 cited slug */
export function computeFeedbackFingerprint(
  question: string,
  citedSlugs: string[] = [],
): string {
  const q = question.trim().toLowerCase()
  const firstSlug = citedSlugs[0] ?? ''
  return hashStr(`${q}|${firstSlug}`)
}

function readStore(): FeedbackStore {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function writeStore(store: FeedbackStore): void {
  try {
    const keys = Object.keys(store)
    if (keys.length > MAX_ENTRIES) {
      // 按 ts 升序，删除最旧的
      const sorted = keys.sort((a, b) => (store[a].ts || 0) - (store[b].ts || 0))
      const toRemove = sorted.slice(0, keys.length - MAX_ENTRIES)
      for (const k of toRemove) delete store[k]
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store))
  } catch {
    // localStorage 不可用（隐私模式等）静默降级
  }
}

/** 读取某问答的反馈 rating，未反馈返回 null */
export function getFeedback(fingerprint: string): FeedbackRating | null {
  const store = readStore()
  return store[fingerprint]?.rating ?? null
}

/** 设置/更新反馈（幂等：重复设同值覆盖 ts） */
export function setFeedback(fingerprint: string, rating: FeedbackRating): void {
  const store = readStore()
  store[fingerprint] = { rating, ts: Date.now() }
  writeStore(store)
}

/** 清除某问答的反馈 */
export function clearFeedback(fingerprint: string): void {
  const store = readStore()
  if (store[fingerprint]) {
    delete store[fingerprint]
    writeStore(store)
  }
}

/** 测试辅助：清空全部反馈存储 */
export function _clearAllForTest(): void {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
}
