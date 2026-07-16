/** 全局 vitest setup — 在测试文件加载之前执行 */

// 在 jsdom 环境中提供 window.matchMedia mock
// Naive UI 的依赖 vueuc 在模块加载时访问 window.matchMedia
if (typeof globalThis !== 'undefined') {
  const win = globalThis as any
  if (!win.matchMedia) {
    win.matchMedia = (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })
  }
}