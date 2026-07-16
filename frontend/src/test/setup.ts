import { config } from '@vue/test-utils'

// Naive UI 依赖 vueuc/vooks 在模块加载时访问 window.matchMedia
// 必须在 importOriginal() 之前 mock，因此放在 setupFiles 中
if (typeof window !== 'undefined') {
  if (typeof window.matchMedia !== 'function') {
    // eslint-disable-next-line no-console
    console.log('[setup] mocking window.matchMedia')
    window.matchMedia = (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as any
  }
}

// 测试环境默认 stub naive-ui 组件，避免在 jsdom 中进行真实渲染。
// renderStubDefaultSlot 让 stub 仍渲染默认插槽内容，便于文本断言。
config.global.renderStubDefaultSlot = true

config.global.stubs = {
  NCard: true,
  NSpace: true,
  NAlert: true,
  NCode: true,
  NInput: true,
  NSelect: true,
  NTag: true,
  NDataTable: true,
  NModal: true,
  NSpin: true,
  NIcon: true,
  NPopover: true,
  NTooltip: true,
  NPopconfirm: true,
  NDivider: true,
  NEmpty: true,
  NPagination: true,
  NForm: true,
  NFormItem: true,
  NTabs: true,
  NTabPane: true,
  NRadio: true,
  NRadioGroup: true,
  NCheckbox: true,
  NCheckboxGroup: true,
  NSwitch: true,
  NDrawer: true,
  NDropdown: true,
  NMenu: true,
  NBreadcrumb: true,
  NBreadcrumbItem: true,
  NCollapse: true,
  NCollapseItem: true,
  NStatistic: true,
  NProgress: true,
  NLayout: true,
  NLayoutSider: true,
  NLayoutContent: true,
  NLayoutHeader: true,
  NGrid: true,
  NGridItem: true,
  NAvatar: true,
  NBadge: true,
  NBackTop: true,
  NButton: true,
  NText: true,
}

// 副作用导入，导出为空
export {}
