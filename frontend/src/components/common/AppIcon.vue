<script setup lang="ts">
/**
 * P1-1: 通用图标组件
 * 语义命名，统一来源 @/utils/icons
 * @example <AppIcon name="dashboard" :size="20" />
 */
import { computed } from 'vue'
import { NIcon } from 'naive-ui'
import { getIconComponent, type IconName } from '@/utils/icons'

const props = withDefaults(
  defineProps<{
    name: IconName
    size?: number | string
    color?: string
  }>(),
  {
    size: 18,
    color: undefined,
  },
)

const iconComp = computed(() => getIconComponent(props.name))
</script>

<template>
  <NIcon :size="size" :color="color" :aria-hidden="true">
    <component :is="iconComp" />
  </NIcon>
</template>
