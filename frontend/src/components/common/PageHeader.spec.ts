import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PageHeader from './PageHeader.vue'

describe('components/common/PageHeader.vue', () => {
  it('渲染标题', () => {
    const wrapper = mount(PageHeader, { props: { title: '仪表盘' } })
    expect(wrapper.text()).toContain('仪表盘')
    expect(wrapper.find('h2').text()).toBe('仪表盘')
  })

  it('有 description 时渲染副标题', () => {
    const wrapper = mount(PageHeader, {
      props: { title: '仪表盘', description: '系统总览' },
    })
    expect(wrapper.find('p').text()).toBe('系统总览')
  })

  it('无 description 时不渲染副标题', () => {
    const wrapper = mount(PageHeader, { props: { title: '仪表盘' } })
    expect(wrapper.find('p').exists()).toBe(false)
  })

  it('actions 插槽渲染到右侧', () => {
    const wrapper = mount(PageHeader, {
      props: { title: '文档' },
      slots: { actions: '<button class="test-btn">上传</button>' },
    })
    expect(wrapper.find('.page-header-actions').exists()).toBe(true)
    expect(wrapper.find('.test-btn').exists()).toBe(true)
  })

  it('无 actions 插槽时不渲染操作区', () => {
    const wrapper = mount(PageHeader, { props: { title: '文档' } })
    expect(wrapper.find('.page-header-actions').exists()).toBe(false)
  })
})
