import { config } from '@vue/test-utils'

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
}

// 副作用导入，导出为空
export {}
