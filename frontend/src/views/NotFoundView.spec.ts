import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const mockRouter = {
  replace: vi.fn(),
  back: vi.fn(),
}
const mockRoute = {
  fullPath: '/nonexistent/path',
}

vi.mock('vue-router', () => ({
  useRouter: () => mockRouter,
  useRoute: () => mockRoute,
}))

import NotFoundView from '@/views/NotFoundView.vue'
import '@/test/setup'

describe('NotFoundView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    mockRoute.fullPath = '/nonexistent/path'
    Object.defineProperty(window.history, 'length', {
      value: 1,
      configurable: true,
      writable: true,
    })
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView() {
    return mount(NotFoundView, {
      global: { plugins: [pinia] },
    })
  }

  it('组件能正确 mount 并显示 404 标题', () => {
    const wrapper = mountView()
    expect(wrapper.html()).toContain('404')
    expect(wrapper.html()).toContain('页面不存在')
  })

  it('attemptedPath 反映当前 route.fullPath', () => {
    mockRoute.fullPath = '/some/missing/page'
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.attemptedPath).toBe('/some/missing/page')
  })

  it('渲染提示路径未匹配的文案', () => {
    const wrapper = mountView()
    expect(wrapper.html()).toContain('路径未匹配')
  })

  it('渲染返回首页与返回上一页按钮', () => {
    const wrapper = mountView()
    expect(wrapper.html()).toContain('返回首页')
    expect(wrapper.html()).toContain('返回上一页')
  })

  it('goHome 调用 router.replace 跳转到 /dashboard', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.goHome()
    expect(mockRouter.replace).toHaveBeenCalledWith('/dashboard')
  })

  it('goBack 在有历史记录时调用 router.back', () => {
    Object.defineProperty(window.history, 'length', {
      value: 2,
      configurable: true,
      writable: true,
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.goBack()
    expect(mockRouter.back).toHaveBeenCalled()
    expect(mockRouter.replace).not.toHaveBeenCalled()
  })

  it('goBack 在无历史记录时调用 router.replace 跳转首页', () => {
    Object.defineProperty(window.history, 'length', {
      value: 1,
      configurable: true,
      writable: true,
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.goBack()
    expect(mockRouter.replace).toHaveBeenCalledWith('/dashboard')
    expect(mockRouter.back).not.toHaveBeenCalled()
  })
})
