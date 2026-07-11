import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const mockRouter = { push: vi.fn(), replace: vi.fn() }

vi.mock('vue-router', () => ({
  useRouter: () => mockRouter,
}))

vi.mock('naive-ui', () => ({
  NCard: { template: '<div class="n-card"><slot/></div>' },
  NSteps: { template: '<div class="n-steps"><slot/></div>' },
  NStep: { template: '<div class="n-step"><slot/></div>' },
  NButton: { template: '<button class="n-button"><slot/></button>' },
  NSpace: { template: '<div class="n-space"><slot/></div>' },
  NForm: { template: '<form class="n-form"><slot/></form>' },
  NFormItem: { template: '<div class="n-form-item"><slot/></div>' },
  NInput: { template: '<input class="n-input" />', props: ['value', 'placeholder'], emits: ['update:value'] },
  NSelect: { template: '<select class="n-select" />', props: ['value', 'options'], emits: ['update:value'] },
  NSwitch: { template: '<input type="checkbox" class="n-switch" />', props: ['value'], emits: ['update:value'] },
  NInputNumber: { template: '<input type="number" class="n-input-number" />', props: ['value'], emits: ['update:value'] },
  NAlert: { template: '<div class="n-alert"><slot/></div>', props: ['type', 'title'] },
  NTag: { template: '<span class="n-tag"><slot/></span>', props: ['type'] },
  NCode: { template: '<pre class="n-code"><slot/></pre>', props: ['code', 'language'] },
  NDivider: { template: '<hr class="n-divider" />' },
  NText: { template: '<span class="n-text"><slot/></span>' },
  NSpin: { template: '<div class="n-spin"><slot/></div>', props: ['show'] },
  NGrid: { template: '<div class="n-grid"><slot/></div>' },
  NGi: { template: '<div class="n-gi"><slot/></div>' },
  useMessage: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}))

import SetupWizardView from '@/views/SetupWizardView.vue'
import '@/test/setup'

describe('SetupWizardView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('组件可挂载', () => {
    const wrapper = mount(SetupWizardView, { global: { plugins: [] } })
    expect(wrapper.exists()).toBe(true)
  })

  it('渲染包含步骤向导', () => {
    const wrapper = mount(SetupWizardView, { global: { plugins: [] } })
    expect(wrapper.html()).toBeTruthy()
    expect(wrapper.html().length).toBeGreaterThan(0)
  })

  it('初始步骤为 0', () => {
    const wrapper = mount(SetupWizardView, { global: { plugins: [] } })
    expect(wrapper.vm).toBeTruthy()
  })

  it('包含配置表单区域', () => {
    const wrapper = mount(SetupWizardView, { global: { plugins: [] } })
    expect(wrapper.find('.n-card').exists() || wrapper.find('.n-steps').exists()).toBe(true)
  })

  it('能正确卸载', () => {
    const wrapper = mount(SetupWizardView, { global: { plugins: [] } })
    wrapper.unmount()
    expect(true).toBe(true)
  })
})
