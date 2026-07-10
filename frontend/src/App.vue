<script setup lang="ts">
import {
  NConfigProvider,
  NMessageProvider,
  NDialogProvider,
  NLoadingBarProvider,
  darkTheme,
  zhCN,
  dateZhCN,
  type GlobalThemeOverrides,
} from 'naive-ui'
import { computed, onMounted } from 'vue'
import { useAppStore } from '@/stores/app'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import LoadingBarBridge from '@/components/common/LoadingBarBridge'
import PageSkeleton from '@/components/common/PageSkeleton.vue'

const appStore = useAppStore()
const theme = computed(() => (appStore.darkMode ? darkTheme : null))

// P0-2: Naive UI 主题覆盖，对齐品牌令牌
const themeOverrides = computed<GlobalThemeOverrides>(() => {
  const isDark = appStore.darkMode
  return {
    common: {
      primaryColor: '#2080f0',
      primaryColorHover: '#4098fc',
      primaryColorPressed: '#1060d4',
      primaryColorSuppl: '#2080f0',
      successColor: '#18a058',
      warningColor: '#f0a020',
      errorColor: '#d03050',
      infoColor: '#70c0e8',
      borderRadius: '8px',
      borderRadiusSmall: '4px',
      fontFamily:
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",
      fontSize: '14px',
    },
    Card: {
      borderRadius: '12px',
      boxShadow: isDark
        ? '0 1px 3px rgba(0,0,0,0.3)'
        : '0 1px 3px rgba(0,0,0,0.08)',
    },
    Menu: {
      borderRadius: '8px',
    },
    Button: {
      fontWeight: '500',
    },
  }
})

onMounted(() => {
  // P0-3: 初始化响应式 + 绑定系统主题
  appStore.updateViewport()
  appStore.bindOsTheme()

  const onResize = () => appStore.updateViewport()
  window.addEventListener('resize', onResize)
})
</script>

<template>
  <NConfigProvider
    :theme="theme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <NLoadingBarProvider>
      <LoadingBarBridge />
      <NMessageProvider>
        <NDialogProvider>
          <ErrorBoundary>
            <router-view v-slot="{ Component }">
              <transition name="fade" mode="out-in">
                <Suspense>
                  <component :is="Component" />
                  <template #fallback>
                    <PageSkeleton />
                  </template>
                </Suspense>
              </transition>
            </router-view>
          </ErrorBoundary>
        </NDialogProvider>
      </NMessageProvider>
    </NLoadingBarProvider>
  </NConfigProvider>
</template>
