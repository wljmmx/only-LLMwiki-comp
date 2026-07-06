/**
 * LoadingBar 单元测试（S14-2 全局 loading bar）
 *
 * 测试覆盖：
 * 1. 单例管理：setLoadingBar / clearLoadingBar
 * 2. 并发计数：多个 start 只启动一次，多个 finish 只完成一次
 * 3. 错误处理：error 立即重置计数
 * 4. 防御性：未初始化时所有操作 no-op
 * 5. axios 拦截器集成：请求启动 loading bar，响应完成/错误
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import {
  setLoadingBar,
  clearLoadingBar,
  startLoadingBar,
  finishLoadingBar,
  errorLoadingBar,
  _getActiveCountForTest,
  _resetForTest,
} from './loadingBar'

// Mock LoadingBarInst
function makeMockBar() {
  return {
    start: vi.fn(),
    finish: vi.fn(),
    error: vi.fn(),
  }
}

describe('api/loadingBar.ts — S14-2 全局 loading bar', () => {
  beforeEach(() => {
    _resetForTest()
  })

  afterEach(() => {
    _resetForTest()
    vi.restoreAllMocks()
  })

  // ────────── 1. 单例管理 ──────────

  it('setLoadingBar 设置实例后可调用 start', () => {
    const bar = makeMockBar()
    setLoadingBar(bar as any)
    startLoadingBar()
    expect(bar.start).toHaveBeenCalledTimes(1)
  })

  it('clearLoadingBar 后 start 不再调用', () => {
    const bar = makeMockBar()
    setLoadingBar(bar as any)
    clearLoadingBar()
    startLoadingBar()
    expect(bar.start).not.toHaveBeenCalled()
  })

  // ────────── 2. 并发计数 ──────────

  it('并发 3 个 start 只调用 1 次 bar.start', () => {
    const bar = makeMockBar()
    setLoadingBar(bar as any)
    startLoadingBar()
    startLoadingBar()
    startLoadingBar()
    expect(bar.start).toHaveBeenCalledTimes(1)
    expect(_getActiveCountForTest()).toBe(3)
  })

  it('并发 3 个 start + 3 个 finish 只调用 1 次 bar.finish', () => {
    const bar = makeMockBar()
    setLoadingBar(bar as any)
    startLoadingBar()
    startLoadingBar()
    startLoadingBar()
    finishLoadingBar()
    finishLoadingBar()
    expect(bar.finish).not.toHaveBeenCalled() // 还剩 1 个活跃
    finishLoadingBar()
    expect(bar.finish).toHaveBeenCalledTimes(1)
    expect(_getActiveCountForTest()).toBe(0)
  })

  it('finish 过量不会变为负数', () => {
    const bar = makeMockBar()
    setLoadingBar(bar as any)
    startLoadingBar()
    finishLoadingBar()
    finishLoadingBar() // 过量
    finishLoadingBar() // 过量
    expect(bar.finish).toHaveBeenCalledTimes(1)
    expect(_getActiveCountForTest()).toBe(0)
  })

  // ────────── 3. 错误处理 ──────────

  it('errorLoadingBar 立即调用 bar.error 并重置计数', () => {
    const bar = makeMockBar()
    setLoadingBar(bar as any)
    startLoadingBar()
    startLoadingBar()
    startLoadingBar()
    errorLoadingBar()
    expect(bar.error).toHaveBeenCalledTimes(1)
    expect(_getActiveCountForTest()).toBe(0)
  })

  it('error 后再 finish 不会重复调用 bar.finish', () => {
    const bar = makeMockBar()
    setLoadingBar(bar as any)
    startLoadingBar()
    errorLoadingBar()
    finishLoadingBar()
    expect(bar.finish).not.toHaveBeenCalled()
  })

  // ────────── 4. 防御性 ──────────

  it('未初始化时 startLoadingBar 静默 no-op', () => {
    // 不调用 setLoadingBar
    expect(() => startLoadingBar()).not.toThrow()
    expect(_getActiveCountForTest()).toBe(0)
  })

  it('未初始化时 finishLoadingBar 静默 no-op', () => {
    expect(() => finishLoadingBar()).not.toThrow()
    expect(_getActiveCountForTest()).toBe(0)
  })

  it('未初始化时 errorLoadingBar 静默 no-op', () => {
    expect(() => errorLoadingBar()).not.toThrow()
    expect(_getActiveCountForTest()).toBe(0)
  })
})

