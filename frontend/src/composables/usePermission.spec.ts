import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { usePermission } from './usePermission'

// mock api/auth 模块
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

import { useAuthStore } from '@/stores/auth'

describe('composables/usePermission.ts', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('初始状态', () => {
    it('未登录时 currentRole 为 null', () => {
      const { currentRole } = usePermission()
      expect(currentRole.value).toBeNull()
    })

    it('未登录时 isDevMode 为 null（authRequired 未设置）', () => {
      const { isDevMode } = usePermission()
      expect(isDevMode.value).toBe(false)
    })
  })

  describe('hasRole', () => {
    it('admin 用户 hasRole("admin") 为 true', () => {
      const auth = useAuthStore()
      auth.user = { id: 1, username: 'admin', role: 'admin', display_name: 'Admin', email: null, active: true, created_at: '', updated_at: '' }
      auth.authRequired = true

      const { hasRole } = usePermission()
      expect(hasRole('admin')).toBe(true)
      expect(hasRole('operator')).toBe(false)
      expect(hasRole('viewer')).toBe(false)
    })

    it('viewer 用户 hasRole("viewer") 为 true，其他为 false', () => {
      const auth = useAuthStore()
      auth.user = { id: 2, username: 'viewer', role: 'viewer', display_name: 'Viewer', email: null, active: true, created_at: '', updated_at: '' }
      auth.authRequired = true

      const { hasRole } = usePermission()
      expect(hasRole('viewer')).toBe(true)
      expect(hasRole('admin')).toBe(false)
    })

    it('dev 模式（authRequired === false）放行所有角色', () => {
      const auth = useAuthStore()
      auth.authRequired = false

      const { hasRole } = usePermission()
      expect(hasRole('admin')).toBe(true)
      expect(hasRole('operator')).toBe(true)
      expect(hasRole('viewer')).toBe(true)
    })

    it('未登录且非 dev 模式时所有角色返回 false', () => {
      const auth = useAuthStore()
      auth.authRequired = true

      const { hasRole } = usePermission()
      expect(hasRole('admin')).toBe(false)
      expect(hasRole('viewer')).toBe(false)
    })
  })

  describe('hasAnyRole', () => {
    it('operator 用户匹配 [admin, operator] 列表', () => {
      const auth = useAuthStore()
      auth.user = { id: 1, username: 'op', role: 'operator', display_name: 'Op', email: null, active: true, created_at: '', updated_at: '' }
      auth.authRequired = true

      const { hasAnyRole } = usePermission()
      expect(hasAnyRole(['admin', 'operator'])).toBe(true)
      expect(hasAnyRole(['admin'])).toBe(false)
      expect(hasAnyRole(['viewer'])).toBe(false)
    })

    it('dev 模式放行任意角色列表', () => {
      const auth = useAuthStore()
      auth.authRequired = false

      const { hasAnyRole } = usePermission()
      expect(hasAnyRole(['admin'])).toBe(true)
      expect(hasAnyRole(['admin', 'operator', 'viewer'])).toBe(true)
    })
  })

  describe('hasMinRole', () => {
    it('admin >= operator >= viewer 层级', () => {
      const auth = useAuthStore()
      auth.user = { id: 1, username: 'admin', role: 'admin', display_name: 'A', email: null, active: true, created_at: '', updated_at: '' }
      auth.authRequired = true

      const { hasMinRole } = usePermission()
      expect(hasMinRole('viewer')).toBe(true)
      expect(hasMinRole('operator')).toBe(true)
      expect(hasMinRole('admin')).toBe(true)
    })

    it('viewer 不满足 operator/admin 最低要求', () => {
      const auth = useAuthStore()
      auth.user = { id: 2, username: 'v', role: 'viewer', display_name: 'V', email: null, active: true, created_at: '', updated_at: '' }
      auth.authRequired = true

      const { hasMinRole } = usePermission()
      expect(hasMinRole('viewer')).toBe(true)
      expect(hasMinRole('operator')).toBe(false)
      expect(hasMinRole('admin')).toBe(false)
    })

    it('operator 满足 operator/viewer 但不满足 admin', () => {
      const auth = useAuthStore()
      auth.user = { id: 3, username: 'op', role: 'operator', display_name: 'O', email: null, active: true, created_at: '', updated_at: '' }
      auth.authRequired = true

      const { hasMinRole } = usePermission()
      expect(hasMinRole('viewer')).toBe(true)
      expect(hasMinRole('operator')).toBe(true)
      expect(hasMinRole('admin')).toBe(false)
    })
  })

  describe('can (操作权限)', () => {
    it('admin 可以执行所有已定义操作', () => {
      const auth = useAuthStore()
      auth.user = { id: 1, username: 'admin', role: 'admin', display_name: 'A', email: null, active: true, created_at: '', updated_at: '' }
      auth.authRequired = true

      const { can } = usePermission()
      expect(can('user:create')).toBe(true)
      expect(can('user:delete')).toBe(true)
      expect(can('wiki:delete')).toBe(true)
      expect(can('version:rollback')).toBe(true)
      expect(can('document:upload')).toBe(true)
    })

    it('viewer 只能执行 viewer 级别操作', () => {
      const auth = useAuthStore()
      auth.user = { id: 2, username: 'v', role: 'viewer', display_name: 'V', email: null, active: true, created_at: '', updated_at: '' }
      auth.authRequired = true

      const { can } = usePermission()
      expect(can('user:delete')).toBe(false)
      expect(can('wiki:delete')).toBe(false)
      expect(can('version:rollback')).toBe(false)
      expect(can('document:upload')).toBe(true)
      expect(can('export:create')).toBe(true)
    })

    it('operator 可以执行 admin+operator 级别操作但不能 admin 独占', () => {
      const auth = useAuthStore()
      auth.user = { id: 3, username: 'op', role: 'operator', display_name: 'O', email: null, active: true, created_at: '', updated_at: '' }
      auth.authRequired = true

      const { can } = usePermission()
      expect(can('user:delete')).toBe(false) // admin 独占
      expect(can('wiki:delete')).toBe(false) // admin 独占
      expect(can('version:rollback')).toBe(true) // admin+operator
      expect(can('review:approve')).toBe(true) // admin+operator
      expect(can('document:upload')).toBe(true) // 所有角色
    })

    it('未定义的操作默认允许', () => {
      const auth = useAuthStore()
      auth.user = { id: 1, username: 'v', role: 'viewer', display_name: 'V', email: null, active: true, created_at: '', updated_at: '' }
      auth.authRequired = true

      const { can } = usePermission()
      expect(can('unknown:action')).toBe(true)
    })

    it('dev 模式放行所有操作', () => {
      const auth = useAuthStore()
      auth.authRequired = false

      const { can } = usePermission()
      expect(can('user:delete')).toBe(true)
      expect(can('wiki:delete')).toBe(true)
    })

    it('未登录且非 dev 模式时所有操作返回 false', () => {
      const auth = useAuthStore()
      auth.authRequired = true

      const { can } = usePermission()
      expect(can('user:delete')).toBe(false)
      expect(can('document:upload')).toBe(false)
    })
  })

  describe('currentRole 响应式', () => {
    it('用户角色变化时 currentRole 更新', () => {
      const auth = useAuthStore()
      auth.authRequired = true

      const { currentRole, hasRole } = usePermission()
      expect(currentRole.value).toBeNull()

      auth.user = { id: 1, username: 'admin', role: 'admin', display_name: 'A', email: null, active: true, created_at: '', updated_at: '' }
      expect(currentRole.value).toBe('admin')
      expect(hasRole('admin')).toBe(true)

      auth.user!.role = 'viewer'
      expect(currentRole.value).toBe('viewer')
      expect(hasRole('admin')).toBe(false)
      expect(hasRole('viewer')).toBe(true)
    })
  })
})
