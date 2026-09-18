import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import type { HeadingTreeNode } from '@/api/wiki'
import WikiHeadingTree from '@/components/wiki/WikiHeadingTree.vue'

const TREE: HeadingTreeNode[] = [
  { slug: 'overview', title: '概述', level: 1, children: [] },
  {
    slug: 'troubleshoot',
    title: '排查',
    level: 1,
    children: [{ slug: 'trouble-502', title: '502', level: 2, children: [] }],
  },
]

describe('components/wiki/WikiHeadingTree.vue — 章节目录树', () => {
  const globalStubs = { NTree: true, NSkeleton: true, NInput: true, NEmpty: true }

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('渲染标题与节点计数', () => {
    const wrapper = mount(WikiHeadingTree, {
      props: { tree: TREE, loading: false },
      global: { stubs: globalStubs },
    })
    expect(wrapper.text()).toContain('章节目录')
    expect(wrapper.text()).toContain('3 节')
  })

  it('treeData 将节点转换为树选项并展开所有父级 key', () => {
    const wrapper = mount(WikiHeadingTree, {
      props: { tree: TREE, loading: false },
      global: { stubs: globalStubs },
    })
    const vm = wrapper.vm as any
    expect(vm.treeData).toHaveLength(2)
    expect(vm.treeData[1].children).toHaveLength(1)
    expect(vm.expandedKeys).toEqual(['troubleshoot'])
  })

  it('搜索过滤仅保留匹配节点及其父链', async () => {
    const wrapper = mount(WikiHeadingTree, {
      props: { tree: TREE, loading: false },
      global: { stubs: globalStubs },
    })
    const vm = wrapper.vm as any
    vm.searchText = '502'
    await wrapper.vm.$nextTick()
    expect(vm.treeData).toHaveLength(1)
    expect(vm.treeData[0].label).toBe('排查')
    expect(vm.treeData[0].children![0].label).toBe('502')
  })

  it('loading 时展示骨架屏', () => {
    const wrapper = mount(WikiHeadingTree, {
      props: { tree: [], loading: true },
      global: { stubs: globalStubs },
    })
    expect(wrapper.find('.tree-skeleton').exists()).toBe(true)
  })

  it('空树且非 loading 时展示暂无章节', () => {
    const wrapper = mount(WikiHeadingTree, {
      props: { tree: [], loading: false },
      global: { stubs: globalStubs },
    })
    expect(wrapper.text()).toContain('暂无章节')
  })
})