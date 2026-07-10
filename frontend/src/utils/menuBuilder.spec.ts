/**
 * P1-5: 菜单单一事实源 — menuBuilder 纯函数测试
 *
 * 覆盖：
 * - 顶层独立项（无 menuGroup + menuOrder）
 * - 分组子项（menuGroup + menuOrder）
 * - menuOrder 缺省 → 不进侧栏
 * - 组内排序、分组顺序
 * - 角色过滤：authRequired=true 时按 requireRole 过滤；dev/null 放行
 * - title/icon 取自 meta
 */
import { describe, it, expect } from 'vitest'
import { buildMenuOptions, type MenuRouteRecord } from '@/utils/menuBuilder'

function rec(
  path: string,
  meta: Partial<NonNullable<MenuRouteRecord['meta']>>,
): MenuRouteRecord {
  return { path, meta: meta as any }
}

describe('menuBuilder — buildMenuOptions', () => {
  it('顶层独立项：无 menuGroup + menuOrder → 直接出现在菜单顶层', () => {
    const options = buildMenuOptions(
      [rec('/dashboard', { title: '仪表盘', icon: 'dashboard', menuOrder: 0 })],
      { authRequired: false },
    )
    expect(options).toHaveLength(1)
    expect(options[0].label).toBe('仪表盘')
    expect(options[0].key).toBe('/dashboard')
    expect(options[0].children).toBeUndefined()
    expect(typeof options[0].icon).toBe('function')
  })

  it('menuOrder 缺省 → 不进侧栏', () => {
    const options = buildMenuOptions(
      [
        rec('/login', { title: '登录', public: true }),
        rec('/dashboard', { title: '仪表盘', menuOrder: 0 }),
      ],
      { authRequired: false },
    )
    expect(options).toHaveLength(1)
    expect(options[0].key).toBe('/dashboard')
  })

  it('分组子项按 menuGroup 聚合，组内按 menuOrder 排序', () => {
    const options = buildMenuOptions(
      [
        rec('/graph', { title: '知识图谱', menuGroup: '知识管理', menuOrder: 4 }),
        rec('/documents', { title: '文档管理', menuGroup: '知识管理', menuOrder: 0 }),
        rec('/search', { title: '知识搜索', menuGroup: '知识管理', menuOrder: 1 }),
      ],
      { authRequired: false },
    )
    expect(options).toHaveLength(1)
    const group = options[0]
    expect(group.label).toBe('知识管理')
    expect(group.key).toBe('知识管理')
    expect(group.children).toHaveLength(3)
    expect((group.children as any[]).map((c) => c.key)).toEqual([
      '/documents',
      '/search',
      '/graph',
    ])
  })

  it('分组顺序遵循 MENU_GROUP_ORDER（知识管理 < 质量治理 < AIOps < 系统工具）', () => {
    const options = buildMenuOptions(
      [
        rec('/mcp', { title: 'MCP', menuGroup: '系统工具', menuOrder: 0 }),
        rec('/incidents', { title: 'Incident', menuGroup: 'AIOps', menuOrder: 0 }),
        rec('/wiki-health', { title: '健康检查', menuGroup: '质量治理', menuOrder: 0 }),
        rec('/documents', { title: '文档', menuGroup: '知识管理', menuOrder: 0 }),
      ],
      { authRequired: false },
    )
    expect(options.map((o) => o.label)).toEqual([
      '知识管理',
      '质量治理',
      'AIOps',
      '系统工具',
    ])
  })

  it('顶层独立项排在所有分组之前', () => {
    const options = buildMenuOptions(
      [
        rec('/documents', { title: '文档', menuGroup: '知识管理', menuOrder: 0 }),
        rec('/dashboard', { title: '仪表盘', menuOrder: 0 }),
        rec('/review', { title: '审查', menuGroup: '质量治理', menuOrder: 0 }),
      ],
      { authRequired: false },
    )
    expect(options[0].label).toBe('仪表盘')
    expect(options[0].children).toBeUndefined()
    expect(options[1].label).toBe('知识管理')
    expect(options[2].label).toBe('质量治理')
  })

  it('角色过滤：authRequired=true 时 requireRole=admin 的项对 viewer 不可见', () => {
    const options = buildMenuOptions(
      [
        rec('/dashboard', { title: '仪表盘', menuOrder: 0 }),
        rec('/users', {
          title: '用户管理',
          menuGroup: '系统工具',
          menuOrder: 0,
          requireRole: ['admin'],
        }),
        rec('/mcp', { title: 'MCP', menuGroup: '系统工具', menuOrder: 1 }),
      ],
      { authRequired: true, userRole: 'viewer' },
    )
    // 仪表盘 + 系统工具（仅 MCP，无 users）
    expect(options).toHaveLength(2)
    const sysGroup = options.find((o) => o.label === '系统工具')!
    expect((sysGroup.children as any[]).map((c) => c.key)).toEqual(['/mcp'])
  })

  it('角色过滤：authRequired=true 且 admin → users 可见', () => {
    const options = buildMenuOptions(
      [
        rec('/users', {
          title: '用户管理',
          menuGroup: '系统工具',
          menuOrder: 0,
          requireRole: ['admin'],
        }),
      ],
      { authRequired: true, userRole: 'admin' },
    )
    const sysGroup = options[0]
    expect((sysGroup.children as any[]).map((c) => c.key)).toEqual(['/users'])
  })

  it('dev 模式（authRequired=false）：requireRole=admin 的项对所有人可见', () => {
    const options = buildMenuOptions(
      [
        rec('/users', {
          title: '用户管理',
          menuGroup: '系统工具',
          menuOrder: 0,
          requireRole: ['admin'],
        }),
      ],
      { authRequired: false, userRole: 'viewer' },
    )
    const sysGroup = options[0]
    expect((sysGroup.children as any[])).toHaveLength(1)
  })

  it('后端不可达（authRequired=null）：放行所有项（与路由守卫一致）', () => {
    const options = buildMenuOptions(
      [
        rec('/users', {
          title: '用户管理',
          menuGroup: '系统工具',
          menuOrder: 0,
          requireRole: ['admin'],
        }),
      ],
      { authRequired: null, userRole: undefined },
    )
    expect((options[0].children as any[])).toHaveLength(1)
  })

  it('title 缺省时用 path 作为 label', () => {
    const options = buildMenuOptions(
      [rec('/some-path', { menuOrder: 0 })],
      { authRequired: false },
    )
    expect(options[0].label).toBe('/some-path')
  })

  it('icon 缺省时不设置 icon 字段', () => {
    const options = buildMenuOptions(
      [rec('/no-icon', { title: '无图标', menuOrder: 0 })],
      { authRequired: false },
    )
    expect(options[0].icon).toBeUndefined()
  })
})
