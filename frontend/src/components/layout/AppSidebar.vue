<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NMenu } from 'naive-ui'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { buildMenuOptions } from '@/utils/menuBuilder'

const emit = defineEmits<{ navigate: [] }>()

const router = useRouter()
const route = useRoute()
const appStore = useAppStore()
const authStore = useAuthStore()

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
