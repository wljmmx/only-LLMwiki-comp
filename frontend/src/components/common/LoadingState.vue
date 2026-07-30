<script setup lang="ts">
/**
 * P1-2: 统一加载态
 * 替代各视图重复的 <div class="loading-container"><n-spin size="large" /></div>
 *
 * @example <LoadingState v-if="loading" />
 * @example <LoadingState text="生成 Runbook 中..." />
 * @example <LoadingState :skeleton="true" />
 */
import { NSpin, NSkeleton } from 'naive-ui'

withDefaults(
  defineProps<{
    /** 加载提示文本 */
    text?: string
    /** 最小高度（px），默认 200 */
    minHeight?: number | string
    /** spin 尺寸 */
    size?: 'small' | 'medium' | 'large'
    /** P1-7: 使用骨架屏（NSkeleton）替代 NSpin */
    skeleton?: boolean
  }>(),
  {
    text: '',
    minHeight: 200,
    size: 'large',
    skeleton: false,
  },
)
</script>

<template>
  <div class="loading-state" :style="{ minHeight: typeof minHeight === 'number' ? `${minHeight}px` : minHeight }">
    <template v-if="skeleton">
      <NSkeleton text :width="220" :height="28" />
      <NSkeleton text :repeat="4" style="margin-top: 12px" />
    </template>
    <template v-else>
      <NSpin :size="size" />
      <div v-if="text" class="loading-text">{{ text }}</div>
    </template>
  </div>
</template>

<style scoped>
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  gap: var(--opskg-sp-4);
}
.loading-text {
  font-size: var(--opskg-fs-sm);
  color: var(--opskg-text-2);
}
</style>
