/**
 * 认证 API 封装（P3-1 SSO）
 *
 * 端点：
 * - POST /auth/login           用户名密码登录
 * - POST /auth/logout          注销
 * - GET  /auth/me              当前用户信息
 * - GET  /auth/oidc/status     OIDC 是否启用
 * - GET  /auth/oidc/providers  OIDC 提供者列表
 *
 * OIDC 登录流程（不通过 axios，直接 window.location 跳转）：
 * - GET /auth/oidc/{provider}?redirect=/dashboard → 后端 302 到 IdP
 * - IdP 完成认证后回调 /auth/oidc/{provider}/callback
 * - 后端签发 session 后 302 到 /login/callback?token=...&redirect=...
 */
import api, { getApiBaseUrl } from './index'

export interface User {
  id: number
  username: string
  role: 'admin' | 'operator' | 'viewer'
  display_name: string | null
  email: string | null
  active: boolean
  must_change_password?: boolean
  created_at: string
  updated_at: string
}

export interface LoginResponse {
  token: string
  user: User
}

export interface MeResponse {
  authenticated: boolean
  user: User | null
}

export interface OIDCProviderInfo {
  name: string
  display_name: string
  scopes: string[]
}

export interface OIDCStatusResponse {
  enabled: boolean
  providers: OIDCProviderInfo[]
}

export interface ChangePasswordResponse {
  changed: boolean
  must_change_password: boolean
}

/** 用户名密码登录 */
export async function login(username: string, password: string): Promise<LoginResponse> {
  return await api.post('/auth/login', { username, password })
}

/** 修改自己的密码（P0-9: 强制改密） */
export async function changePassword(
  oldPassword: string,
  newPassword: string,
): Promise<ChangePasswordResponse> {
  return await api.post('/auth/change-password', {
    old_password: oldPassword,
    new_password: newPassword,
  })
}

/** 注销 */
export async function logout(): Promise<{ logged_out: boolean }> {
  return await api.post('/auth/logout')
}

/** 获取当前用户信息 */
export async function getMe(): Promise<MeResponse> {
  return await api.get('/auth/me')
}

/** 获取可用角色 */
export async function getRoles(): Promise<{ roles: string[]; hierarchy: Record<string, number> }> {
  return await api.get('/auth/roles')
}

/** 用户列表（admin） */
export async function listUsers(): Promise<{ users: User[]; count: number }> {
  return await api.get('/auth/users')
}

/** 创建用户（admin） */
export async function createUser(payload: {
  username: string
  password: string
  role?: string
  display_name?: string
  email?: string
}): Promise<{ created: boolean; user: User }> {
  return await api.post('/auth/users', payload)
}

/** 更新用户（admin） */
export async function updateUser(
  userId: number,
  payload: {
    role?: string
    display_name?: string
    email?: string
    active?: boolean
    password?: string
  },
): Promise<{ updated: boolean; user: User }> {
  return await api.put(`/auth/users/${userId}`, payload)
}

/** 删除用户（admin） */
export async function deleteUser(userId: number): Promise<{ deleted: boolean; user_id: number }> {
  return await api.delete(`/auth/users/${userId}`)
}

/** 清理过期 session（admin） */
export async function cleanupSessions(): Promise<{ cleaned: number }> {
  return await api.post('/auth/cleanup')
}

/** OIDC 状态 */
export async function getOIDCStatus(): Promise<OIDCStatusResponse> {
  return await api.get('/auth/oidc/status')
}

/** OIDC 提供者列表 */
export async function listOIDCProviders(): Promise<{ providers: OIDCProviderInfo[]; count: number }> {
  return await api.get('/auth/oidc/providers')
}

/**
 * 跳转到 OIDC 提供者授权页
 * 直接修改 window.location 触发后端 302 重定向
 */
export function redirectToOIDC(provider: string, redirect: string = '/dashboard'): void {
  // P1-5: 使用 getApiBaseUrl() 支持版本化前缀
  const url = `${getApiBaseUrl()}/auth/oidc/${provider}?redirect=${encodeURIComponent(redirect)}`
  window.location.href = url
}

/** 判断当前环境是否需要登录（dev 模式无 token 配置时后端放行） */
export async function checkAuthRequired(): Promise<boolean> {
  // 通过 /auth/me 判断：返回 authenticated=false 且无 401 → dev 模式放行
  // 返回 401 → 需要登录
  try {
    await getMe()
    // authenticated=false 表示 dev/legacy 模式放行，不需要登录
    // 但如果有 token 且 authenticated=true，也不需要登录
    return false
  } catch (err: any) {
    if (err.response?.status === 401) {
      return true
    }
    // 其他错误（网络等）保守起见不强制登录
    return false
  }
}
