<script setup lang="ts">
import { NInput, NButton } from 'naive-ui'

defineProps<{
  query: string
  loading: boolean
}>()

const emit = defineEmits<{
  'update:query': [value: string]
  search: []
}>()

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    emit('search')
  }
}
</script>

<template>
  <div class="search-bar">
    <NInput
      :value="query"
      size="large"
      placeholder="输入关键词搜索知识库..."
      clearable
      class="search-input"
      @update:value="(val: string) => emit('update:query', val)"
      @keydown="handleKeydown"
    >
      <template #prefix>
        <span style="font-size: 18px">🔍</span>
      </template>
    </NInput>
    <NButton
      type="primary"
      size="large"
      :loading="loading"
      class="search-btn"
      @click="emit('search')"
    >
      搜索
    </NButton>
  </div>
</template>

<style scoped>
.search-bar {
  display: flex;
  gap: 12px;
  margin-bottom: 32px;
  align-items: center;
}

.search-input {
  flex: 1;
}

.search-btn {
  min-width: 100px;
}
</style>