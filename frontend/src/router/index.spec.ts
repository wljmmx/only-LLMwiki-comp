/**
 * 路由守卫测试（S14-1 路由级权限守卫）
 *
 * 测试覆盖：
 * 1. navigationGuard 守卫的角色检查逻辑
 * 2. meta.requireRole 配置正确性
 * 3. 403/404 路由注册
 * 4. 公开路由放行
 * 5. dev 模式不拦截
 * 6. viewer/operator 访问 admin 路由 → 跳 forbidden
 * 7. 未登录访问受保护路由 → 跳 login
 * 8. hasRequiredRole 纯函数测试
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// mock api/auth（router 守卫内部通过 authStore.fetchMe 间接调用）
vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  logout: vi.fn(),
  getMe: vi.fn(),
  getRoles: vi.fn(),
  listUsers: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
  deleteUser: vi.fn(),
  getOIDCStatus: vi.fn(),
  listOIDCProviders: vi.fn(),
  redirectToOIDC: vi.fn(),
  checkAuthRequired: vi.fn(),
}))

import * as authApi from '@/api/auth'
import {
  default as router,
  hasRequiredRole,
  navigationGuard,
  _resetAuthInitializedForTest,
} from '@/router/index'

function makeUser(role: 'admin' | 'operator' | 'viewer') {
  return {
    id: 1,
    username: `${role}1`,
    role,
    display_name: `${role} user`,
    email: null,
    active: true,
    created_at: '',
    updated_at: '',
  }
}

/**
 * 模拟已登录场景：设置 token + mock fetchMe 返回已认证用户
 * isAuthenticated 要求 token && user && user.active 三者都满足
 */
function mockLoggedIn(role: 'admin' | 'operator' | 'viewer') {
  localStorage.setItem('opskg_token', `test-token-${role}`)
  vi.mocked(authApi.getMe).mockResolvedValue({
    authenticated: true,
    user: makeUser(role),
  })
}

describe('router/index.ts — S14-1 路由级权限守卫', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
    _resetAuthInitializedForTest()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  // ────────── 1. 路由结构验证 ──────────

  it('注册 /forbidden 路由且为 public', () => {
    const route = router.resolve({ name: 'forbidden' })
    expect(route.name).toBe('forbidden')
    expect(route.meta.public).toBe(true)
  })

  it('注册 /users 路由且 requireRole 包含 admin', () => {
    const route = router.resolve({ name: 'users' })
    expect(route.name).toBe('users')
    expect(route.meta.requireRole).toEqual(['admin'])
  })

  it('注册 catch-all 404 路由', () => {
    const route = router.resolve('/some/nonexistent/path/xyz')
    expect(route.name).toBe('not-found')
  })

  it('dashboard 路由无 requireRole（所有已登录用户可访问）', () => {
    const route = router.resolve({ name: 'dashboard' })
    expect(route.meta.requireRole).toBeUndefined()
  })

  it('login 路由为 public', () => {
    const route = router.resolve({ name: 'login' })
    expect(route.meta.public).toBe(true)
  })

  // ────────── 2. hasRequiredRole 纯函数测试 ──────────

  it('hasRequiredRole: 无 requireRole 时总是放行', () => {
    expect(hasRequiredRole('viewer', undefined)).toBe(true)
    expect(hasRequiredRole('admin', [])).toBe(true)
  })

  it('hasRequiredRole: userRole 为 undefined 时拒绝', () => {
    expect(hasRequiredRole(undefined, ['admin'])).toBe(false)
  })

  it('hasRequiredRole: 角色匹配时放行', () => {
    expect(hasRequiredRole('admin', ['admin'])).toBe(true)
    expect(hasRequiredRole('operator', ['admin', 'operator'])).toBe(true)
  })

  it('hasRequiredRole: 角色不匹配时拒绝', () => {
    expect(hasRequiredRole('viewer', ['admin'])).toBe(false)
    expect(hasRequiredRole('operator', ['admin'])).toBe(false)
  })

  // ────────── 3. 公开路由放行 ──────────

  it('public 路由（login）放行', async () => {
    const to = router.resolve({ name: 'login' })
    const result = await navigationGuard(to as any)
    expect(result).toBe(true)
  })

  it('public 路由（forbidden）放行', async () => {
    const to = router.resolve({ name: 'forbidden' })
    const result = await navigationGuard(to as any)
    expect(result).toBe(true)
  })

  // ────────── 4. dev 模式不拦截 ──────────

  it('dev 模式（authRequired=false）下 viewer 可访问 admin 路由', async () => {
    // 模拟 fetchMe 返回 authenticated=false（dev 模式）
    vi.mocked(authApi.getMe).mockResolvedValue({
      authenticated: false,
      user: null,
    })

    const to = router.resolve({ name: 'users' })
    const result = await navigationGuard(to as any)
    // dev 模式不拦截，应放行
    expect(result).toBe(true)
  })

  // ────────── 5. 未登录访问受保护路由 → 跳 login ──────────

  it('未登录访问 admin 路由 → 跳 login 并带 redirect', async () => {
    // 模拟 401（未登录）
    vi.mocked(authApi.getMe).mockRejectedValue({
      response: { status: 401 },
    })

    const to = router.resolve({ name: 'users' })
    const result: any = await navigationGuard(to as any)
    expect(result).not.toBe(true)
    expect(result.name).toBe('login')
    expect(result.query.redirect).toBe('/users')
  })

  // ────────── 6. 已登录但角色不足 → 跳 forbidden ──────────

  it('viewer 访问 admin 路由 → 跳 forbidden', async () => {
    mockLoggedIn('viewer')

    const to = router.resolve({ name: 'users' })
    const result: any = await navigationGuard(to as any)
    expect(result).not.toBe(true)
    expect(result.name).toBe('forbidden')
    // state 中 requiredRoles 已序列化为逗号分隔字符串（HistoryState 不接受 string[]）
    expect(result.state?.requiredRoles).toBe('admin')
  })

  it('operator 访问 admin 路由 → 跳 forbidden', async () => {
    mockLoggedIn('operator')

    const to = router.resolve({ name: 'users' })
    const result: any = await navigationGuard(to as any)
    expect(result).not.toBe(true)
    expect(result.name).toBe('forbidden')
  })

  // ────────── 7. 已登录且角色匹配 → 放行 ──────────

  it('admin 访问 admin 路由 → 放行', async () => {
    mockLoggedIn('admin')

    const to = router.resolve({ name: 'users' })
    const result = await navigationGuard(to as any)
    expect(result).toBe(true)
  })

  it('viewer 访问无 requireRole 路由（dashboard）→ 放行', async () => {
    mockLoggedIn('viewer')

    const to = router.resolve({ name: 'dashboard' })
    const result = await navigationGuard(to as any)
    expect(result).toBe(true)
  })

  // ────────── 8. 后端不可达时不拦截（保守放行） ──────────

  it('后端不可达（500）时访问 admin 路由 → 放行（不锁死）', async () => {
    vi.mocked(authApi.getMe).mockRejectedValue({
      response: { status: 500 },
    })

    const to = router.resolve({ name: 'users' })
    const result = await navigationGuard(to as any)
    // 后端不可达时不强制角色检查（避免网络抖动锁死）
    expect(result).toBe(true)
  })

  // ────────── 9. 第二次导航不重复 fetchMe（authInitialized 缓存） ──────────

  it('第二次导航不重复 fetchMe', async () => {
    mockLoggedIn('admin')

    const to1 = router.resolve({ name: 'dashboard' })
    await navigationGuard(to1 as any)
    expect(authApi.getMe).toHaveBeenCalledTimes(1)

    // 第二次导航：不应再次调用 fetchMe
    const to2 = router.resolve({ name: 'users' })
    await navigationGuard(to2 as any)
    expect(authApi.getMe).toHaveBeenCalledTimes(1)
  })
})
