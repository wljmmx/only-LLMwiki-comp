<script setup lang="ts">
import { h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NMenu, type MenuOption } from 'naive-ui'
import { useAppStore } from '@/stores/app'

const router = useRouter()
const route = useRoute()
const appStore = useAppStore()

const menuItems: MenuOption[] = [
  {
    label: '仪表盘',
    key: '/dashboard',
    icon: () => h('span', { style: 'font-size: 16px' }, '📊'),
  },
  {
    label: '知识管理',
    key: 'knowledge',
    children: [
      {
        label: '文档管理',
        key: '/documents',
        icon: () => h('span', { style: 'font-size: 16px' }, '📄'),
      },
      {
        label: '知识搜索',
        key: '/search',
        icon: () => h('span', { style: 'font-size: 16px' }, '🔍'),
      },
      {
        label: 'Wiki 浏览',
        key: '/wiki',
        icon: () => h('span', { style: 'font-size: 16px' }, '📖'),
      },
      {
        label: 'Wiki Q&A',
        key: '/wiki-query',
        icon: () => h('span', { style: 'font-size: 16px' }, '💬'),
      },
      {
        label: '健康检查',
        key: '/wiki-health',
        icon: () => h('span', { style: 'font-size: 16px' }, '🩺'),
      },
    ],
  },
  {
    label: '系统治理',
    key: 'governance',
    children: [
      {
        label: '审查队列',
        key: '/review',
        icon: () => h('span', { style: 'font-size: 16px' }, '✅'),
      },
      {
        label: '模板管理',
        key: '/templates',
        icon: () => h('span', { style: 'font-size: 16px' }, '📋'),
      },
    ],
  },
  {
    label: 'AIOps',
    key: 'aiops',
    children: [
      {
        label: 'Incident 管理',
        key: '/incidents',
        icon: () => h('span', { style: 'font-size: 16px' }, '🚨'),
      },
      {
        label: '变更关联',
        key: '/changes',
        icon: () => h('span', { style: 'font-size: 16px' }, '🔄'),
      },
      {
        label: '服务拓扑',
        key: '/topology',
        icon: () => h('span', { style: 'font-size: 16px' }, '🕸️'),
      },
      {
        label: 'Runbook 工作台',
        key: '/runbook',
        icon: () => h('span', { style: 'font-size: 16px' }, '🛠️'),
      },
    ],
  },
]

function handleSelect(key: string) {
  router.push(key)
}
</script>

<template>
  <NMenu
    :collapsed="appStore.sidebarCollapsed"
    :collapsed-width="64"
    :collapsed-icon-size="20"
    :options="menuItems"
    :value="route.path"
    :indent="18"
    @update:value="handleSelect"
  />
</template>
