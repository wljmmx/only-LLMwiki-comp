<script setup lang="ts">
import { NLayout, NLayoutSider, NLayoutHeader, NLayoutContent, NDropdown } from 'naive-ui'
import AppSidebar from './AppSidebar.vue'
import OnboardingTour from '@/components/onboarding/OnboardingTour.vue'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { useOnboardingStore } from '@/stores/onboarding'
import { useSetupStore } from '@/stores/setup'
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const appStore = useAppStore()
const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const onboardingStore = useOnboardingStore()
const setupStore = useSetupStore()
const collapsed = computed(() => appStore.sidebarCollapsed)
const pageTitle = computed(() => route.meta.title || 'OpsKG')

function handleToggle() {
  appStore.toggleSidebar()
}

function toggleDark() {
  appStore.toggleDarkMode()
}

/** 重启引导（用户菜单"重新查看引导"可调用） */
function restartTour() {
  onboardingStore.resetTour()
  onboardingStore.startTour()
}

/** 打开开箱配置向导 */
function openSetupWizard() {
  setupStore.undismiss()
  router.push({ name: 'setup' })
}

/** 注销 */
async function handleLogout() {
  await authStore.logout()
  router.replace('/login')
}

/** 用户菜单选项 */
const userMenuOptions = computed(() => {
  const opts: Array<{ label: string; key: string; props?: { onClick?: () => void } }> = [
    {
      label: authStore.user ? `${authStore.displayName} (${authStore.user.role})` : '匿名用户',
      key: 'info',
    },
  ]
  if (authStore.isAuthenticated) {
    opts.push({ label: '注销', key: 'logout' })
  }
  opts.push({ label: '开箱配置向导', key: 'setup' })
  opts.push({ label: '重新查看引导', key: 'tour' })
  return opts
})

function handleUserMenuSelect(key: string) {
  if (key === 'logout') {
    handleLogout()
  } else if (key === 'tour') {
    restartTour()
  } else if (key === 'setup') {
    openSetupWizard()
  }
}

defineExpose({ restartTour, openSetupWizard })

// 首次访问自动启动 tour
onMounted(() => {
  onboardingStore.autoStartIfNeeded()
  // 若 store 有 token 但未加载用户（如刷新页面），加载一次
  if (authStore.token && !authStore.user) {
    authStore.fetchMe()
  }
})
</script>

<template>
  <NLayout style="height: 100vh">
    <NLayoutSider
      :collapsed="collapsed"
      :collapsed-width="64"
      :width="240"
      show-trigger
      @update-collapsed="handleToggle"
    >
      <div class="logo">
        <span class="logo-icon">📚</span>
        <span v-if="!collapsed" class="logo-text">OpsKG</span>
      </div>
      <AppSidebar />
    </NLayoutSider>

    <NLayout>
      <NLayoutHeader bordered class="header">
        <div class="header-left">
          <span class="page-title">{{ pageTitle }}</span>
        </div>
        <div class="header-right">
          <button class="icon-btn" title="切换主题" @click="toggleDark">
            {{ appStore.darkMode ? '☀️' : '🌙' }}
          </button>
          <NDropdown
            :options="userMenuOptions"
            trigger="click"
            @select="handleUserMenuSelect"
          >
            <span class="user-avatar" :title="authStore.displayName">
              {{ authStore.user ? '👤' : '👥' }}
              <span v-if="authStore.user" class="user-name">{{ authStore.displayName }}</span>
            </span>
          </NDropdown>
        </div>
      </NLayoutHeader>

      <NLayoutContent class="content">
        <router-view />
      </NLayoutContent>
    </NLayout>
  </NLayout>
  <!-- Onboarding 引导 tour（首次访问自动启动） -->
  <OnboardingTour />
</template>

<style scoped>
.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px;
  font-size: 18px;
  font-weight: 600;
  border-bottom: 1px solid var(--n-border-color, #e5e7eb);
  height: 56px;
  box-sizing: border-box;
}
.logo-icon {
  font-size: 20px;
}
.logo-text {
  color: var(--n-text-color, #111827);
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 56px;
}
.page-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}
.icon-btn {
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 18px;
  padding: 4px 8px;
  border-radius: 6px;
}
.icon-btn:hover {
  background: var(--n-item-color-hover, #f3f4f6);
}
.user-avatar {
  font-size: 20px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-radius: 6px;
}
.user-avatar:hover {
  background: var(--n-item-color-hover, #f3f4f6);
}
.user-name {
  font-size: 13px;
  color: var(--n-text-color-2, #374151);
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.content {
  padding: 24px;
  overflow-y: auto;
}
</style>
