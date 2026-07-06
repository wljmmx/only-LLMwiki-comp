import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAppStore } from './app'

describe('stores/app.ts', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('初始 state 具有默认值', () => {
    const store = useAppStore()
    expect(store.sidebarCollapsed).toBe(false)
    expect(store.darkMode).toBe(false)
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

  it('每个测试 store 状态相互独立（重新创建 pinia 后重置）', () => {
    const store = useAppStore()
    store.toggleDarkMode()
    store.toggleSidebar()
    expect(store.darkMode).toBe(true)
    expect(store.sidebarCollapsed).toBe(true)

    // 模拟新的测试会话
    setActivePinia(createPinia())
    const fresh = useAppStore()
    expect(fresh.darkMode).toBe(false)
    expect(fresh.sidebarCollapsed).toBe(false)
  })
})
