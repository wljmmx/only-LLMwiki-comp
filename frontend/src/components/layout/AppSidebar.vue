<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NMenu, NDivider } from 'naive-ui'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { buildMenuOptions } from '@/utils/menuBuilder'
import { useRecentPages } from '@/composables/useRecentPages'
import { getTypeLabel } from '@/utils/format'

const emit = defineEmits<{ navigate: [] }>()

const router = useRouter()
const route = useRoute()
const appStore = useAppStore()
const authStore = useAuthStore()
const { recentPages } = useRecentPages()

/**
 * P1-5: 菜单单一事实源
 *
 * 菜单选项从 router 路由 meta 派生（menuGroup/menuOrder/icon/title），
 * 废弃硬编码 path/emoji。角色过滤与路由守卫一致：
 * - authRequired === true：按 meta.requireRole 过滤
 * - dev 模式（false）/后端不可达（null）：放行
 */
const menuItems = computed(() =>
  buildMenuOptions(
    router.getRoutes() as unknown as { path: string; meta: any }[],
    {
      authRequired: authStore.authRequired,
      userRole: authStore.user?.role,
    },
  ),
)

/** P2-6: 最近访问菜单项（动态，从 localStorage 读取） */
const recentMenuItems = computed(() => {
  if (recentPages.value.length === 0) return []
  return [
    {
      type: 'group' as const,
      label: '最近访问',
      key: 'recent-group',
      children: recentPages.value.slice(0, 5).map((p) => ({
        label: p.title,
        key: `/wiki?slug=${p.slug}`,
        // 用类型标签作为额外提示
        description: getTypeLabel(p.type),
      })),
    },
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
    :options="[...recentMenuItems, ...menuItems]"
    :value="route.path"
    :indent="18"
    @update:value="handleSelect"
  />
</template>
