<script setup lang="ts">
import { NConfigProvider, darkTheme, zhCN, dateZhCN } from 'naive-ui'
import { computed } from 'vue'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
const theme = computed(() => (appStore.darkMode ? darkTheme : null))
</script>

<template>
  <NConfigProvider :theme="theme" :locale="zhCN" :date-locale="dateZhCN">
    <router-view />
  </NConfigProvider>
</template>
