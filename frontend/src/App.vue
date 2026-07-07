<script setup lang="ts">
import {
  NConfigProvider,
  NMessageProvider,
  NDialogProvider,
  NLoadingBarProvider,
  darkTheme,
  zhCN,
  dateZhCN,
} from 'naive-ui'
import { computed } from 'vue'
import { useAppStore } from '@/stores/app'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import LoadingBarBridge from '@/components/common/LoadingBarBridge'

const appStore = useAppStore()
const theme = computed(() => (appStore.darkMode ? darkTheme : null))
</script>

<template>
  <NConfigProvider :theme="theme" :locale="zhCN" :date-locale="dateZhCN">
    <NLoadingBarProvider>
      <LoadingBarBridge />
      <NMessageProvider>
        <NDialogProvider>
          <ErrorBoundary>
            <router-view />
          </ErrorBoundary>
        </NDialogProvider>
      </NMessageProvider>
    </NLoadingBarProvider>
  </NConfigProvider>
</template>
