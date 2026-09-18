/**
 * LoadingBarBridge 单元测试
 *
 * 测试覆盖：
 * 1. 挂载时通过 setLoadingBar 注册 naive-ui loadingBar 实例
 * 2. 注册后 startLoadingBar 可正常驱动进度条
 * 3. 卸载时 clearLoadingBar，后续操作静默 no-op
 * 4. 组件不渲染任何 DOM
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { _resetForTest, startLoadingBar } from '@/api/loadingBar'

const mockBar = {
  start: vi.fn(),
  finish: vi.fn(),
  error: vi.fn(),
}

// 仅 mock useLoadingBar，保留其余 naive-ui 导出
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return { ...actual, useLoadingBar: () => mockBar }
})

import LoadingBarBridge from './LoadingBarBridge'

describe('components/common/LoadingBarBridge.vue', () => {
  beforeEach(() => {
    _resetForTest()
    vi.clearAllMocks()
  })

  it('挂载时注册 loadingBar 实例并驱动进度条', () => {
    const wrapper = mount(LoadingBarBridge)
    startLoadingBar()
    startLoadingBar()
    // 仅注册成功时才会调用真实 bar.start
    expect(mockBar.start).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('卸载时清空 loadingBar，后续操作静默 no-op', () => {
    const wrapper = mount(LoadingBarBridge)
    wrapper.unmount()
    expect(() => startLoadingBar()).not.toThrow()
    expect(mockBar.start).not.toHaveBeenCalled()
  })

  it('不渲染任何 DOM 内容', () => {
    const wrapper = mount(LoadingBarBridge)
    expect(wrapper.html()).toBe('')
    wrapper.unmount()
  })
})