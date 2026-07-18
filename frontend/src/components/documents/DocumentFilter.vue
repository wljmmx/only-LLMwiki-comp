<script setup lang="ts">
import { NInput, NSelect } from 'naive-ui'

defineProps<{
  searchText: string
  formatFilter: string
  statusFilter: string
  formatOptions: { label: string; value: string }[]
  statusOptions: { label: string; value: string }[]
}>()

const emit = defineEmits<{
  'update:searchText': [value: string]
  'update:formatFilter': [value: string]
  'update:statusFilter': [value: string]
  searchInput: [value: string]
}>()

function onSearchInput(val: string) {
  emit('update:searchText', val)
  emit('searchInput', val)
}
</script>

<template>
  <NInput
    :value="searchText"
    placeholder="搜索文件名/标题..."
    clearable
    style="width: 240px"
    @update:value="onSearchInput"
  >
    <template #prefix>🔍</template>
  </NInput>

  <NSelect
    :value="formatFilter"
    :options="formatOptions"
    placeholder="格式筛选"
    style="width: 140px"
    @update:value="(val: string) => emit('update:formatFilter', val)"
  />

  <NSelect
    :value="statusFilter"
    :options="statusOptions"
    placeholder="状态筛选"
    style="width: 140px"
    @update:value="(val: string) => emit('update:statusFilter', val)"
  />
</template>