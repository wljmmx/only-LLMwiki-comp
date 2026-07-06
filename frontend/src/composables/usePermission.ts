/**
 * 权限 composable（S15-4 页面级权限粒度）
 *
 * 提供按钮级 / 操作级权限检查，复用 authStore 的角色模型。
 * dev 模式（authRequired === false）放行所有权限，与路由守卫行为一致。
 *
 * 用法：
 *   const { hasRole, hasAnyRole, can } = usePermission()
 *   if (can('user:delete')) { ... }
 */
import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'

export type Role = 'admin' | 'operator' | 'viewer'

/** 角色层级（数字越大权限越高） */
const ROLE_LEVEL: Record<Role, number> = {
  viewer: 1,
  operator: 2,
  admin: 3,
}

/** 操作 → 所需角色映射 */
const ACTION_PERMISSIONS: Record<string, Role[]> = {
  'user:create': ['admin'],
  'user:delete': ['admin'],
  'user:update': ['admin'],
  'wiki:publish': ['admin', 'operator'],
  'wiki:delete': ['admin'],
  'version:rollback': ['admin', 'operator'],
  'review:approve': ['admin', 'operator'],
  'review:reject': ['admin', 'operator'],
  'document:delete': ['admin', 'operator'],
  'document:upload': ['admin', 'operator', 'viewer'],
  'export:create': ['admin', 'operator', 'viewer'],
  'runbook:generate': ['admin', 'operator'],
}

export function usePermission() {
  const authStore = useAuthStore()

  const currentRole = computed<Role | null>(() => authStore.user?.role ?? null)

  /** dev 模式（authRequired === false）放行所有权限 */
  const isDevMode = computed(() => authStore.authRequired === false)

  /** 检查当前用户是否具有指定角色 */
  function hasRole(role: Role): boolean {
    if (isDevMode.value) return true
    return currentRole.value === role
  }

  /** 检查当前用户是否具有给定角色中的任意一个 */
  function hasAnyRole(roles: Role[]): boolean {
    if (isDevMode.value) return true
    if (!currentRole.value) return false
    return roles.includes(currentRole.value)
  }

  /** 检查当前用户角色是否 >= 指定最低角色 */
  function hasMinRole(minRole: Role): boolean {
    if (isDevMode.value) return true
    if (!currentRole.value) return false
    return ROLE_LEVEL[currentRole.value] >= ROLE_LEVEL[minRole]
  }

  /** 检查当前用户是否能执行指定操作 */
  function can(action: string): boolean {
    if (isDevMode.value) return true
    if (!currentRole.value) return false
    const allowed = ACTION_PERMISSIONS[action]
    if (!allowed) return true // 未定义的操作默认允许
    return allowed.includes(currentRole.value)
  }

  return {
    currentRole,
    isDevMode,
    hasRole,
    hasAnyRole,
    hasMinRole,
    can,
  }
}
