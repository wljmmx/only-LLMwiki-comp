import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import type { WikiPage } from '@/types/api'
import WikiSidebar from '@/components/wiki/WikiSidebar.vue'

const PAGES = [
  { slug: 'service-nginx', title: 'Nginx', type: 'service', tags: ['nginx'], updated_at: '2026-01-02T00:00:00Z' },
  { slug: 'concept-proxy', title: '反向代理', type: 'concept', tags: [], updated_at: '2026-01-01T00:00:00Z' },
  { slug: 'host-web1', title: 'Web1', type: 'host', tags: ['host'], updated_at: '2026-01-03T00:00:00Z' },
] as unknown as WikiPage[]

const globalStubs = {
  NTree: true,
  NCard: true,
  NSpace: true,
  NInput: true,
  NSelect: true,
  NSkeleton: true,
  NButton: true,
  NEmpty: true,
}

describe('components/wiki/WikiSidebar.vue — 侧栏页面树', () => {
  function mountSidebar(props: Record<string, unknown> = {}) {
    return mount(WikiSidebar, {
      props: {
        pages: PAGES,
        treeLoading: false,
        selectedKey: null,
        treeSearchText: '',
        ...props,
      } as any,
      global: { stubs: globalStubs },
    })
  }

  it('type 分组：treeData 含 type-{type} 父节点，计数正确', () => {
    const wrapper = mountSidebar()
    const vm = wrapper.vm as any
    const labels = vm.treeData.map((n: any) => n.label)
    expect(labels).toContain('服务 (1)')
    expect(labels).toContain('概念 (1)')
    expect(labels).toContain('主机 (1)')
  })

  it('展示总页数与各类型计数', () => {
    const wrapper = mountSidebar()
    expect(wrapper.text()).toContain('3 页')
    expect(wrapper.text()).toContain('概念 1')
    expect(wrapper.text()).toContain('服务 1')
    // 计数为 0 的类型（如实体）不渲染
    expect(wrapper.text()).not.toContain('实体 0')
  })

  it('初始展开全部 type 组', () => {
    const wrapper = mountSidebar()
    const vm = wrapper.vm as any
    expect(vm.allExpanded).toBe(true)
    expect(vm.expandedKeys).toEqual([
      'type-entity',
      'type-concept',
      'type-incident',
      'type-runbook',
      'type-service',
      'type-host',
    ])
  })

  it('展开/折叠切换清空 expandedKeys', async () => {
    const wrapper = mountSidebar()
    const vm = wrapper.vm as any
    vm.toggleExpandAll()
    await wrapper.vm.$nextTick()
    expect(vm.expandedKeys).toEqual([])
    expect(vm.allExpanded).toBe(false)
    vm.toggleExpandAll()
    await wrapper.vm.$nextTick()
    expect(vm.allExpanded).toBe(true)
  })

  it('日期排序下按搜索过滤出叶子节点', async () => {
    const wrapper = mountSidebar({ treeSearchText: 'nginx' })
    const vm = wrapper.vm as any
    vm.sortBy = 'date' // 切到扁平列表模式
    await wrapper.vm.$nextTick()
    expect(vm.treeData).toHaveLength(1)
    expect(vm.treeData[0].label).toBe('Nginx')
  })

  it('type 分组下搜索仅保留匹配组的子节点', async () => {
    const wrapper = mountSidebar({ treeSearchText: 'nginx' })
    const vm = wrapper.vm as any
    vm.sortBy = 'type'
    await wrapper.vm.$nextTick()
    expect(vm.treeData).toHaveLength(1)
    expect(vm.treeData[0].label).toBe('服务 (1)')
    expect(vm.treeData[0].children![0].label).toBe('Nginx')
  })

  it('handleSelect 忽略 type- 前缀 key，仅对页面 key 发 select', () => {
    const wrapper = mountSidebar()
    const vm = wrapper.vm as any
    vm.handleSelect(['type-service'])
    expect(wrapper.emitted('select')).toBeUndefined()
    vm.handleSelect(['service-nginx'])
    expect(wrapper.emitted('select')![0]).toEqual(['service-nginx'])
  })
})