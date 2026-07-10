/**
 * 角色权限纯函数（S14-1）
 *
 * 从 router/index.ts 抽出，供路由守卫与菜单构建共享，避免循环依赖。
 *
 * 角色层级（admin > operator > viewer）：
 * - requireRole 缺省/空 → 所有已登录用户可访问
 * - userRole 为空 → 视为无权限（由调用方决定是否在 dev 模式放行）
 */
export function hasRequiredRole(
  userRole: string | undefined,
  requireRole: Array<'admin' | 'operator' | 'viewer'> | undefined,
): boolean {
  if (!requireRole || requireRole.length === 0) {
    return true
  }
  if (!userRole) {
    return false
  }
  return requireRole.includes(userRole as 'admin' | 'operator' | 'viewer')
}
