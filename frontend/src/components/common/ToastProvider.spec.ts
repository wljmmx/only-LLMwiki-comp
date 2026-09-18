/**
 * ToastProvider 单元测试
 *
 * 测试覆盖：
 * 1. useToast() 返回 5 种通知方法（success/error/warning/info/loading）
 * 2. 每种方法调用对应的 naive-ui message 且合并默认选项
 * 3. error 默认 duration=5000，loading 默认 duration=0
 * 4. 自定义 options 覆盖默认值
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'

const mockMessage = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
  loading: vi.fn(),
}

// mock 全局组件，因为 <script setup> 无 props/不渲染，直接测试 composable
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return { ...actual, useMessage: () => mockMessage }
})

import { useToast } from './ToastProvider.vue'

describe('components/common/ToastProvider.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('useToast 返回完整的方法集合', () => {
    const toast = useToast()
    expect(typeof toast.success).toBe('function')
    expect(typeof toast.error).toBe('function')
    expect(typeof toast.warning).toBe('function')
    expect(typeof toast.info).toBe('function')
    expect(typeof toast.loading).toBe('function')
  })

  it('success 调用 message.success 并携带默认选项', () => {
    useToast().success('操作成功')
    expect(mockMessage.success).toHaveBeenCalledWith('操作成功', {
      duration: 3000,
      closable: true,
      keepAliveOnHover: true,
    })
  })

  it('error 默认 duration=5000', () => {
    useToast().error('操作失败')
    expect(mockMessage.error).toHaveBeenCalledWith('操作失败', {
      duration: 5000,
      closable: true,
      keepAliveOnHover: true,
    })
  })

  it('warning 调用 message.warning 并携带默认选项', () => {
    useToast().warning('请注意')
    expect(mockMessage.warning).toHaveBeenCalledWith('请注意', {
      duration: 3000,
      closable: true,
      keepAliveOnHover: true,
    })
  })

  it('info 调用 message.info 并携带默认选项', () => {
    useToast().info('提示信息')
    expect(mockMessage.info).toHaveBeenCalledWith('提示信息', {
      duration: 3000,
      closable: true,
      keepAliveOnHover: true,
    })
  })

  it('loading 默认 duration=0', () => {
    useToast().loading('处理中')
    expect(mockMessage.loading).toHaveBeenCalledWith('处理中', {
      duration: 0,
      closable: true,
      keepAliveOnHover: true,
    })
  })

  it('自定义 options 覆盖默认值', () => {
    useToast().success('覆盖', { duration: 1000, closable: false })
    expect(mockMessage.success).toHaveBeenCalledWith('覆盖', {
      duration: 1000,
      closable: false,
      keepAliveOnHover: true,
    })
  })
})