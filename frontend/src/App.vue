<script setup lang="ts">
import { NConfigProvider, NMessageProvider, NDialogProvider, darkTheme, zhCN, dateZhCN } from 'naive-ui'
import { computed } from 'vue'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
const theme = computed(() => (appStore.darkMode ? darkTheme : null))
</script>

<template>
  <NConfigProvider :theme="theme" :locale="zhCN" :date-locale="dateZhCN">
    <NMessageProvider>
      <NDialogProvider>
        <router-view />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>
