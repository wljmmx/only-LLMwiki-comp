<script setup lang="ts">
import { NLayout, NLayoutSider, NLayoutHeader, NLayoutContent } from 'naive-ui'
import AppSidebar from './AppSidebar.vue'
import { useAppStore } from '@/stores/app'
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const appStore = useAppStore()
const route = useRoute()
const collapsed = computed(() => appStore.sidebarCollapsed)
const pageTitle = computed(() => route.meta.title || 'OpsKG')

function handleToggle() {
  appStore.toggleSidebar()
}

function toggleDark() {
  appStore.toggleDarkMode()
}
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
          <span class="user-avatar">👤</span>
        </div>
      </NLayoutHeader>

      <NLayoutContent class="content">
        <router-view />
      </NLayoutContent>
    </NLayout>
  </NLayout>
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
}
.content {
  padding: 24px;
  overflow-y: auto;
}
</style>
