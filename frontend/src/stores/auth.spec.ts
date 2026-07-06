import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from './auth'

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

import * as authApi from '@/api/auth'

describe('stores/auth.ts', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('初始 state：无 token、无 user、未认证', () => {
    const store = useAuthStore()
    expect(store.token).toBeNull()
    expect(store.user).toBeNull()
    expect(store.isAuthenticated).toBe(false)
    expect(store.isAdmin).toBe(false)
    expect(store.isOperator).toBe(false)
    expect(store.displayName).toBe('匿名')
    expect(store.loading).toBe(false)
    expect(store.oidcEnabled).toBe(false)
    expect(store.oidcProviders).toEqual([])
    expect(store.authRequired).toBeNull()
  })

  it('setToken 保存到 localStorage 并更新 store', () => {
    const store = useAuthStore()
    store.setToken('test-token-123')
    expect(store.token).toBe('test-token-123')
    expect(localStorage.getItem('opskg_token')).toBe('test-token-123')
  })

  it('setToken(null) 清除 localStorage 与 store', () => {
    const store = useAuthStore()
    store.setToken('test-token')
    store.setToken(null)
    expect(store.token).toBeNull()
    expect(localStorage.getItem('opskg_token')).toBeNull()
  })

  it('loginWithPassword 调用 API 并设置 token + user', async () => {
    const mockUser = {
      id: 1,
      username: 'admin',
      role: 'admin' as const,
      display_name: 'Admin',
      email: null,
      active: true,
      created_at: '',
      updated_at: '',
    }
    ;(authApi.login as any).mockResolvedValue({
      token: 'session-token-xyz',
      user: mockUser,
    })

    const store = useAuthStore()
    const user = await store.loginWithPassword('admin', 'pass')

    expect(authApi.login).toHaveBeenCalledWith('admin', 'pass')
    expect(user).toEqual(mockUser)
    expect(store.token).toBe('session-token-xyz')
    expect(store.user).toEqual(mockUser)
    expect(store.isAuthenticated).toBe(true)
    expect(store.isAdmin).toBe(true)
    expect(store.loading).toBe(false)
  })

  it('loginWithPassword 失败时不设置 token', async () => {
    ;(authApi.login as any).mockRejectedValue(new Error('Invalid credentials'))

    const store = useAuthStore()
    await expect(store.loginWithPassword('bad', 'bad')).rejects.toThrow()
    expect(store.token).toBeNull()
    expect(store.user).toBeNull()
    expect(store.loading).toBe(false)
  })

  it('handleOIDCCallback 设置 token 并加载用户', async () => {
    const mockUser = {
      id: 2,
      username: 'oidc_user',
      role: 'viewer' as const,
      display_name: 'OIDC User',
      email: 'user@example.com',
      active: true,
      created_at: '',
      updated_at: '',
    }
    ;(authApi.getMe as any).mockResolvedValue({
      authenticated: true,
      user: mockUser,
    })

    const store = useAuthStore()
    await store.handleOIDCCallback('oidc-token-abc')

    expect(store.token).toBe('oidc-token-abc')
    expect(store.user).toEqual(mockUser)
    expect(store.isAuthenticated).toBe(true)
    expect(store.authRequired).toBe(true)
  })

  it('fetchMe 200 + authenticated=true 设置 user 与 authRequired=true', async () => {
    const mockUser = {
      id: 1,
      username: 'admin',
      role: 'admin' as const,
      display_name: 'Admin',
      email: null,
      active: true,
      created_at: '',
      updated_at: '',
    }
    ;(authApi.getMe as any).mockResolvedValue({
      authenticated: true,
      user: mockUser,
    })

    const store = useAuthStore()
    store.setToken('valid-token')
    const user = await store.fetchMe()

    expect(user).toEqual(mockUser)
    expect(store.user).toEqual(mockUser)
    expect(store.authRequired).toBe(true)
  })

  it('fetchMe 200 + authenticated=false 视为 dev 模式（authRequired=false）', async () => {
    ;(authApi.getMe as any).mockResolvedValue({
      authenticated: false,
      user: null,
    })

    const store = useAuthStore()
    const user = await store.fetchMe()

    expect(user).toBeNull()
    expect(store.user).toBeNull()
    expect(store.authRequired).toBe(false)
  })

  it('fetchMe 401 清除 token 并设置 authRequired=true', async () => {
    const err = new Error('Unauthorized')
    ;(err as any).response = { status: 401 }
    ;(authApi.getMe as any).mockRejectedValue(err)

    const store = useAuthStore()
    store.setToken('invalid-token')
    const user = await store.fetchMe()

    expect(user).toBeNull()
    expect(store.user).toBeNull()
    expect(store.token).toBeNull()
    expect(store.authRequired).toBe(true)
  })

  it('fetchMe 网络/其他错误不清除 token', async () => {
    const err = new Error('Network Error')
    ;(err as any).response = { status: 500 }
    ;(authApi.getMe as any).mockRejectedValue(err)

    const store = useAuthStore()
    store.setToken('existing-token')
    const user = await store.fetchMe()

    expect(user).toBeNull()
    expect(store.token).toBe('existing-token')
    expect(store.authRequired).toBeNull()
  })

  it('logout 调用 API 并清除本地状态', async () => {
    ;(authApi.logout as any).mockResolvedValue({ logged_out: true })

    const store = useAuthStore()
    store.setToken('to-be-cleared')
    store.user = {
      id: 1,
      username: 'admin',
      role: 'admin',
      display_name: 'Admin',
      email: null,
      active: true,
      created_at: '',
      updated_at: '',
    }
    await store.logout()

    expect(authApi.logout).toHaveBeenCalled()
    expect(store.token).toBeNull()
    expect(store.user).toBeNull()
  })

  it('logout API 失败仍清除本地状态', async () => {
    ;(authApi.logout as any).mockRejectedValue(new Error('Network'))

    const store = useAuthStore()
    store.setToken('to-be-cleared')
    await store.logout()

    expect(store.token).toBeNull()
    expect(store.user).toBeNull()
  })

  it('loadOIDCProviders 加载提供者列表', async () => {
    ;(authApi.getOIDCStatus as any).mockResolvedValue({
      enabled: true,
      providers: [
        { name: 'google', display_name: 'Google', scopes: ['openid', 'email'] },
      ],
    })

    const store = useAuthStore()
    await store.loadOIDCProviders()

    expect(store.oidcEnabled).toBe(true)
    expect(store.oidcProviders).toHaveLength(1)
    expect(store.oidcProviders[0].name).toBe('google')
    expect(store.oidcLoaded).toBe(true)
  })

  it('loadOIDCProviders 失败时禁用 OIDC', async () => {
    ;(authApi.getOIDCStatus as any).mockRejectedValue(new Error('Network'))

    const store = useAuthStore()
    await store.loadOIDCProviders()

    expect(store.oidcEnabled).toBe(false)
    expect(store.oidcProviders).toEqual([])
    expect(store.oidcLoaded).toBe(true)
  })

  it('loadOIDCProviders 已加载后不重复请求', async () => {
    ;(authApi.getOIDCStatus as any).mockResolvedValue({
      enabled: true,
      providers: [{ name: 'google', display_name: 'Google', scopes: [] }],
    })

    const store = useAuthStore()
    await store.loadOIDCProviders()
    await store.loadOIDCProviders()

    expect(authApi.getOIDCStatus).toHaveBeenCalledTimes(1)
  })

  it('redirectToOIDC 调用 api 模块', () => {
    const store = useAuthStore()
    store.redirectToOIDC('google', '/dashboard')
    expect(authApi.redirectToOIDC).toHaveBeenCalledWith('google', '/dashboard')
  })

  it('reset 清除所有状态', () => {
    const store = useAuthStore()
    store.setToken('token')
    store.user = {
      id: 1,
      username: 'x',
      role: 'admin',
      display_name: 'X',
      email: null,
      active: true,
      created_at: '',
      updated_at: '',
    }
    store.oidcEnabled = true
    store.oidcProviders = [{ name: 'g', display_name: 'G', scopes: [] }]
    store.oidcLoaded = true
    store.authRequired = true

    store.reset()

    expect(store.token).toBeNull()
    expect(store.user).toBeNull()
    expect(store.oidcEnabled).toBe(false)
    expect(store.oidcProviders).toEqual([])
    expect(store.oidcLoaded).toBe(false)
    expect(store.authRequired).toBeNull()
  })

  it('computed 属性随 user 变化', () => {
    const store = useAuthStore()
    expect(store.displayName).toBe('匿名')

    store.user = {
      id: 1,
      username: 'admin',
      role: 'admin',
      display_name: '管理员',
      email: null,
      active: true,
      created_at: '',
      updated_at: '',
    }
    store.setToken('t')
    expect(store.displayName).toBe('管理员')
    expect(store.isAuthenticated).toBe(true)
    expect(store.isAdmin).toBe(true)
    expect(store.isOperator).toBe(true)

    store.user!.role = 'viewer'
    expect(store.isAdmin).toBe(false)
    expect(store.isOperator).toBe(false)
  })

  it('isAuthenticated 要求 token + active user', () => {
    const store = useAuthStore()
    // 无 token 无 user
    expect(store.isAuthenticated).toBe(false)
    // 有 token 无 user
    store.setToken('t')
    expect(store.isAuthenticated).toBe(false)
    // 有 token 有 inactive user
    store.user = {
      id: 1,
      username: 'x',
      role: 'viewer',
      display_name: 'X',
      email: null,
      active: false,
      created_at: '',
      updated_at: '',
    }
    expect(store.isAuthenticated).toBe(false)
    // 有 token 有 active user
    store.user!.active = true
    expect(store.isAuthenticated).toBe(true)
  })
})
