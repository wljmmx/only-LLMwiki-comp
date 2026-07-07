import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import ErrorBoundary from './ErrorBoundary.vue'
import '@/test/setup'

// 受 shouldThrow 控制的子组件：抛错以触发 ErrorBoundary 的 onErrorCaptured
const BoomChild = defineComponent({
  name: 'BoomChild',
  props: {
    shouldThrow: { type: Boolean, default: true },
  },
  setup(props) {
    if (props.shouldThrow) {
      throw new Error('测试错误消息')
    }
    return () => h('p', { class: 'normal-content' }, '子组件正常渲染')
  },
})

// 用一个父组件包裹 ErrorBoundary + BoomChild，便于通过 render function 动态控制 shouldThrow
function mountWithBoom(opts: { showStack?: boolean; shouldThrowRef?: ReturnType<typeof ref<boolean>> } = {}) {
  const shouldThrowRef = opts.shouldThrowRef ?? ref(true)
  const Parent = defineComponent({
    components: { ErrorBoundary, BoomChild },
    setup() {
      return () => {
        const ebProps: Record<string, unknown> = {}
        if (opts.showStack !== undefined) ebProps.showStack = opts.showStack
        return h(ErrorBoundary, ebProps, {
          default: () => h(BoomChild, { shouldThrow: shouldThrowRef.value }),
        })
      }
    },
  })
  const wrapper = mount(Parent)
  const eb = wrapper.findComponent(ErrorBoundary)
  return { wrapper, eb, shouldThrowRef }
}

describe('ErrorBoundary.vue', () => {
  beforeEach(() => {
    // naive-ui 在缺少 ConfigProvider 时会 warn，suppress 掉以保持输出整洁
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('无错误时渲染 slot 内容，不显示错误回退 UI', () => {
    const wrapper = mount(ErrorBoundary, {
      slots: { default: '<div class="slot-content">Hello Slot</div>' },
    })
    expect(wrapper.text()).toContain('Hello Slot')
    expect(wrapper.find('.slot-content').exists()).toBe(true)
    expect(wrapper.find('.error-boundary').exists()).toBe(false)
  })

  it('子组件抛错时捕获错误并显示错误回退 UI', async () => {
    const { wrapper, eb } = mountWithBoom()
    // onErrorCaptured 设置 hasError 后，错误 UI 的重新渲染是异步调度的
    await flushPromises()

    expect(eb.emitted('error')).toBeDefined()
    expect(wrapper.find('.error-boundary').exists()).toBe(true)
    expect(wrapper.text()).toContain('测试错误消息')
    expect(wrapper.text()).toContain('重试')
    expect(wrapper.text()).toContain('刷新页面')
  })

  it('重试按钮重置错误状态，恢复 slot 正常内容', async () => {
    const shouldThrowRef = ref(true)
    const { wrapper } = mountWithBoom({ shouldThrowRef })
    await flushPromises()
    expect(wrapper.find('.error-boundary').exists()).toBe(true)

    // 让子组件再次挂载时不再抛错
    shouldThrowRef.value = false

    // 找到 "重试" 按钮并点击（NButton 为真实渲染的 <button>）
    const buttons = wrapper.findAll('button')
    const retry = buttons.find((b) => b.text().includes('重试'))
    expect(retry).toBeTruthy()
    await retry!.trigger('click')
    await flushPromises()

    // 错误 UI 消失，slot 正常内容出现
    expect(wrapper.find('.error-boundary').exists()).toBe(false)
    expect(wrapper.text()).toContain('子组件正常渲染')
  })

  it('showStack=true 时显示错误堆栈区', async () => {
    const { wrapper } = mountWithBoom({ showStack: true })
    await flushPromises()
    expect(wrapper.find('.stack-section').exists()).toBe(true)
    expect(wrapper.text()).toContain('错误堆栈')
  })

  it('showStack=false 时隐藏错误堆栈区', async () => {
    const { wrapper } = mountWithBoom({ showStack: false })
    await flushPromises()
    expect(wrapper.find('.stack-section').exists()).toBe(false)
  })

  it('捕获错误时 emit error 事件并携带 Error', async () => {
    const { eb } = mountWithBoom()
    await flushPromises()
    const errorEvents = eb.emitted('error')
    expect(errorEvents).toBeDefined()
    expect(errorEvents).toHaveLength(1)
    const [err] = errorEvents![0] as [Error, string]
    expect(err).toBeInstanceOf(Error)
    expect(err.message).toBe('测试错误消息')
  })

  it('点击重试时 emit reset 事件', async () => {
    const shouldThrowRef = ref(true)
    const { eb } = mountWithBoom({ shouldThrowRef })
    await flushPromises()

    shouldThrowRef.value = false
    expect(eb.emitted('reset')).toBeUndefined()

    const retry = eb.findAll('button').find((b) => b.text().includes('重试'))
    expect(retry).toBeTruthy()
    await retry!.trigger('click')

    const resetEvents = eb.emitted('reset')
    expect(resetEvents).toBeDefined()
    expect(resetEvents).toHaveLength(1)
  })
})
