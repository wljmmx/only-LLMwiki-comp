import axios, {
  type AxiosInstance,
  type AxiosError,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
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

// ========================================================================
// 请求去重（Request Dedup）
// ========================================================================

interface DedupEntry {
  promise: Promise<AxiosResponse>
  resolve: (value: AxiosResponse) => void
  reject: (reason?: unknown) => void
}

/** 进行中的请求映射表：key → deferred promise */
const pendingRequests = new Map<string, DedupEntry>()

/** 根据请求配置生成唯一 key */
function getRequestKey(config: InternalAxiosRequestConfig): string {
  const { method, url, params, data } = config
  return `${method}:${url}:${JSON.stringify(params)}:${JSON.stringify(data)}`
}

/** 判断是否应跳过去重（blob 下载、文件上传等不应去重） */
function shouldSkipDedup(config: InternalAxiosRequestConfig): boolean {
  if (config.responseType === 'blob') return true
  if (config.data instanceof FormData) return true
  const ct = config.headers?.['Content-Type'] as string | undefined
  if (ct && ct.includes('multipart/form-data')) return true
  return false
}

// ========================================================================
// 指数退避重试（Exponential Backoff Retry）
// ========================================================================

/** 最大重试次数 */
const MAX_RETRIES = 2
/** 基础退避延迟（ms） */
const BASE_DELAY = 500

/** 判断错误是否应重试：仅 5xx 服务器错误和网络错误 */
function shouldRetry(error: AxiosError): boolean {
  if (!error.response) return true // 网络错误（无响应）
  const status = error.response.status
  return status >= 500 && status < 600
}

/** 计算指数退避延迟：BASE_DELAY * 2^retryCount */
function getRetryDelay(retryCount: number): number {
  return BASE_DELAY * Math.pow(2, retryCount)
}

// ========================================================================
// 统一 401 处理（P0-7: 去重锁防止并发 401 竞态）
// ========================================================================

/** 防止多个 401 响应同时触发 redirect 的竞态锁 */
let _unauthorizedHandling = false

function handleUnauthorized(): void {
  if (_unauthorizedHandling) return
  _unauthorizedHandling = true
  try {
    localStorage.removeItem(AUTH_TOKEN_KEY)
    if (window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
  } finally {
    // 重置锁（如果 redirect 未生效，允许后续 401 再次触发）
    setTimeout(() => {
      _unauthorizedHandling = false
    }, 1000)
  }
}

// ========================================================================
// 拦截器工厂
// ========================================================================

/** 扩展 config 字段（用于去重 key 和重试计数） */
interface ExtendedConfig extends InternalAxiosRequestConfig {
  __dedupKey?: string
  __retryCount?: number
}

/**
 * 应用请求拦截器：auth token + loading bar + 请求去重
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

      // 请求去重：相同 URL + method + params + data 的并发请求共享同一个 Promise
      if (!shouldSkipDedup(config)) {
        const key = getRequestKey(config)
        if (pendingRequests.has(key)) {
          // 已有相同请求进行中 → 复用其 Promise
          config.adapter = () => pendingRequests.get(key)!.promise
          return config
        }
        // 创建 deferred promise，等待响应拦截器 resolve
        let resolve!: (value: AxiosResponse) => void
        let reject!: (reason?: unknown) => void
        const promise = new Promise<AxiosResponse>((res, rej) => {
          resolve = res
          reject = rej
        })
        pendingRequests.set(key, { promise, resolve, reject })
        ;(config as ExtendedConfig).__dedupKey = key
      }

      return config
    },
    (error) => {
      errorLoadingBar()
      return Promise.reject(error)
    },
  )
}

/**
 * 应用响应拦截器：loading bar 收尾 + 去重 deferred 结算 + 401 处理 + 指数退避重试
 *
 * @param raw  — true 时返回完整 AxiosResponse（apiRaw），false 时返回 response.data（api）
 */
function applyResponseInterceptor(
  instance: AxiosInstance,
  raw: boolean,
): void {
  instance.interceptors.response.use(
    (response) => {
      finishLoadingBar()

      // 结算去重 deferred
      const key = (response.config as ExtendedConfig).__dedupKey
      if (key) {
        const entry = pendingRequests.get(key)
        if (entry) {
          entry.resolve(response)
          pendingRequests.delete(key)
        }
      }

      return raw ? response : response.data
    },
    async (error: AxiosError) => {
      errorLoadingBar()

      // 401 统一处理
      if (error.response?.status === 401) {
        handleUnauthorized()
      }

      const config = error.config as ExtendedConfig | undefined
      const key = config?.__dedupKey
      const retryCount = config?.__retryCount ?? 0

      // 指数退避重试（仅 5xx / 网络错误，最多 2 次）
      if (config && shouldRetry(error) && retryCount < MAX_RETRIES) {
        config.__retryCount = retryCount + 1

        // 保存旧 deferred（用于后续结算），然后从去重 map 中移除当前 key
        const oldEntry = key ? pendingRequests.get(key) : undefined
        if (key) {
          pendingRequests.delete(key)
          delete config.__dedupKey
        }

        const delay = getRetryDelay(retryCount)
        await new Promise<void>((r) => setTimeout(r, delay))

        try {
          // 重试请求：instance.request 会再次经过请求/响应拦截器
          const result = await instance.request(config)
          // 结算旧 deferred：raw 模式下 result 是 AxiosResponse，否则需构造
          if (oldEntry) {
            oldEntry.resolve(
              raw
                ? (result as AxiosResponse)
                : ({
                    data: result,
                    status: 200,
                    statusText: 'OK',
                    headers: {} as Record<string, string>,
                    config,
                    request: {},
                  } as AxiosResponse),
            )
          }
          return result
        } catch (finalError) {
          oldEntry?.reject(finalError)
          return Promise.reject(finalError)
        }
      }

      // 不重试或重试次数耗尽 → 结算去重 deferred（reject）
      if (key) {
        const entry = pendingRequests.get(key)
        if (entry) {
          entry.reject(error)
          pendingRequests.delete(key)
        }
      }

      console.error(
        'API Error:',
        error.response?.data?.detail || error.message,
      )
      return Promise.reject(error)
    },
  )
}

// ========================================================================
// 实例创建
// ========================================================================

/**
 * 标准实例：response 拦截器返回 response.data
 *
 * 适用于绝大多数 JSON API 调用（自动解包 data 字段）。
 */
const api = axios.create(baseConfig)
applyRequestInterceptor(api)
applyResponseInterceptor(api, false)

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
applyResponseInterceptor(apiRaw, true)

export default api
export { apiRaw }
