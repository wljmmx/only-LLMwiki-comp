/**
 * useAsyncList — 基于 useAsyncData，专门处理列表场景
 *
 * 在 useAsyncData 基础上增加：
 * - pagination { page, pageSize, total, setPage, setPageSize }
 * - 支持 search / filter 参数
 */
import { ref, type Ref } from 'vue'
import { useAsyncData, type UseAsyncDataOptions } from './useAsyncData'

export interface PaginationState {
  page: Ref<number>
  pageSize: Ref<number>
  total: Ref<number>
  setPage: (page: number) => void
  setPageSize: (size: number) => void
}

export interface UseAsyncListOptions<T> extends UseAsyncDataOptions<T[]> {
  /** 默认每页条数（默认 20） */
  defaultPageSize?: number
}

export interface AsyncListParams {
  page?: number
  pageSize?: number
  search?: string
  filter?: Record<string, unknown>
}

export function useAsyncList<T>(
  fn: (params: AsyncListParams) => Promise<{ items: T[]; total: number }>,
  options: UseAsyncListOptions<T> = {},
): {
  data: Ref<T[] | null>
  loading: Ref<boolean>
  error: Ref<string | null>
  execute: (params?: AsyncListParams) => Promise<void>
  refresh: () => Promise<void>
  pagination: PaginationState
} {
  const pageSize = ref(options.defaultPageSize ?? 20)
  const page = ref(1)
  const total = ref(0)

  let currentParams: AsyncListParams = { page: page.value, pageSize: pageSize.value }

  const wrappedFn = async (): Promise<T[]> => {
    const result = await fn(currentParams)
    total.value = result.total
    return result.items
  }

  const { data, loading, error, refresh } = useAsyncData(wrappedFn, {
    immediate: options.immediate,
    onSuccess: options.onSuccess,
    onError: options.onError,
  })

  async function execute(params?: AsyncListParams): Promise<void> {
    if (params) {
      currentParams = params
      if (params.page !== undefined) page.value = params.page
      if (params.pageSize !== undefined) pageSize.value = params.pageSize
    } else {
      currentParams = { page: page.value, pageSize: pageSize.value }
    }
    await refresh()
  }

  function setPage(p: number): void {
    page.value = p
    currentParams = { ...currentParams, page: p }
  }

  function setPageSize(size: number): void {
    pageSize.value = size
    page.value = 1
    currentParams = { ...currentParams, pageSize: size, page: 1 }
  }

  return {
    data,
    loading,
    error,
    execute,
    refresh,
    pagination: { page, pageSize, total, setPage, setPageSize },
  }
}