import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { User } from '@/api/auth'

const mockMessage = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return { ...actual, useMessage: () => mockMessage }
})

const mockRouter = {
  replace: vi.fn(),
}
const mockRoute = {
  query: {} as Record<string, string>,
}

vi.mock('vue-router', () => ({
  useRouter: () => mockRouter,
  useRoute: () => mockRoute,
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

import { login as authLogin, getMe, getOIDCStatus, redirectToOIDC } from '@/api/auth'
import LoginView from '@/views/LoginView.vue'
import '@/test/setup'

const testUser: User = {
  id: 1,
  username: 'admin',
  role: 'admin',
  display_name: 'Administrator',
  email: 'admin@example.com',
  active: true,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
}

describe('LoginView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    localStorage.clear()
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    mockRoute.query = {}
    ;(getOIDCStatus as any).mockResolvedValue({ enabled: false, providers: [] })
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView() {
    return mount(LoginView, {
      global: { plugins: [pinia] },
    })
  }

  it('组件能正确 mount 并显示登录标题', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.html()).toContain('OpsKG')
    expect(wrapper.html()).toContain('登录')
  })

  it('初始状态：form 为空、submitting false、errorMessage 空', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.form.username).toBe('')
    expect(vm.form.password).toBe('')
    expect(vm.submitting).toBe(false)
    expect(vm.errorMessage).toBe('')
  })

  it('onMounted 调用 loadOIDCProviders', async () => {
    mountView()
    await flushPromises()
    expect(getOIDCStatus).toHaveBeenCalledTimes(1)
  })

  it('redirect 从 query.redirect 读取，默认 /dashboard', async () => {
    mockRoute.query = { redirect: '/wiki' }
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.redirect).toBe('/wiki')
  })

  it('onMounted 已登录时跳转到 redirect', async () => {
    localStorage.setItem('opskg_token', 'existing-token')
    ;(getMe as any).mockResolvedValue({ authenticated: true, user: testUser })
    mockRoute.query = { redirect: '/documents' }
    mountView()
    await flushPromises()
    expect(getMe).toHaveBeenCalled()
    expect(mockRouter.replace).toHaveBeenCalledWith('/documents')
  })

  it('onMounted 已登录但 fetchMe 返回 null 时不跳转', async () => {
    localStorage.setItem('opskg_token', 'existing-token')
    ;(getMe as any).mockResolvedValue({ authenticated: false, user: null })
    mountView()
    await flushPromises()
    expect(mockRouter.replace).not.toHaveBeenCalled()
  })

  it('handleLogin 空表单时设置 errorMessage 不调用 login', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleLogin()
    expect(vm.errorMessage).toBe('请输入用户名和密码')
    expect(authLogin).not.toHaveBeenCalled()
  })

  it('handleLogin 成功时调用 message.success 并跳转', async () => {
    ;(authLogin as any).mockResolvedValue({ token: 'newtoken', user: testUser })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.form.username = 'admin'
    vm.form.password = 'admin'
    await vm.handleLogin()
    expect(authLogin).toHaveBeenCalledWith('admin', 'admin')
    expect(mockMessage.success).toHaveBeenCalledWith('欢迎，Administrator')
    expect(mockRouter.replace).toHaveBeenCalledWith('/dashboard')
    expect(vm.submitting).toBe(false)
  })

  it('handleLogin 失败时设置 errorMessage', async () => {
    ;(authLogin as any).mockRejectedValue({
      response: { data: { detail: '密码错误' } },
      message: 'Request failed',
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.form.username = 'admin'
    vm.form.password = 'wrong'
    await vm.handleLogin()
    expect(vm.errorMessage).toBe('密码错误')
    expect(vm.submitting).toBe(false)
    expect(mockRouter.replace).not.toHaveBeenCalled()
  })

  it('handleLogin 失败且 detail 非字符串时使用默认错误信息', async () => {
    ;(authLogin as any).mockRejectedValue({
      response: { data: { detail: { code: 'invalid' } } },
      message: 'Network error',
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.form.username = 'admin'
    vm.form.password = 'wrong'
    await vm.handleLogin()
    expect(vm.errorMessage).toBe('登录失败')
  })

  it('handleOIDC 调用 redirectToOIDC 传入 provider 与 redirect', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.handleOIDC('google')
    expect(redirectToOIDC).toHaveBeenCalledWith('google', '/dashboard')
  })
})
