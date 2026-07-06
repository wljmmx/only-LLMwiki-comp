import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { User } from '@/api/auth'

const mockRouter = {
  replace: vi.fn(),
}

vi.mock('vue-router', () => ({
  useRouter: () => mockRouter,
}))

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  logout: vi.fn(),
  getMe: vi.fn(),
  getOIDCStatus: vi.fn(),
  listOIDCProviders: vi.fn(),
  redirectToOIDC: vi.fn(),
  checkAuthRequired: vi.fn(),
  listUsers: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
  deleteUser: vi.fn(),
  cleanupSessions: vi.fn(),
  getRoles: vi.fn(),
}))

vi.mock('@/api/index', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
    put: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  getAuthToken: () => 'fake-token',
  AUTH_TOKEN_KEY: 'opskg_token',
}))

import { logout as authLogout } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import ForbiddenView from '@/views/ForbiddenView.vue'
import '@/test/setup'

const adminUser: User = {
  id: 1,
  username: 'admin',
  role: 'admin',
  display_name: 'Administrator',
  email: 'admin@example.com',
  active: true,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
}

const viewerUser: User = {
  id: 2,
  username: 'viewer1',
  role: 'viewer',
  display_name: 'Viewer One',
  email: null,
  active: true,
  created_at: '2026-07-02T00:00:00Z',
  updated_at: '2026-07-02T00:00:00Z',
}

describe('ForbiddenView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    localStorage.clear()
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    // 重置 history.state
    Object.defineProperty(window.history, 'state', {
      value: null,
      configurable: true,
      writable: true,
    })
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView() {
    return mount(ForbiddenView, {
      global: { plugins: [pinia] },
    })
  }

  it('组件能正确 mount 并显示 403 标题', () => {
    const wrapper = mountView()
    expect(wrapper.html()).toContain('403')
    expect(wrapper.html()).toContain('禁止访问')
  })

  it('未登录时 currentRole 显示"未登录"，displayName 显示"匿名"', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.currentRole).toBe('未登录')
    expect(vm.displayName).toBe('匿名')
  })

  it('已登录 admin 时 currentRole 为 admin、displayName 为 display_name', () => {
    const authStore = useAuthStore()
    authStore.user = adminUser
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.currentRole).toBe('admin')
    expect(vm.displayName).toBe('Administrator')
  })

  it('viewer 用户 currentRole 为 viewer', () => {
    const authStore = useAuthStore()
    authStore.user = viewerUser
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.currentRole).toBe('viewer')
    expect(vm.displayName).toBe('Viewer One')
  })

  it('history.state 无 requiredRoles 时 requiredRolesText 默认为 admin', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.requiredRolesText).toBe('admin')
  })

  it('history.state 有 requiredRoles 时 requiredRolesText 为拼接', () => {
    Object.defineProperty(window.history, 'state', {
      value: { requiredRoles: ['admin', 'operator'] },
      configurable: true,
      writable: true,
    })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.requiredRolesText).toBe('admin / operator')
  })

  it('goHome 调用 router.replace 跳转到 /dashboard', () => {
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.goHome()
    expect(mockRouter.replace).toHaveBeenCalledWith('/dashboard')
  })

  it('switchAccount 调用 authStore.logout 后跳转到登录页', async () => {
    localStorage.setItem('opskg_token', 'fake-token')
    ;(authLogout as any).mockResolvedValue({ logged_out: true })
    const wrapper = mountView()
    const vm = wrapper.vm as any
    vm.switchAccount()
    await flushPromises()
    expect(authLogout).toHaveBeenCalled()
    expect(mockRouter.replace).toHaveBeenCalledWith({ name: 'login' })
  })
})
