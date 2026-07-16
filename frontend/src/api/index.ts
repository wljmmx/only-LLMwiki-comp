import axios, { type AxiosInstance } from 'axios'
import {
  startLoadingBar,
  finishLoadingBar,
  errorLoadingBar,
} from './loadingBar'

// P1-5: API 版本化 — 通过 VITE_API_BASE_URL 环境变量配置
// 默认 /api/v1，可通过 VITE_API_BASE_URL 覆盖为 /api（向后兼容）
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1'

export function getApiBaseUrl() {
  return API_BASE_URL
}

/**
 * 获取认证 token（S14-3：统一 token 读取入口，避免 'opskg_token' key 散落多处）
 *
 * 所有需要手动注入 Authorization 头的场景（如 fetch SSE）应使用本函数，
 * 而非直接 localStorage.getItem('opskg_token')。
 */
export function getAuthToken(): string | null {
  if (typeof localStorage === 'undefined') return null
  return localStorage.getItem('opskg_token')
}

/** token 存储的 localStorage key（S14-3：单一来源） */
export const AUTH_TOKEN_KEY = 'opskg_token'

const baseConfig = {
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
}

/**
 * 应用请求拦截器：auth token + loading bar
 *
 * 共享给 api / apiRaw 两个实例，确保所有请求都经过统一的认证与进度条处理。
 */
function applyRequestInterceptor(instance: AxiosInstance): void {
  instance.interceptors.request.use(
    (config) => {
      const token = getAuthToken()
      if (token) {
        config.headers.Authorization = `Bearer ${token}`
      }
      // S14-2: 全局 loading bar（每个请求启动进度条）
      startLoadingBar()
      return config
    },
    (error) => {
      // 请求发送失败（如网络错误）也要结束 loading bar
      errorLoadingBar()
      return Promise.reject(error)
    },
  )
}

/**
 * 标准实例：response 拦截器返回 response.data
 *
 * 适用于绝大多数 JSON API 调用（自动解包 data 字段）。
 */
const api = axios.create(baseConfig)
applyRequestInterceptor(api)
api.interceptors.response.use(
  (response) => {
    // S14-2: 请求成功 → 完成 loading bar
    finishLoadingBar()
    return response.data
  },
  (error) => {
    // S14-2: 请求失败 → 错误 loading bar
    errorLoadingBar()
    console.error('API Error:', error.response?.data?.detail || error.message)
    return Promise.reject(error)
  },
)

/**
 * 原始实例：response 拦截器返回完整 AxiosResponse（不解包 data）
 *
 * S14-3：用于需要访问 response.headers 或 responseType: 'blob' 的场景
 * （如 export.ts 导出文档时需从 Content-Disposition 解析文件名）。
 *
 * 共享 auth + loading bar 拦截器，但保留完整响应对象。
 */
const apiRaw = axios.create(baseConfig)
applyRequestInterceptor(apiRaw)
apiRaw.interceptors.response.use(
  (response) => {
    finishLoadingBar()
    return response
  },
  (error) => {
    errorLoadingBar()
    console.error('API Error:', error.response?.data?.detail || error.message)
    return Promise.reject(error)
  },
)

export default api
export { apiRaw }
