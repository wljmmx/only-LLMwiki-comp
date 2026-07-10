<script setup lang="ts">
/**
 * P1-3: 页面骨架屏
 * 路由 chunk 加载/异步 setup 期间的占位，替代空白闪烁
 * 用于 App.vue <Suspense> fallback 与各视图首次加载
 *
 * @example
 * <Suspense>
 *   <component :is="Component" />
 *   <template #fallback><PageSkeleton /></template>
 * </Suspense>
 */
import { NSkeleton } from 'naive-ui'

withDefaults(
  defineProps<{
    /** 是否显示头部标题骨架 */
    header?: boolean
    /** 卡片骨架数量，默认 1 */
    cards?: number
  }>(),
  {
    header: true,
    cards: 1,
  },
)
</script>

<template>
  <div class="page-skeleton">
    <!-- 页面标题骨架 -->
    <div v-if="header" class="skeleton-header">
      <NSkeleton text :width="220" :height="28" />
      <NSkeleton text :width="360" :height="14" style="margin-top: 8px" />
    </div>

    <!-- 卡片骨架 -->
    <div
      v-for="n in cards"
      :key="n"
      class="skeleton-card"
    >
      <div class="skeleton-card-header">
        <NSkeleton text :width="120" :height="16" />
        <NSkeleton text :width="80" :height="16" />
      </div>
      <div class="skeleton-card-body">
        <NSkeleton text :repeat="4" style="margin-bottom: 12px" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-skeleton {
  max-width: var(--opskg-content-max-width, 1200px);
  margin: 0 auto;
  padding: var(--opskg-sp-2);
}
.skeleton-header {
  margin-bottom: var(--opskg-sp-6);
}
.skeleton-card {
  background: var(--opskg-bg-elevated);
  border: 1px solid var(--opskg-border-color);
  border-radius: var(--opskg-radius-lg);
  padding: var(--opskg-sp-6);
  margin-bottom: var(--opskg-sp-4);
  box-shadow: var(--opskg-shadow-card);
}
.skeleton-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--opskg-sp-4);
}
.skeleton-card-body {
  padding-top: var(--opskg-sp-2);
}
</style>
