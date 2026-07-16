/**
 * P1-1: 集中式图标注册表
 *
 * 统一管理所有图标，替换散落的 emoji。语义命名 → @vicons/ionicons5 组件映射。
 * - 菜单/模板用 renderMenuIcon(component)
 * - 模板用 <AppIcon name="dashboard" />
 *
 * 新增图标：在此 import 并加入 iconRegistry，无需在各组件分散引入。
 */
import { h, type Component } from 'vue'
import { NIcon } from 'naive-ui'
import {
  DocumentTextOutline,
  SearchOutline,
  BookOutline,
  ChatbubblesOutline,
  GitNetworkOutline,
  HeartCircleOutline,
  CheckmarkDoneOutline,
  TimeOutline,
  AlertCircleOutline,
  SwapHorizontalOutline,
  GitBranchOutline,
  BuildOutline,
  CloudDownloadOutline,
  PeopleOutline,
  SettingsOutline,
  CubeOutline,
  SpeedometerOutline,
  NotificationsOutline,
  AnalyticsOutline,
  // 通用操作图标
  AddOutline,
  RefreshOutline,
  TrashOutline,
  PencilOutline,
  EyeOutline,
  DownloadOutline,
  ChevronForwardOutline,
  SunnyOutline,
  MoonOutline,
  MenuOutline,
  PersonCircleOutline,
  BookmarksOutline,
  AlertOutline,
  LinkOutline,
  StopCircleOutline,
} from '@vicons/ionicons5'

/** 语义图标名 → vicons 组件 */
export const iconRegistry = {
  // 导航/菜单
  dashboard: SpeedometerOutline,
  documents: DocumentTextOutline,
  search: SearchOutline,
  wiki: BookOutline,
  'wiki-query': ChatbubblesOutline,
  graph: GitNetworkOutline,
  health: HeartCircleOutline,
  pipeline: AnalyticsOutline,
  review: CheckmarkDoneOutline,
  versions: TimeOutline,
  incidents: AlertCircleOutline,
  changes: SwapHorizontalOutline,
  topology: GitBranchOutline,
  runbook: BuildOutline,
  templates: BookmarksOutline,
  export: CloudDownloadOutline,
  mcp: CubeOutline,
  users: PeopleOutline,
  system: SettingsOutline,
  // 操作
  add: AddOutline,
  refresh: RefreshOutline,
  trash: TrashOutline,
  edit: PencilOutline,
  eye: EyeOutline,
  download: DownloadOutline,
  chevron: ChevronForwardOutline,
  // 状态/反馈
  notify: NotificationsOutline,
  alert: AlertOutline,
  link: LinkOutline,
  stop: StopCircleOutline,
  // 主题/布局
  sunny: SunnyOutline,
  moon: MoonOutline,
  menu: MenuOutline,
  user: PersonCircleOutline,
} as const satisfies Record<string, Component>

export type IconName = keyof typeof iconRegistry

/** 渲染单个语义图标组件（用于模板内 NIcon 插槽） */
export function getIconComponent(name: IconName): Component {
  return iconRegistry[name]
}

/**
 * Naive UI MenuOption.icon 所需的渲染函数工厂
 * @example { label: '仪表盘', key: '/dashboard', icon: renderMenuIcon('dashboard') }
 */
export function renderMenuIcon(name: IconName, size = 18) {
  const Comp = iconRegistry[name]
  return () => h(NIcon, { size }, { default: () => h(Comp) })
}
