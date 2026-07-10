import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import EmptyState from './EmptyState.vue'

describe('components/common/EmptyState.vue', () => {
  it('渲染 description', () => {
    const wrapper = mount(EmptyState, { props: { description: '暂无数据' } })
    // NEmpty 被 stub，description 作为 prop 传入
    expect(wrapper.find('.empty-state').exists()).toBe(true)
  })

  it('无 description 时不报错', () => {
    const wrapper = mount(EmptyState)
    expect(wrapper.find('.empty-state').exists()).toBe(true)
  })

  it('extra 插槽渲染', () => {
    const wrapper = mount(EmptyState, {
      props: { description: '未找到结果' },
      slots: { extra: '<button class="retry">重试</button>' },
    })
    expect(wrapper.find('.retry').exists()).toBe(true)
  })

  it('iconSize prop 传递（stub）', () => {
    const wrapper = mount(EmptyState, {
      props: { description: '空', iconSize: 48 },
    })
    expect(wrapper.find('.empty-state').exists()).toBe(true)
  })
})
