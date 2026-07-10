<script setup lang="ts">
/**
 * P1-2: 统一页面标题头
 * 替代各视图重复的 <div class="page-header"><h2 class="page-title">...
 *
 * @example
 * <PageHeader title="Incident 管理" description="事件关联..." />
 * <PageHeader title="文档管理">
 *   <template #actions><NButton>上传</NButton></template>
 * </PageHeader>
 */
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <div class="page-header">
    <div class="page-header-main">
      <h2 class="page-title">{{ title }}</h2>
      <p v-if="description" class="page-desc">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="page-header-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--opskg-sp-4);
  margin-bottom: var(--opskg-sp-6);
}
.page-header-main {
  min-width: 0;
}
.page-title {
  font-size: var(--opskg-fs-2xl);
  font-weight: 600;
  color: var(--opskg-text-1);
  margin: 0;
}
.page-desc {
  font-size: var(--opskg-fs-sm);
  color: var(--opskg-text-2);
  margin-top: var(--opskg-sp-1);
}
.page-header-actions {
  flex-shrink: 0;
  display: flex;
  gap: var(--opskg-sp-2);
}
</style>
