/**
 * P1-5: 菜单单一事实源
 *
 * 从 router 路由记录的 meta 生成 Naive UI 菜单选项，废弃 AppSidebar 中硬编码的
 * path/emoji。路由 meta 即唯一事实源：新增路由只需补 menuGroup/menuOrder 即可
 * 自动出现在侧栏，无需同步改 sidebar。
 *
 * 规则（见 router/index.ts RouteMeta 注释）：
 * - menuOrder === undefined → 不进侧栏
 * - menuGroup 缺省 + menuOrder 定义 → 顶层独立项
 * - menuGroup 定义 → 折叠分组的子项
 * - 角色过滤：authRequired === true 时按 requireRole 过滤（与路由守卫一致）；
 *   dev 模式（authRequired === false）或后端不可达（null）放行。
 *
 * 输入接受 router.getRoutes() 返回的扁平记录（绝对路径），避免与 router/index
 * 产生循环依赖（AppLayout → AppSidebar → menuBuilder 链不再 import router/index）。
 */
import type { RouteMeta } from 'vue-router'
import type { MenuOption } from 'naive-ui'
import { renderMenuIcon, type IconName } from '@/utils/icons'
import { hasRequiredRole } from '@/utils/role'

/** 侧栏分组顺序（顶层独立项优先，随后按此数组顺序） */
const MENU_GROUP_ORDER = ['知识管理', '质量治理', 'AIOps', '系统工具'] as const

export interface MenuBuildContext {
  /** 后端是否要求认证（dev 模式 false，后端不可达 null） */
  authRequired: boolean | null
  /** 当前用户角色 */
  userRole?: string
}

/** 输入记录的最小契约（兼容 RouteRecordNormalized / RouteRecordRaw） */
export interface MenuRouteRecord {
  path: string
  meta?: RouteMeta
}

/**
 * 从路由记录生成 Naive UI 菜单选项
 *
 * @param records router.getRoutes() 或路由定义数组
 * @param ctx     认证上下文（用于角色过滤）
 */
export function buildMenuOptions(
  records: MenuRouteRecord[],
  ctx: MenuBuildContext,
): MenuOption[] {
  // 收集所有进侧栏的记录
  const collected: { group: string | null; order: number; path: string; meta: RouteMeta }[] = []
  for (const r of records) {
    const meta = r.meta
    if (!meta || meta.menuOrder === undefined) continue
    collected.push({
      group: meta.menuGroup ?? null,
      order: meta.menuOrder,
      path: r.path,
      meta,
    })
  }

  // 角色过滤
  const visible = collected.filter((it) => {
    if (ctx.authRequired === true) {
      return hasRequiredRole(
        ctx.userRole as 'admin' | 'operator' | 'viewer' | undefined,
        it.meta.requireRole,
      )
    }
    return true
  })

  // 顶层独立项（无 menuGroup）
  const standalone = visible
    .filter((it) => it.group === null)
    .sort((a, b) => a.order - b.order)

  // 分组子项
  const grouped: Record<string, typeof visible> = {}
  for (const it of visible) {
    if (it.group !== null) {
      ;(grouped[it.group] ||= []).push(it)
    }
  }

  const options: MenuOption[] = standalone.map(toMenuOption)
  for (const group of MENU_GROUP_ORDER) {
    const children = grouped[group]
    if (!children || children.length === 0) continue
    children.sort((a, b) => a.order - b.order)
    options.push({
      label: group,
      key: group,
      children: children.map(toMenuOption),
    })
  }
  return options
}

function toMenuOption(it: { path: string; meta: RouteMeta }): MenuOption {
  const opt: MenuOption = {
    label: it.meta.title || it.path,
    key: it.path,
  }
  if (it.meta.icon) {
    opt.icon = renderMenuIcon(it.meta.icon as IconName)
  }
  return opt
}
