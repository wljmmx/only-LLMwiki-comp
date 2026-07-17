import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

const STORAGE_KEY_DARK = 'opskg:darkMode'
const STORAGE_KEY_SIDEBAR = 'opskg:sidebarCollapsed'

/** 读取 localStorage 布尔值（带默认） */
function readBool(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key)
    if (v === null) return fallback
    return v === 'true'
  } catch {
    return fallback
  }
}

/** 读取系统主题偏好 */
function readOsDark(): boolean {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch {
    return false
  }
}

export const useAppStore = defineStore('app', () => {
  // P0-2: 持久化 + 跟随系统
  // 首次访问无偏好时跟随系统主题
  const hasUserPreference = (() => {
    try {
      return localStorage.getItem(STORAGE_KEY_DARK) !== null
    } catch {
      return false
    }
  })()

  const sidebarCollapsed = ref(readBool(STORAGE_KEY_SIDEBAR, false))
  const darkMode = ref(hasUserPreference ? readBool(STORAGE_KEY_DARK, false) : readOsDark())

  // P0-3: 响应式断点状态
  /** 当前屏宽类型：mobile(<768) | tablet(768-1023) | desktop(>=1024) */
  const viewport = ref<'mobile' | 'tablet' | 'desktop'>('desktop')

  /** 根据屏宽自动调整侧栏模式 */
  function updateViewport() {
    const w = window.innerWidth
    if (w < 768) {
      viewport.value = 'mobile'
      // 移动端侧栏默认收起（drawer 模式由 AppLayout 控制）
      if (!sidebarCollapsed.value) sidebarCollapsed.value = true
    } else if (w < 1024) {
      viewport.value = 'tablet'
      // 平板默认折叠
      if (!sidebarCollapsed.value) sidebarCollapsed.value = true
    } else {
      viewport.value = 'desktop'
      // 桌面端自动展开侧栏，确保菜单可见
      if (sidebarCollapsed.value) sidebarCollapsed.value = false
    }
  }

  /** 移动端 drawer 是否打开（独立于 sidebarCollapsed） */
  const mobileDrawerOpen = ref(false)

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  function toggleDarkMode() {
    darkMode.value = !darkMode.value
  }

  function openMobileDrawer() {
    mobileDrawerOpen.value = true
  }

  function closeMobileDrawer() {
    mobileDrawerOpen.value = false
  }

  // P0-2: 应用 data-theme 到 <html> 并持久化
  watch(
    darkMode,
    (isDark) => {
      const html = document.documentElement
      if (isDark) {
        html.setAttribute('data-theme', 'dark')
      } else {
        html.removeAttribute('data-theme')
      }
      try {
        localStorage.setItem(STORAGE_KEY_DARK, String(isDark))
      } catch {
        /* ignore */
      }
    },
    { immediate: true }
  )

  watch(sidebarCollapsed, (v) => {
    try {
      localStorage.setItem(STORAGE_KEY_SIDEBAR, String(v))
    } catch {
      /* ignore */
    }
  })

  // 监听系统主题变化（仅当用户无显式偏好时）
  function bindOsTheme() {
    try {
      const mq = window.matchMedia('(prefers-color-scheme: dark)')
      mq.addEventListener('change', (e) => {
        try {
          if (localStorage.getItem(STORAGE_KEY_DARK) === null) {
            darkMode.value = e.matches
          }
        } catch {
          darkMode.value = e.matches
        }
      })
    } catch {
      /* ignore */
    }
  }

  return {
    sidebarCollapsed,
    darkMode,
    viewport,
    mobileDrawerOpen,
    toggleSidebar,
    toggleDarkMode,
    openMobileDrawer,
    closeMobileDrawer,
    updateViewport,
    bindOsTheme,
  }
})
