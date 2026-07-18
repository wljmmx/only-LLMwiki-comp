/**
 * useAsyncData — 统一管理异步请求的 loading / error / data 三态
 *
 * 特性：
 * - 竞态处理：多次调用 execute 时，仅最后一次的结果生效
 * - 支持 onSuccess / onError 回调
 * - 支持 immediate 选项（默认 false，设为 true 时立即执行）
 */
import { ref, type Ref } from 'vue'

export interface UseAsyncDataOptions<T> {
  /** 是否在创建时立即执行（默认 false） */
  immediate?: boolean
  /** 请求成功回调 */
  onSuccess?: (data: T) => void
  /** 请求失败回调 */
  onError?: (error: Error) => void
}

export function useAsyncData<T>(
  fn: () => Promise<T>,
  options: UseAsyncDataOptions<T> = {},
): {
  data: Ref<T | null>
  loading: Ref<boolean>
  error: Ref<string | null>
  execute: () => Promise<void>
  refresh: () => Promise<void>
} {
  const data = ref<T | null>(null) as Ref<T | null>
  const loading = ref(false)
  const error = ref<string | null>(null)

  let generation = 0

  async function execute(): Promise<void> {
    const currentGen = ++generation
    loading.value = true
    error.value = null

    try {
      const result = await fn()
      // 竞态处理：仅当 generation 未变时生效
      if (currentGen !== generation) return
      data.value = result
      if (options.onSuccess) {
        options.onSuccess(result)
      }
    } catch (e: unknown) {
      if (currentGen !== generation) return
      const message = e instanceof Error ? e.message : String(e)
      error.value = message
      if (options.onError) {
        options.onError(e instanceof Error ? e : new Error(message))
      }
    } finally {
      if (currentGen === generation) {
        loading.value = false
      }
    }
  }

  const refresh = execute

  if (options.immediate) {
    execute()
  }

  return { data, loading, error, execute, refresh }
}