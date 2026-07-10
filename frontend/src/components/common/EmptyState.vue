<script setup lang="ts">
/**
 * P1-2: 统一空状态
 * 替代各视图重复的 <n-empty description="..." style="padding: 60px 0" />
 * 统一内边距与图标，支持自定义内容插槽
 *
 * @example <EmptyState description="暂无 incident" />
 * @example <EmptyState description="未找到结果"><template #extra><NButton>重试</NButton></template></EmptyState>
 */
import { NEmpty } from 'naive-ui'

defineProps<{
  description?: string
  /** 图标尺寸，默认 64 */
  iconSize?: number
}>()
</script>

<template>
  <NEmpty :description="description" :icon-size="iconSize" class="empty-state">
    <template v-if="$slots.extra" #extra>
      <slot name="extra" />
    </template>
  </NEmpty>
</template>

<style scoped>
.empty-state {
  padding: 60px 0;
  justify-content: center;
}
</style>
