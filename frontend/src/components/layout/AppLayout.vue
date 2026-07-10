<script setup lang="ts">
import {
  NLayout,
  NLayoutSider,
  NLayoutHeader,
  NLayoutContent,
  NDropdown,
  NButton,
  NDrawer,
  NBreadcrumb,
  NBreadcrumbItem,
} from 'naive-ui'
import AppSidebar from './AppSidebar.vue'
import OnboardingTour from '@/components/onboarding/OnboardingTour.vue'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { useOnboardingStore } from '@/stores/onboarding'
import { useSetupStore } from '@/stores/setup'
import { computed, onMounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const appStore = useAppStore()
const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const onboardingStore = useOnboardingStore()
const setupStore = useSetupStore()
const collapsed = computed(() => appStore.sidebarCollapsed)
const pageTitle = computed(() => (route.meta.title as string) || 'OpsKG')

// P0-3: 是否移动端 drawer 模式
const isMobile = computed(() => appStore.viewport === 'mobile')

// P1-3: 面包屑（基于路由 matched + 菜单分组映射）
const breadcrumbs = computed(() => {
  const crumbs: { title: string; path?: string }[] = []
  // 从路由 matched 构建
  for (const r of route.matched) {
    if (r.meta?.title) {
      crumbs.push({ title: r.meta.title as string, path: r.path })
    }
  }
  return crumbs
})

function handleToggle() {
  appStore.toggleSidebar()
}

function toggleDark() {
  appStore.toggleDarkMode()
}

/** 移动端打开 drawer */
function openMobileDrawer() {
  appStore.openMobileDrawer()
}

/** 重启引导 */
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

const userMenuOptions = computed(() => {
  const opts: Array<{ label: string; key: string; props?: { onClick?: () => void } }> = [
    {
      label: authStore.user
        ? `${authStore.displayName} (${authStore.user.role})`
        : '匿名用户',
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

// P1-4: 路由切换后焦点移至主内容区（可访问性）
watch(
  () => route.path,
  async () => {
    await nextTick()
    const main = document.getElementById('main-content')
    if (main) {
      main.focus()
    }
  },
)

onMounted(() => {
  onboardingStore.autoStartIfNeeded()
  if (authStore.token && !authStore.user) {
    authStore.fetchMe()
  }
})
</script>

<template>
  <!-- P1-4: skip-link 键盘可达性 -->
  <a href="#main-content" class="skip-link">跳到主内容</a>

  <NLayout style="height: 100vh">
    <!-- P0-3: 桌面端固定侧栏 -->
    <NLayoutSider
      v-if="!isMobile"
      :collapsed="collapsed"
      :collapsed-width="64"
      :width="240"
      bordered
      @update:collapsed="handleToggle"
    >
      <div class="logo">
        <span class="logo-icon">📚</span>
        <span v-if="!collapsed" class="logo-text">OpsKG</span>
      </div>
      <AppSidebar />
    </NLayoutSider>

    <!-- P0-3: 移动端 drawer 模式 -->
    <NDrawer
      v-if="isMobile"
      :show="appStore.mobileDrawerOpen"
      :width="240"
      placement="left"
      @update:show="(v: boolean) => { if (!v) appStore.closeMobileDrawer() }"
    >
      <div class="logo">
        <span class="logo-icon">📚</span>
        <span class="logo-text">OpsKG</span>
      </div>
      <AppSidebar @navigate="appStore.closeMobileDrawer()" />
    </NDrawer>

    <NLayout>
      <NLayoutHeader bordered class="header">
        <div class="header-left">
          <!-- P0-3: 移动端汉堡按钮 -->
          <NButton
            v-if="isMobile"
            quaternary
            circle
            aria-label="打开菜单"
            class="hamburger-btn"
            @click="openMobileDrawer"
          >
            <template #icon><span>☰</span></template>
          </NButton>
          <!-- P1-3: 面包屑（桌面端） -->
          <NBreadcrumb v-if="!isMobile && breadcrumbs.length > 1" class="breadcrumb">
            <NBreadcrumbItem
              v-for="(crumb, i) in breadcrumbs"
              :key="i"
              :clickable="!!crumb.path && i < breadcrumbs.length - 1"
              @click="crumb.path && i < breadcrumbs.length - 1 && router.push(crumb.path)"
            >
              {{ crumb.title }}
            </NBreadcrumbItem>
          </NBreadcrumb>
          <span v-else class="page-title">{{ pageTitle }}</span>
        </div>
        <div class="header-right">
          <!-- P1-4: aria-label 可访问性 -->
          <button
            class="icon-btn"
            :aria-label="appStore.darkMode ? '切换到浅色模式' : '切换到深色模式'"
            :title="appStore.darkMode ? '切换到浅色模式' : '切换到深色模式'"
            @click="toggleDark"
          >
            {{ appStore.darkMode ? '☀️' : '🌙' }}
          </button>
          <NDropdown
            :options="userMenuOptions"
            trigger="click"
            @select="handleUserMenuSelect"
          >
            <span
              class="user-avatar"
              :title="authStore.displayName"
              role="button"
              tabindex="0"
              aria-label="用户菜单"
            >
              {{ authStore.user ? '👤' : '👥' }}
              <span v-if="authStore.user" class="user-name">{{ authStore.displayName }}</span>
            </span>
          </NDropdown>
        </div>
      </NLayoutHeader>

      <!-- P1-4: 主内容区 id + tabindex 焦点管理 -->
      <NLayoutContent id="main-content" tabindex="-1" class="content">
        <div class="content-inner">
          <router-view />
        </div>
      </NLayoutContent>
    </NLayout>
  </NLayout>
  <OnboardingTour />
</template>

<style scoped>
.logo {
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-3);
  padding: var(--opskg-sp-4) var(--opskg-sp-6);
  font-size: var(--opskg-fs-lg);
  font-weight: 600;
  border-bottom: 1px solid var(--opskg-border-color);
  height: 56px;
  box-sizing: border-box;
}
.logo-icon {
  font-size: var(--opskg-fs-xl);
}
.logo-text {
  color: var(--opskg-text-1);
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--opskg-sp-6);
  height: 56px;
  gap: var(--opskg-sp-3);
}
.header-left {
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-3);
  min-width: 0;
  flex: 1;
}
.breadcrumb {
  font-size: var(--opskg-fs-base);
}
.page-title {
  font-size: var(--opskg-fs-lg);
  font-weight: 600;
  color: var(--opskg-text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.header-right {
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-4);
  flex-shrink: 0;
}
.hamburger-btn {
  flex-shrink: 0;
}
.icon-btn {
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: var(--opskg-fs-lg);
  padding: var(--opskg-sp-1) var(--opskg-sp-2);
  border-radius: var(--opskg-radius-md);
  color: var(--opskg-text-1);
  transition: background var(--opskg-transition-fast);
}
.icon-btn:hover {
  background: var(--opskg-bg-hover);
}
.user-avatar {
  font-size: var(--opskg-fs-xl);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: var(--opskg-sp-2);
  padding: var(--opskg-sp-1) var(--opskg-sp-2);
  border-radius: var(--opskg-radius-md);
  color: var(--opskg-text-1);
  transition: background var(--opskg-transition-fast);
}
.user-avatar:hover,
.user-avatar:focus-visible {
  background: var(--opskg-bg-hover);
}
.user-name {
  font-size: var(--opskg-fs-sm);
  color: var(--opskg-text-2);
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.content {
  padding: var(--opskg-sp-6);
  overflow-y: auto;
}
.content-inner {
  max-width: var(--opskg-content-max-width);
  margin: 0 auto;
}

/* P0-3: 移动端调整 */
@media (max-width: 767px) {
  .header {
    padding: 0 var(--opskg-sp-3);
  }
  .content {
    padding: var(--opskg-sp-3);
  }
  .user-name {
    display: none; /* 移动端隐藏用户名，仅显示头像 */
  }
}
</style>
