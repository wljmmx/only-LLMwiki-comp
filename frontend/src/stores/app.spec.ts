import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { nextTick } from 'vue'
import { useAppStore } from './app'

describe('stores/app.ts', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    // 清空 localStorage，确保每个测试独立
    localStorage.clear()
    // mock matchMedia 返回浅色（确保默认 darkMode=false）
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }))
    )
  })

  it('初始 state 具有默认值（无偏好时跟随系统浅色）', () => {
    const store = useAppStore()
    expect(store.sidebarCollapsed).toBe(false)
    expect(store.darkMode).toBe(false) // matchMedia mocked 返回 false
  })

  it('toggleSidebar 切换侧边栏折叠状态', () => {
    const store = useAppStore()
    expect(store.sidebarCollapsed).toBe(false)
    store.toggleSidebar()
    expect(store.sidebarCollapsed).toBe(true)
    store.toggleSidebar()
    expect(store.sidebarCollapsed).toBe(false)
  })

  it('toggleDarkMode 切换暗黑模式', () => {
    const store = useAppStore()
    expect(store.darkMode).toBe(false)
    store.toggleDarkMode()
    expect(store.darkMode).toBe(true)
    store.toggleDarkMode()
    expect(store.darkMode).toBe(false)
  })

  it('暴露 toggleSidebar 与 toggleDarkMode 两个 action', () => {
    const store = useAppStore()
    expect(typeof store.toggleSidebar).toBe('function')
    expect(typeof store.toggleDarkMode).toBe('function')
  })

  it('两个状态互不影响', () => {
    const store = useAppStore()
    store.toggleDarkMode()
    expect(store.darkMode).toBe(true)
    expect(store.sidebarCollapsed).toBe(false)

    store.toggleSidebar()
    expect(store.sidebarCollapsed).toBe(true)
    expect(store.darkMode).toBe(true)
  })

  it('每个测试 store 状态相互独立（重新创建 pinia 后重置）', async () => {
    const store = useAppStore()
    store.toggleDarkMode()
    store.toggleSidebar()
    expect(store.darkMode).toBe(true)
    expect(store.sidebarCollapsed).toBe(true)
    await nextTick()

    // 模拟新的测试会话：清空持久化状态
    localStorage.clear()
    setActivePinia(createPinia())
    const fresh = useAppStore()
    expect(fresh.darkMode).toBe(false)
    expect(fresh.sidebarCollapsed).toBe(false)
  })

  // P0-2: 持久化与主题应用
  it('toggleDarkMode 持久化到 localStorage', async () => {
    const store = useAppStore()
    store.toggleDarkMode()
    await nextTick()
    expect(localStorage.getItem('opskg:darkMode')).toBe('true')
    store.toggleDarkMode()
    await nextTick()
    expect(localStorage.getItem('opskg:darkMode')).toBe('false')
  })

  it('toggleSidebar 持久化到 localStorage', async () => {
    const store = useAppStore()
    store.toggleSidebar()
    await nextTick()
    expect(localStorage.getItem('opskg:sidebarCollapsed')).toBe('true')
  })

  it('darkMode=true 时设置 html data-theme=dark', async () => {
    const store = useAppStore()
    store.toggleDarkMode()
    await nextTick()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    store.toggleDarkMode()
    await nextTick()
    expect(document.documentElement.getAttribute('data-theme')).toBeNull()
  })

  // P0-3: 响应式 viewport
  it('暴露 viewport 和响应式方法', () => {
    const store = useAppStore()
    expect(typeof store.viewport).toBe('string')
    expect(typeof store.updateViewport).toBe('function')
    expect(typeof store.openMobileDrawer).toBe('function')
    expect(typeof store.closeMobileDrawer).toBe('function')
  })

  it('updateViewport 在桌面宽度保持 desktop', () => {
    const store = useAppStore()
    // mock window.innerWidth
    Object.defineProperty(window, 'innerWidth', {
      writable: true,
      configurable: true,
      value: 1280,
    })
    store.updateViewport()
    expect(store.viewport).toBe('desktop')
  })
})
