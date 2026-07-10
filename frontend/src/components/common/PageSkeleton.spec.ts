import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PageSkeleton from './PageSkeleton.vue'

describe('components/common/PageSkeleton.vue', () => {
  it('默认渲染（header + 1 card）', () => {
    const wrapper = mount(PageSkeleton)
    expect(wrapper.find('.page-skeleton').exists()).toBe(true)
    expect(wrapper.find('.skeleton-header').exists()).toBe(true)
    expect(wrapper.findAll('.skeleton-card')).toHaveLength(1)
  })

  it('header=false 隐藏标题骨架', () => {
    const wrapper = mount(PageSkeleton, { props: { header: false } })
    expect(wrapper.find('.skeleton-header').exists()).toBe(false)
  })

  it('cards prop 控制卡片数量', () => {
    const wrapper = mount(PageSkeleton, { props: { cards: 3 } })
    expect(wrapper.findAll('.skeleton-card')).toHaveLength(3)
  })

  it('cards=0 时不渲染卡片', () => {
    const wrapper = mount(PageSkeleton, { props: { cards: 0, header: false } })
    expect(wrapper.findAll('.skeleton-card')).toHaveLength(0)
    expect(wrapper.find('.skeleton-header').exists()).toBe(false)
  })
})
