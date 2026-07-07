/**
 * 编辑草稿持久化 composable（S16-5 协作编辑冲突恢复）
 *
 * 用途：在 WikiEditor 中持久化用户未保存的编辑内容到 localStorage，
 * 当页面刷新 / 断线重连后可恢复草稿，或在服务器版本变化时提示冲突。
 *
 * localStorage key 格式：collab_draft:{slug}
 * 值为 JSON：{ content, version, savedAt, summary? }
 *
 * 用法（纯函数，便于测试隔离）：
 *   import { saveDraft, loadDraft, clearDraft, hasDraft } from '@/composables/useEditDraft'
 *   saveDraft('nginx-502', '# 编辑内容', 3, '修改背景章节')
 *   const draft = loadDraft('nginx-502')   // EditDraft | null
 *   if (draft) { ... }
 *   clearDraft('nginx-502')
 *
 * 用法（composable，便于组件集成响应式状态）：
 *   const { draft, save, clear, reload, isConflictWith } = useEditDraft('nginx-502')
 *   draft.value        // EditDraft | null
 *   save(content, ver) // 持久化 + 刷新 ref
 *   clear()            // 清除 + 重置 ref
 *   isConflictWith(5)  // 草稿版本号是否与服务器 5 冲突
 */
import { ref } from 'vue'

/** 草稿数据结构 */
export interface EditDraft {
  /** 草稿内容（Markdown 全文） */
  content: string
  /** 保存草稿时的服务器版本号（用于冲突检测） */
  version: number
  /** 草稿保存时间（毫秒时间戳） */
  savedAt: number
  /** 变更摘要（可选） */
  summary?: string
}

const DRAFT_PREFIX = 'collab_draft:'

function draftKey(slug: string): string {
  return `${DRAFT_PREFIX}${slug}`
}

// ────────── 纯函数 API（无副作用，便于测试） ──────────

/**
 * 是否存在指定 slug 的草稿
 */
export function hasDraft(slug: string): boolean {
  try {
    return localStorage.getItem(draftKey(slug)) !== null
  } catch {
    return false
  }
}

/**
 * 加载草稿
 * @returns 草稿数据；不存在或解析失败时返回 null
 */
export function loadDraft(slug: string): EditDraft | null {
  try {
    const raw = localStorage.getItem(draftKey(slug))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<EditDraft>
    // 基本字段校验（防御损坏数据）
    if (typeof parsed.content !== 'string' || typeof parsed.version !== 'number') {
      return null
    }
    return {
      content: parsed.content,
      version: parsed.version,
      savedAt: typeof parsed.savedAt === 'number' ? parsed.savedAt : Date.now(),
      summary: typeof parsed.summary === 'string' ? parsed.summary : undefined,
    }
  } catch {
    return null
  }
}

/**
 * 保存草稿
 */
export function saveDraft(
  slug: string,
  content: string,
  version: number,
  summary?: string,
): void {
  try {
    const draft: EditDraft = {
      content,
      version,
      savedAt: Date.now(),
      summary,
    }
    localStorage.setItem(draftKey(slug), JSON.stringify(draft))
  } catch (e) {
    // localStorage 满 / 被禁用时静默失败（不阻塞编辑流程）
    console.warn('[useEditDraft] saveDraft 失败:', e)
  }
}

/**
 * 清除草稿
 */
export function clearDraft(slug: string): void {
  try {
    localStorage.removeItem(draftKey(slug))
  } catch {
    // 忽略
  }
}

// ────────── composable（响应式包装，便于组件集成） ──────────

/**
 * 编辑草稿 composable
 *
 * 提供 reactive 的 draft 状态与 save / clear / reload 操作。
 * 主要用于 WikiEditor 组件挂载时检测草稿、用户交互恢复 / 丢弃。
 *
 * @param slug 页面 slug（草稿按 slug 隔离）
 */
export function useEditDraft(slug: string) {
  const draft = ref<EditDraft | null>(loadDraft(slug))

  /** 重新从 localStorage 加载草稿到 ref */
  function reload() {
    draft.value = loadDraft(slug)
  }

  /** 保存草稿并刷新 ref */
  function save(content: string, version: number, summary?: string) {
    saveDraft(slug, content, version, summary)
    reload()
  }

  /** 清除草稿并重置 ref */
  function clear() {
    clearDraft(slug)
    draft.value = null
  }

  /** 草稿版本号与当前服务器版本号是否冲突（不一致=冲突） */
  function isConflictWith(serverVersion: number): boolean {
    return draft.value !== null && draft.value.version !== serverVersion
  }

  return {
    /** 当前草稿（响应式，null 表示无草稿） */
    draft,
    /** 重新从 localStorage 加载草稿到 ref */
    reload,
    /** 保存草稿并刷新 ref */
    save,
    /** 清除草稿并重置 ref */
    clear,
    /** 是否存在草稿 */
    has: () => draft.value !== null,
    /** 草稿版本号是否与服务器冲突 */
    isConflictWith,
  }
}
