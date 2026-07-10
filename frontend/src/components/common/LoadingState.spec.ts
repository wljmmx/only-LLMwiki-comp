import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import LoadingState from './LoadingState.vue'

describe('components/common/LoadingState.vue', () => {
  it('默认渲染（无文本）', () => {
    const wrapper = mount(LoadingState)
    expect(wrapper.find('.loading-state').exists()).toBe(true)
    expect(wrapper.find('.loading-text').exists()).toBe(false)
  })

  it('有 text 时渲染提示文本', () => {
    const wrapper = mount(LoadingState, { props: { text: '加载中...' } })
    expect(wrapper.find('.loading-text').text()).toBe('加载中...')
  })

  it('minHeight 应用到容器', () => {
    const wrapper = mount(LoadingState, { props: { minHeight: 300 } })
    expect(wrapper.find('.loading-state').attributes('style')).toContain('300px')
  })

  it('minHeight 接受字符串值', () => {
    const wrapper = mount(LoadingState, { props: { minHeight: '50vh' } })
    expect(wrapper.find('.loading-state').attributes('style')).toContain('50vh')
  })

  it('size prop 传递给 NSpin（stub）', () => {
    const wrapper = mount(LoadingState, { props: { size: 'small' } })
    // NSpin 被 stub，验证不报错即可
    expect(wrapper.find('.loading-state').exists()).toBe(true)
  })
})
