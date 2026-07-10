<script setup lang="ts">
import { h, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NMenu, type MenuOption } from 'naive-ui'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits<{ navigate: [] }>()

const router = useRouter()
const route = useRoute()
const appStore = useAppStore()
const authStore = useAuthStore()

/** 是否可访问用户管理：admin 用户，或 dev/legacy 模式 */
const canManageUsers = computed(
  () => authStore.isAdmin || authStore.authRequired === false,
)

const icon = (emoji: string) => () => h('span', { style: 'font-size: 16px' }, emoji)

const menuItems = computed<MenuOption[]>(() => {
  const systemChildren: MenuOption[] = [
    { label: '模板管理', key: '/templates', icon: icon('📋') },
    { label: '导出中心', key: '/export', icon: icon('📤') },
    { label: 'MCP 浏览器', key: '/mcp', icon: icon('🔌') },
  ]
  if (canManageUsers.value) {
    systemChildren.push({ label: '用户管理', key: '/users', icon: icon('👥') })
  }

  return [
    { label: '仪表盘', key: '/dashboard', icon: icon('📊') },
    {
      label: '知识管理',
      key: 'knowledge',
      children: [
        { label: '文档管理', key: '/documents', icon: icon('📄') },
        { label: '知识搜索', key: '/search', icon: icon('🔍') },
        { label: 'Wiki 浏览', key: '/wiki', icon: icon('📖') },
        { label: 'Wiki Q&A', key: '/wiki-query', icon: icon('💬') },
        { label: '知识图谱', key: '/graph', icon: icon('🕸️') },
      ],
    },
    {
      label: '质量治理',
      key: 'quality',
      children: [
        { label: '健康检查', key: '/wiki-health', icon: icon('🩺') },
        { label: '审查队列', key: '/review', icon: icon('✅') },
        { label: '版本控制', key: '/versions', icon: icon('🕒') },
      ],
    },
    {
      label: 'AIOps',
      key: 'aiops',
      children: [
        { label: 'Incident 管理', key: '/incidents', icon: icon('🚨') },
        { label: '变更关联', key: '/changes', icon: icon('🔄') },
        { label: '服务拓扑', key: '/topology', icon: icon('🔗') },
        { label: 'Runbook 工作台', key: '/runbook', icon: icon('🛠️') },
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
