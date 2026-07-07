/**
 * useEditDraft composable 单元测试（S16-5 协作编辑冲突恢复）
 *
 * 覆盖：
 * 1. 纯函数 API：saveDraft / loadDraft / clearDraft / hasDraft
 * 2. slug 隔离：不同 slug 草稿互不干扰
 * 3. 字段校验：损坏数据返回 null
 * 4. composable：useEditDraft 响应式状态 + save / clear / reload / isConflictWith
 * 5. localStorage 异常静默处理
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import {
  saveDraft,
  loadDraft,
  clearDraft,
  hasDraft,
  useEditDraft,
} from './useEditDraft'

describe('composables/useEditDraft.ts — S16-5 编辑草稿持久化', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  // ────────── 1. 纯函数 API ──────────

  describe('纯函数 API', () => {
    it('hasDraft：无草稿时返回 false', () => {
      expect(hasDraft('nginx-502')).toBe(false)
    })

    it('saveDraft + hasDraft：保存后返回 true', () => {
      saveDraft('nginx-502', '# 内容', 1)
      expect(hasDraft('nginx-502')).toBe(true)
    })

    it('saveDraft + loadDraft：往返一致', () => {
      saveDraft('nginx-502', '# Hello World', 3, '修改背景章节')
      const draft = loadDraft('nginx-502')
      expect(draft).not.toBeNull()
      expect(draft!.content).toBe('# Hello World')
      expect(draft!.version).toBe(3)
      expect(draft!.summary).toBe('修改背景章节')
      expect(typeof draft!.savedAt).toBe('number')
      expect(draft!.savedAt).toBeLessThanOrEqual(Date.now())
    })

    it('loadDraft：无草稿时返回 null', () => {
      expect(loadDraft('nginx-502')).toBeNull()
    })

    it('clearDraft：清除后 hasDraft=false', () => {
      saveDraft('nginx-502', '# 内容', 1)
      expect(hasDraft('nginx-502')).toBe(true)
      clearDraft('nginx-502')
      expect(hasDraft('nginx-502')).toBe(false)
      expect(loadDraft('nginx-502')).toBeNull()
    })

    it('clearDraft：无草稿时不抛错', () => {
      expect(() => clearDraft('never-saved')).not.toThrow()
    })

    it('summary 可选：未传时 loadDraft 返回 undefined', () => {
      saveDraft('nginx-502', '# 内容', 1)
      const draft = loadDraft('nginx-502')
      expect(draft!.summary).toBeUndefined()
    })
  })

  // ────────── 2. slug 隔离 ──────────

  describe('slug 隔离', () => {
    it('不同 slug 草稿互不干扰', () => {
      saveDraft('nginx-502', '# A', 1)
      saveDraft('k8s-pod', '# B', 2)
      expect(loadDraft('nginx-502')!.content).toBe('# A')
      expect(loadDraft('k8s-pod')!.content).toBe('# B')
    })

    it('clearDraft 只清除指定 slug', () => {
      saveDraft('nginx-502', '# A', 1)
      saveDraft('k8s-pod', '# B', 2)
      clearDraft('nginx-502')
      expect(hasDraft('nginx-502')).toBe(false)
      expect(hasDraft('k8s-pod')).toBe(true)
    })

    it('特殊字符 slug 正常工作', () => {
      saveDraft('page-with-dash_and_underscore', '# X', 1)
      expect(hasDraft('page-with-dash_and_underscore')).toBe(true)
      expect(loadDraft('page-with-dash_and_underscore')!.content).toBe('# X')
    })
  })

  // ────────── 3. 字段校验（损坏数据防御） ──────────

  describe('损坏数据防御', () => {
    it('content 字段缺失 → loadDraft 返回 null', () => {
      localStorage.setItem('collab_draft:bad', JSON.stringify({ version: 1, savedAt: 1 }))
      expect(loadDraft('bad')).toBeNull()
    })

    it('version 字段缺失 → loadDraft 返回 null', () => {
      localStorage.setItem('collab_draft:bad', JSON.stringify({ content: 'x', savedAt: 1 }))
      expect(loadDraft('bad')).toBeNull()
    })

    it('version 类型错误（字符串）→ loadDraft 返回 null', () => {
      localStorage.setItem('collab_draft:bad', JSON.stringify({ content: 'x', version: '1' }))
      expect(loadDraft('bad')).toBeNull()
    })

    it('content 类型错误（数字）→ loadDraft 返回 null', () => {
      localStorage.setItem('collab_draft:bad', JSON.stringify({ content: 123, version: 1 }))
      expect(loadDraft('bad')).toBeNull()
    })

    it('非 JSON 数据 → loadDraft 返回 null', () => {
      localStorage.setItem('collab_draft:bad', 'not-json{')
      expect(loadDraft('bad')).toBeNull()
    })

    it('savedAt 缺失时用 Date.now() 兜底', () => {
      localStorage.setItem(
        'collab_draft:ok',
        JSON.stringify({ content: 'x', version: 1 }),
      )
      const before = Date.now()
      const draft = loadDraft('ok')
      const after = Date.now()
      expect(draft).not.toBeNull()
      expect(draft!.savedAt).toBeGreaterThanOrEqual(before)
      expect(draft!.savedAt).toBeLessThanOrEqual(after)
    })

    it('summary 类型错误（数字）→ 视为 undefined', () => {
      localStorage.setItem(
        'collab_draft:ok',
        JSON.stringify({ content: 'x', version: 1, summary: 123 }),
      )
      const draft = loadDraft('ok')
      expect(draft!.summary).toBeUndefined()
    })
  })

  // ────────── 4. composable useEditDraft ──────────

  describe('composable useEditDraft', () => {
    it('初始无草稿时 draft.value 为 null', () => {
      const { draft } = useEditDraft('nginx-502')
      expect(draft.value).toBeNull()
    })

    it('localStorage 已有草稿时初始化加载', () => {
      saveDraft('nginx-502', '# 已存在', 5, 'old summary')
      const { draft } = useEditDraft('nginx-502')
      expect(draft.value).not.toBeNull()
      expect(draft.value!.content).toBe('# 已存在')
      expect(draft.value!.version).toBe(5)
    })

    it('save：保存后 draft.value 更新', () => {
      const { draft, save } = useEditDraft('nginx-502')
      expect(draft.value).toBeNull()
      save('# 新内容', 2, 'new summary')
      expect(draft.value).not.toBeNull()
      expect(draft.value!.content).toBe('# 新内容')
      expect(draft.value!.version).toBe(2)
      expect(draft.value!.summary).toBe('new summary')
    })

    it('save 同时持久化到 localStorage', () => {
      const { save } = useEditDraft('nginx-502')
      save('# X', 1)
      expect(hasDraft('nginx-502')).toBe(true)
      expect(loadDraft('nginx-502')!.content).toBe('# X')
    })

    it('clear：清除后 draft.value 为 null', () => {
      saveDraft('nginx-502', '# X', 1)
      const { draft, clear } = useEditDraft('nginx-502')
      expect(draft.value).not.toBeNull()
      clear()
      expect(draft.value).toBeNull()
      expect(hasDraft('nginx-502')).toBe(false)
    })

    it('reload：从 localStorage 重新加载', () => {
      const { draft, reload } = useEditDraft('nginx-502')
      expect(draft.value).toBeNull()
      // 外部写入 localStorage
      saveDraft('nginx-502', '# 外部写入', 3)
      // composable 内部状态未变
      expect(draft.value).toBeNull()
      // reload 后同步
      reload()
      expect(draft.value).not.toBeNull()
      expect(draft.value!.content).toBe('# 外部写入')
    })

    it('has：返回是否存在草稿', () => {
      const { has, save, clear } = useEditDraft('nginx-502')
      expect(has()).toBe(false)
      save('# X', 1)
      expect(has()).toBe(true)
      clear()
      expect(has()).toBe(false)
    })

    it('isConflictWith：版本号一致时返回 false', () => {
      saveDraft('nginx-502', '# X', 5)
      const { isConflictWith } = useEditDraft('nginx-502')
      expect(isConflictWith(5)).toBe(false)
    })

    it('isConflictWith：版本号不一致时返回 true', () => {
      saveDraft('nginx-502', '# X', 3)
      const { isConflictWith } = useEditDraft('nginx-502')
      expect(isConflictWith(5)).toBe(true)
    })

    it('isConflictWith：无草稿时返回 false', () => {
      const { isConflictWith } = useEditDraft('nginx-502')
      expect(isConflictWith(5)).toBe(false)
    })

    it('不同 slug 的 composable 互不干扰', () => {
      const a = useEditDraft('page-a')
      const b = useEditDraft('page-b')
      a.save('# A', 1)
      expect(a.draft.value).not.toBeNull()
      expect(b.draft.value).toBeNull()
      b.save('# B', 2)
      expect(a.draft.value!.content).toBe('# A')
      expect(b.draft.value!.content).toBe('# B')
    })
  })

  // ────────── 5. localStorage 异常静默处理 ──────────

  describe('localStorage 异常静默处理', () => {
    it('saveDraft：localStorage.setItem 抛错时不抛出（仅 console.warn）', () => {
      const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new Error('QuotaExceededError')
      })
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
      expect(() => saveDraft('nginx-502', '# X', 1)).not.toThrow()
      expect(warnSpy).toHaveBeenCalled()
      spy.mockRestore()
      warnSpy.mockRestore()
    })

    it('loadDraft：localStorage.getItem 抛错时返回 null', () => {
      const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
        throw new Error('SecurityError')
      })
      expect(loadDraft('nginx-502')).toBeNull()
      spy.mockRestore()
    })

    it('hasDraft：localStorage.getItem 抛错时返回 false', () => {
      const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
        throw new Error('SecurityError')
      })
      expect(hasDraft('nginx-502')).toBe(false)
      spy.mockRestore()
    })

    it('clearDraft：localStorage.removeItem 抛错时不抛出', () => {
      const spy = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
        throw new Error('SecurityError')
      })
      expect(() => clearDraft('nginx-502')).not.toThrow()
      spy.mockRestore()
    })
  })
})
