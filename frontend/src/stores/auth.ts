/**
 * 认证状态管理（P3-1 SSO）
 *
 * 职责：
 * - 管理 token（localStorage 持久化）
 * - 管理 current user 状态
 * - 提供 login / logout / fetchMe 方法
 * - 加载 OIDC 提供者列表
 *
 * 注意：token key 通过 AUTH_TOKEN_KEY 从 @/api/index 统一导入（S14-3），
 *      store 与 axios 拦截器共享同一 key。
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as authApi from '@/api/auth'
import { AUTH_TOKEN_KEY } from '@/api/index'
import type { OIDCProviderInfo, User } from '@/api/auth'

const TOKEN_KEY = AUTH_TOKEN_KEY

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY))
  const user = ref<User | null>(null)
  const loading = ref(false)
  const oidcProviders = ref<OIDCProviderInfo[]>([])
  const oidcEnabled = ref(false)
  const oidcLoaded = ref(false)
  /** 后端是否要求认证（dev 模式 false，生产模式 true） */
  const authRequired = ref<boolean | null>(null)
  /** P0-9: bootstrap admin 首次登录是否需强制改密 */
  const mustChangePassword = ref(false)

  const isAuthenticated = computed(
    () => !!token.value && !!user.value && user.value.active,
  )
  const isAdmin = computed(() => user.value?.role === 'admin')
  const isOperator = computed(
    () => user.value?.role === 'admin' || user.value?.role === 'operator',
  )
  const displayName = computed(
    () => user.value?.display_name || user.value?.username || '匿名',
  )

  function setToken(newToken: string | null) {
    token.value = newToken
    if (newToken) {
      localStorage.setItem(TOKEN_KEY, newToken)
    } else {
      localStorage.removeItem(TOKEN_KEY)
    }
  }

  /** 用户名密码登录 */
  async function loginWithPassword(username: string, password: string): Promise<User> {
    loading.value = true
    try {
      const resp = await authApi.login(username, password)
      setToken(resp.token)
      user.value = resp.user
      mustChangePassword.value = resp.user.must_change_password ?? false
      return resp.user
    } finally {
      loading.value = false
    }
  }

  /** OIDC 回调：从 URL query 中提取 token 并加载用户 */
  async function handleOIDCCallback(tokenFromUrl: string): Promise<void> {
    setToken(tokenFromUrl)
    await fetchMe()
  }

  /** 获取当前用户信息 */
  async function fetchMe(): Promise<User | null> {
    try {
      const resp = await authApi.getMe()
      // 200 响应：后端可达
      if (resp.authenticated && resp.user) {
        user.value = resp.user
        authRequired.value = true // 后端在用 session 认证
        return resp.user
      }
      // authenticated=false → dev/legacy 模式放行
      user.value = null
      // 如果有 token 但 authenticated=false，说明 token 是 legacy 共享 token（视为 admin）
      // 此时后端不要求用户级登录
      authRequired.value = false
      return null
    } catch (err: any) {
      if (err.response?.status === 401) {
        // 后端要求认证
        authRequired.value = true
        // token 无效，清除
        if (token.value) {
          setToken(null)
        }
        user.value = null
      }
      // 其他错误（网络等）：保持原状，不强制判定
      return null
    }
  }

  /** 注销 */
  async function logout(): Promise<void> {
    try {
      if (token.value) {
        await authApi.logout()
      }
    } catch {
      // 忽略错误，前端总是清除本地状态
    } finally {
      setToken(null)
      user.value = null
    }
  }

  /** P0-9: 强制改密（调用 POST /auth/change-password） */
  async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
    loading.value = true
    try {
      const resp = await authApi.changePassword(oldPassword, newPassword)
      mustChangePassword.value = resp.must_change_password
      if (user.value) {
        user.value = { ...user.value, must_change_password: resp.must_change_password }
      }
    } finally {
      loading.value = false
    }
  }

  /** 加载 OIDC 提供者列表 */
  async function loadOIDCProviders(): Promise<void> {
    if (oidcLoaded.value) return
    try {
      const resp = await authApi.getOIDCStatus()
      oidcEnabled.value = resp.enabled
      oidcProviders.value = resp.providers
      oidcLoaded.value = true
    } catch {
      oidcEnabled.value = false
      oidcProviders.value = []
      oidcLoaded.value = true
    }
  }

  /** 跳转到 OIDC 提供者 */
  function redirectToOIDC(provider: string, redirect: string = '/dashboard'): void {
    authApi.redirectToOIDC(provider, redirect)
  }

  /** 重置 store（用于登出后） */
  function reset() {
    setToken(null)
    user.value = null
    oidcProviders.value = []
    oidcEnabled.value = false
    oidcLoaded.value = false
    authRequired.value = null
  }

  return {
    // state
    token,
    user,
    loading,
    oidcProviders,
    oidcEnabled,
    oidcLoaded,
    authRequired,
    mustChangePassword,
    // computed
    isAuthenticated,
    isAdmin,
    isOperator,
    displayName,
    // actions
    setToken,
    loginWithPassword,
    handleOIDCCallback,
    fetchMe,
    logout,
    changePassword,
    loadOIDCProviders,
    redirectToOIDC,
    reset,
  }
})
