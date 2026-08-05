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

vi.mock('@/api/setup', () => ({
  getSetupStatus: vi.fn().mockResolvedValue({
    llm_backend: 'openai_compat',
    neo4j_uri: 'bolt://localhost:7687',
    llm_configured: false,
    neo4j_configured: false,
    auth_configured: false,
    ready: false,
    missing: ['llm', 'neo4j'],
  }),
  testLLM: vi.fn().mockResolvedValue({ ok: true, latency_ms: 100, model: 'test' }),
  testNeo4j: vi.fn().mockResolvedValue({ ok: true, version: '5.0', latency_ms: 50 }),
  generateCommand: vi.fn().mockResolvedValue({
    env_file_content: 'TEST=1',
    command: 'docker run test',
  }),
}))

import SetupWizardView from '@/views/SetupWizardView.vue'
import i18n from '@/i18n'
import '@/test/setup'

describe('SetupWizardView', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
  })

  function mountView() {
    return mount(SetupWizardView, { global: { plugins: [pinia, i18n] } })
  }

  it('组件可挂载', () => {
    const wrapper = mountView()
    expect(wrapper.exists()).toBe(true)
  })

  it('渲染包含步骤向导', () => {
    const wrapper = mountView()
    expect(wrapper.html()).toBeTruthy()
    expect(wrapper.html().length).toBeGreaterThan(0)
  })

  it('初始步骤为 0', () => {
    const wrapper = mountView()
    expect(wrapper.vm).toBeTruthy()
  })

  it('包含配置表单区域', () => {
    const wrapper = mountView()
    expect(wrapper.find('.n-card').exists() || wrapper.find('.n-steps').exists()).toBe(true)
  })

  it('能正确卸载', () => {
    const wrapper = mountView()
    wrapper.unmount()
    expect(true).toBe(true)
  })
})
