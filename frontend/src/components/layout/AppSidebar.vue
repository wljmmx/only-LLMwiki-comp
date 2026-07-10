<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NMenu, type MenuOption } from 'naive-ui'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { renderMenuIcon } from '@/utils/icons'

const emit = defineEmits<{ navigate: [] }>()

const router = useRouter()
const route = useRoute()
const appStore = useAppStore()
const authStore = useAuthStore()

/** 是否可访问用户管理：admin 用户，或 dev/legacy 模式 */
const canManageUsers = computed(
  () => authStore.isAdmin || authStore.authRequired === false,
)

const menuItems = computed<MenuOption[]>(() => {
  const systemChildren: MenuOption[] = [
    { label: '模板管理', key: '/templates', icon: renderMenuIcon('templates') },
    { label: '导出中心', key: '/export', icon: renderMenuIcon('export') },
    { label: 'MCP 浏览器', key: '/mcp', icon: renderMenuIcon('mcp') },
  ]
  if (canManageUsers.value) {
    systemChildren.push({ label: '用户管理', key: '/users', icon: renderMenuIcon('users') })
  }

  return [
    { label: '仪表盘', key: '/dashboard', icon: renderMenuIcon('dashboard') },
    {
      label: '知识管理',
      key: 'knowledge',
      children: [
        { label: '文档管理', key: '/documents', icon: renderMenuIcon('documents') },
        { label: '知识搜索', key: '/search', icon: renderMenuIcon('search') },
        { label: 'Wiki 浏览', key: '/wiki', icon: renderMenuIcon('wiki') },
        { label: 'Wiki Q&A', key: '/wiki-query', icon: renderMenuIcon('wiki-query') },
        { label: '知识图谱', key: '/graph', icon: renderMenuIcon('graph') },
      ],
    },
    {
      label: '质量治理',
      key: 'quality',
      children: [
        { label: '健康检查', key: '/wiki-health', icon: renderMenuIcon('health') },
        { label: '审查队列', key: '/review', icon: renderMenuIcon('review') },
        { label: '版本控制', key: '/versions', icon: renderMenuIcon('versions') },
      ],
    },
    {
      label: 'AIOps',
      key: 'aiops',
      children: [
        { label: 'Incident 管理', key: '/incidents', icon: renderMenuIcon('incidents') },
        { label: '变更关联', key: '/changes', icon: renderMenuIcon('changes') },
        { label: '服务拓扑', key: '/topology', icon: renderMenuIcon('topology') },
        { label: 'Runbook 工作台', key: '/runbook', icon: renderMenuIcon('runbook') },
      ],
    },
    { label: '系统工具', key: 'system', children: systemChildren },
  ]
})

function handleSelect(key: string) {
  router.push(key)
  emit('navigate') // 通知父组件（移动端 drawer 关闭）
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
