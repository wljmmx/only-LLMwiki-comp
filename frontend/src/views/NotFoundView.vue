<script setup lang="ts">
/**
 * 404 页面不存在（S14-1 路由级权限守卫）
 *
 * 触发场景：
 * - 路由表无匹配（catch-all `/:pathMatch(.*)*` 兜底）
 * - 用户手动输入错误 URL 或旧链接失效
 */
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { NCard, NButton, NSpace, NAlert } from 'naive-ui'

const router = useRouter()
const route = useRoute()

const attemptedPath = computed(() => route.fullPath)

function goHome() {
  router.replace('/dashboard')
}

function goBack() {
  // 无历史时退回首页
  if (window.history.length > 1) {
    router.back()
  } else {
    router.replace('/dashboard')
  }
}
</script>

<template>
  <div class="not-found-page">
    <NCard :bordered="true" class="not-found-card">
      <template #header>
        <NSpace align="center" :size="8">
          <span class="not-found-icon">🔍</span>
          <span>页面不存在（404）</span>
        </NSpace>
      </template>

      <NSpace vertical :size="16">
        <NAlert type="info" :show-icon="true">
          <template #header>路径未匹配</template>
          访问的路径
          <code class="attempted-path">{{ attemptedPath }}</code>
          在系统中不存在对应页面。可能是链接已失效、输入错误，或该功能尚未上线。
        </NAlert>

        <NSpace :size="12">
          <NButton type="primary" @click="goHome">返回首页</NButton>
          <NButton quaternary @click="goBack">返回上一页</NButton>
        </NSpace>
      </NSpace>
    </NCard>
  </div>
</template>

<style scoped>
.not-found-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 400px;
  padding: 24px;
}

.not-found-card {
  max-width: 560px;
  width: 100%;
}

.not-found-icon {
  font-size: 18px;
}

.attempted-path {
  background: var(--n-color-target, #f5f5f5);
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 12px;
  font-family: 'SFMono-Regular', Consolas, monospace;
  word-break: break-all;
}
</style>
