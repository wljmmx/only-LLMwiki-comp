import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

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

vi.mock('@/api/settings', () => ({
  getSettings: vi.fn(),
  updateSettings: vi.fn(),
  validateSettings: vi.fn(),
  restartService: vi.fn(),
}))

import {
  getSettings,
  updateSettings,
  validateSettings,
  restartService,
} from '@/api/settings'
import SettingsView from '@/views/SettingsView.vue'
import '@/test/setup'

const sampleSettings = {
  groups: {
    llm: {
      label: 'LLM 配置',
      items: {
        api_key: {
          value: '',
          meta: {
            type: 'string',
            label: 'API Key',
            description: 'LLM 服务密钥',
            sensitive: true,
          },
        },
        model: {
          value: 'gpt-4',
          meta: {
            type: 'select',
            label: '模型',
            description: '选择模型',
            options: ['gpt-4', 'gpt-3.5-turbo'],
          },
        },
        temperature: {
          value: 0.7,
          meta: {
            type: 'float',
            label: 'Temperature',
            description: '采样温度',
            range: [0, 2],
          },
        },
        max_tokens: {
          value: 4096,
          meta: {
            type: 'int',
            label: 'Max Tokens',
            description: '最大 token 数',
            range: [1, 32768],
          },
        },
        enabled: {
          value: true,
          meta: {
            type: 'bool',
            label: '启用',
            description: '是否启用',
          },
        },
      },
    },
  },
}

describe('SettingsView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    ;(getSettings as any).mockResolvedValue(sampleSettings)
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView() {
    return mount(SettingsView, {
      global: { plugins: [pinia] },
    })
  }

  it('onMounted 加载配置', async () => {
    mountView()
    await flushPromises()
    expect(getSettings).toHaveBeenCalled()
  })

  it('加载中显示 NSpin', () => {
    const wrapper = mountView()
    expect(wrapper.find('.loading-card').exists()).toBe(true)
  })

  it('加载成功后显示配置表单', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.loading-card').exists()).toBe(false)
    expect(wrapper.text()).toContain('LLM 配置')
    expect(wrapper.text()).toContain('API Key')
  })

  it('加载失败显示错误信息 + 重试按钮', async () => {
    ;(getSettings as any).mockRejectedValue({ message: 'Network Error' })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.error-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('加载失败')
  })

  it('点击重试重新加载配置', async () => {
    ;(getSettings as any).mockRejectedValueOnce({ message: 'Network Error' })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.find('.error-card').exists()).toBe(true)
    ;(getSettings as any).mockResolvedValue(sampleSettings)
    await wrapper.vm.$nextTick()
    const retryBtn = wrapper.find('.error-card button')
    await retryBtn.trigger('click')
    await flushPromises()
    expect(getSettings).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.loading-card').exists()).toBe(false)
  })

  it('setFieldValue 记录变更', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.setFieldValue('llm', 'model', 'gpt-3.5-turbo')
    expect(vm.pendingChanges.model).toBe('gpt-3.5-turbo')
    expect(vm.groups.llm.items.model.value).toBe('gpt-3.5-turbo')
  })

  it('handleValidate 无变更时提示', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleValidate()
    expect(mockMessage.info).toHaveBeenCalledWith('没有待验证的变更')
  })

  it('handleValidate 验证通过', async () => {
    ;(validateSettings as any).mockResolvedValue({ valid: true, errors: [] })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.setFieldValue('llm', 'model', 'gpt-4')
    await vm.handleValidate()
    expect(validateSettings).toHaveBeenCalled()
    expect(mockMessage.success).toHaveBeenCalledWith('配置验证通过')
  })

  it('handleValidate 验证失败', async () => {
    ;(validateSettings as any).mockResolvedValue({
      valid: false,
      errors: ['无效的模型'],
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.setFieldValue('llm', 'model', 'invalid')
    await vm.handleValidate()
    expect(mockMessage.error).toHaveBeenCalledWith(expect.stringContaining('无效的模型'))
  })

  it('handleSave 无变更时提示', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleSave()
    expect(mockMessage.info).toHaveBeenCalledWith('没有待保存的变更')
  })

  it('handleSave 保存成功后显示重启对话框', async () => {
    ;(updateSettings as any).mockResolvedValue({
      updated: ['model'],
      message: '已保存',
      restart_endpoint: '/settings/restart',
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.setFieldValue('llm', 'model', 'gpt-4')
    await vm.handleSave()
    expect(updateSettings).toHaveBeenCalled()
    expect(mockMessage.success).toHaveBeenCalled()
    expect(vm.showRestartModal).toBe(true)
  })

  it('handleRestart 重启成功', async () => {
    ;(restartService as any).mockResolvedValue({
      restart: true,
      message: '重启中',
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.showRestartModal = true
    await vm.handleRestart()
    expect(restartService).toHaveBeenCalled()
    expect(mockMessage.success).toHaveBeenCalledWith('重启中')
    expect(vm.showRestartModal).toBe(false)
  })
})
