<script setup lang="ts">
/**
 * P1-2: 统一加载态
 * 替代各视图重复的 <div class="loading-container"><n-spin size="large" /></div>
 *
 * @example <LoadingState v-if="loading" />
 * @example <LoadingState text="生成 Runbook 中..." />
 */
import { NSpin } from 'naive-ui'

withDefaults(
  defineProps<{
    /** 加载提示文本 */
    text?: string
    /** 最小高度（px），默认 200 */
    minHeight?: number | string
    /** spin 尺寸 */
    size?: 'small' | 'medium' | 'large'
  }>(),
  {
    text: '',
    minHeight: 200,
    size: 'large',
  },
)
</script>

<template>
  <div class="loading-state" :style="{ minHeight: typeof minHeight === 'number' ? `${minHeight}px` : minHeight }">
    <NSpin :size="size" />
    <div v-if="text" class="loading-text">{{ text }}</div>
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
