import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

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

const mockAuthStore = {
  handleOIDCCallback: vi.fn(),
  isAuthenticated: false,
}

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => mockAuthStore,
}))

import LoginCallbackView from '@/views/LoginCallbackView.vue'
import '@/test/setup'

describe('LoginCallbackView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    mockRoute.query = {}
    mockAuthStore.isAuthenticated = false
    mockAuthStore.handleOIDCCallback.mockResolvedValue(undefined)
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView() {
    return mount(LoginCallbackView, {
      global: { plugins: [pinia] },
    })
  }

  it('初始状态 processing 为 true', () => {
    mockRoute.query = { token: 'abc123', redirect: '/dashboard' }
    const wrapper = mountView()
    const vm = wrapper.vm as any
    // onMounted 已触发但 await handleOIDCCallback 尚未 resolve
    expect(vm.processing).toBe(true)
  })

  it('query 含 error 时显示错误且 processing 为 false', async () => {
    mockRoute.query = { error: 'access_denied', error_description: '用户拒绝授权' }
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.error).toBe('access_denied')
    expect(vm.errorDescription).toBe('用户拒绝授权')
    expect(vm.processing).toBe(false)
    expect(mockAuthStore.handleOIDCCallback).not.toHaveBeenCalled()
  })

  it('query 含 error 但无 error_description 时使用默认描述', async () => {
    mockRoute.query = { error: 'server_error' }
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.error).toBe('server_error')
    expect(vm.errorDescription).toBe('OIDC 认证失败')
  })

  it('query 缺少 token 时显示 invalid_callback 错误', async () => {
    mockRoute.query = {}
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.error).toBe('invalid_callback')
    expect(vm.errorDescription).toBe('回调 URL 缺少 token 参数')
    expect(vm.processing).toBe(false)
    expect(mockAuthStore.handleOIDCCallback).not.toHaveBeenCalled()
  })

  it('有 token 时调用 handleOIDCCallback 并跳转', async () => {
    mockRoute.query = { token: 'tok123', redirect: '/dashboard' }
    const wrapper = mountView()
    await flushPromises()
    expect(mockAuthStore.handleOIDCCallback).toHaveBeenCalledWith('tok123')
    expect(mockRouter.replace).toHaveBeenCalledWith('/dashboard')
  })

  it('无 redirect 参数时默认跳转 /dashboard', async () => {
    mockRoute.query = { token: 'tok123' }
    const wrapper = mountView()
    await flushPromises()
    expect(mockRouter.replace).toHaveBeenCalledWith('/dashboard')
  })

  it('redirect 自定义路径时跳转到指定路径', async () => {
    mockRoute.query = { token: 'tok123', redirect: '/documents' }
    const wrapper = mountView()
    await flushPromises()
    expect(mockRouter.replace).toHaveBeenCalledWith('/documents')
  })

  it('handleOIDCCallback 抛错时显示 callback_failed 错误', async () => {
    mockAuthStore.handleOIDCCallback.mockRejectedValue(new Error('网络异常'))
    mockRoute.query = { token: 'tok123' }
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.error).toBe('callback_failed')
    expect(vm.errorDescription).toBe('网络异常')
    expect(vm.processing).toBe(false)
  })

  it('backToLogin 调用 router.replace 跳转到 /login', async () => {
    mockRoute.query = { error: 'test' }
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.backToLogin()
    expect(mockRouter.replace).toHaveBeenCalledWith('/login')
  })
})
