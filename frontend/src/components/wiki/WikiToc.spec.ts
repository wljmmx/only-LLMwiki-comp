import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import WikiToc from '@/components/wiki/WikiToc.vue'

describe('components/wiki/WikiToc.vue — P1-6 目录大纲', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  function makeContentEl(html: string) {
    const div = document.createElement('div')
    div.innerHTML = html
    document.body.appendChild(div)
    return div
  }

  it('≥2 个标题时渲染目录 nav，并展示标题文本', async () => {
    const el = makeContentEl('<h2 id="a">第一节</h2><h3>第二节</h3>')
    const wrapper = mount(WikiToc, { props: { contentEl: el, pageKey: 'p' } })
    await flushPromises()
    const nav = wrapper.find('nav.wiki-toc')
    expect(nav.exists()).toBe(true)
    expect(nav.text()).toContain('目录')
    expect(nav.text()).toContain('第一节')
    expect(nav.text()).toContain('第二节')
    // 无 id 的标题被分配 id
    expect(el.querySelector('h3')!.id).toBe('wiki-heading-1')
  })

  it('标题 <2 个时不渲染 nav', async () => {
    const el = makeContentEl('<h2 id="a">仅一个</h2>')
    const wrapper = mount(WikiToc, { props: { contentEl: el, pageKey: 'p' } })
    await flushPromises()
    expect(wrapper.find('nav.wiki-toc').exists()).toBe(false)
  })

  it('contentEl 为 null 时无标题', async () => {
    const wrapper = mount(WikiToc, { props: { contentEl: null, pageKey: 'p' } })
    await flushPromises()
    expect(wrapper.find('nav.wiki-toc').exists()).toBe(false)
  })

  it('点击标题 button 触发 scrollIntoView 并将该项标为 active', async () => {
    const el = makeContentEl('<h2 id="sec-a">A</h2><h2 id="sec-b">B</h2>')
    const scrollIntoView = vi.fn()
    const target = el.querySelector('#sec-b')!
    Object.defineProperty(target, 'scrollIntoView', { value: scrollIntoView, configurable: true })
    const wrapper = mount(WikiToc, { props: { contentEl: el, pageKey: 'p' } })
    await flushPromises()

    const buttons = wrapper.findAll('button.toc-link')
    expect(buttons).toHaveLength(2)
    await buttons[1].trigger('click')
    expect(scrollIntoView).toHaveBeenCalled()
    const vm = wrapper.vm as any
    expect(vm.activeId).toBe('sec-b')
  })
})