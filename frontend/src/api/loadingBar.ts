/**
 * LoadingBar 单例管理（S14-2 全局 loading bar）
 *
 * 提供 axios 拦截器在非组件上下文中调用 loading bar 的能力。
 *
 * 使用方式：
 * - LoadingBarBridge 组件在 onMounted 时调用 setLoadingBar(bar)
 * - axios 拦截器通过 startLoadingBar() / finishLoadingBar() / errorLoadingBar() 控制
 *
 * 防御性：
 * - 若 loadingBar 未初始化（如单元测试、SSR），所有操作静默 no-op
 * - 通过计数器支持并发请求（多个请求同时进行时只显示一条进度条）
 */
import type { LoadingBarApi } from 'naive-ui'

let loadingBar: LoadingBarApi | null = null
let activeCount = 0

export function setLoadingBar(bar: LoadingBarApi): void {
  loadingBar = bar
}

export function clearLoadingBar(): void {
  loadingBar = null
  activeCount = 0
}

/**
 * 启动 loading bar（并发请求时仅第一个启动）
 */
export function startLoadingBar(): void {
  if (!loadingBar) return
  activeCount += 1
  if (activeCount === 1) {
    loadingBar.start()
  }
}

/**
 * 完成 loading bar（并发请求时仅最后一个完成）
 *
 * 注意：若 activeCount 已为 0（如未启动或已结束），调用本函数为 no-op，
 * 不会重复触发 bar.finish()。
 */
export function finishLoadingBar(): void {
  if (!loadingBar) return
  if (activeCount === 0) return // 已结束，no-op（避免重复 finish）
  activeCount -= 1
  if (activeCount === 0) {
    loadingBar.finish()
  }
}

/**
 * 错误 loading bar（任意请求失败时立即标记错误并重置计数）
 */
export function errorLoadingBar(): void {
  if (!loadingBar) return
  loadingBar.error()
  activeCount = 0
}

/** 测试辅助：获取当前活跃请求数（仅用于单元测试断言） */
export function _getActiveCountForTest(): number {
  return activeCount
}

/** 测试辅助：重置状态（仅用于单元测试隔离） */
export function _resetForTest(): void {
  loadingBar = null
  activeCount = 0
}
