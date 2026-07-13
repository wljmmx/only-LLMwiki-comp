<script setup lang="ts">
import { ref, onErrorCaptured } from 'vue'
import { NCard, NButton, NSpace, NAlert, NCode } from 'naive-ui'

interface Props {
  /** 是否显示错误堆栈详情 */
  showStack?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  // P2-3: 生产环境默认隐藏堆栈，避免暴露内部路径
  showStack: !import.meta.env.PROD,
})

const emit = defineEmits<{
  (e: 'error', err: Error, info: string): void
  (e: 'reset'): void
}>()

const hasError = ref(false)
const error = ref<Error | null>(null)
const errorInfo = ref<string>('')
const errorStack = ref<string>('')

onErrorCaptured((err: Error, _instance, info) => {
  hasError.value = true
  error.value = err
  errorInfo.value = info
  errorStack.value = err.stack || ''
  emit('error', err, info)
  // 返回 false 阻止错误继续向上冒泡
  return false
})

function handleReset() {
  hasError.value = false
  error.value = null
  errorInfo.value = ''
  errorStack.value = ''
  emit('reset')
}

function handleReload() {
  window.location.reload()
}
</script>

<template>
  <template v-if="!hasError">
    <slot />
  </template>
  <template v-else>
    <div class="error-boundary">
      <NCard :bordered="true" class="error-card">
        <template #header>
          <NSpace align="center" :size="8">
            <span class="error-icon">⚠️</span>
            <span>页面渲染出错</span>
          </NSpace>
        </template>

        <NSpace vertical :size="12">
          <NAlert type="error" :show-icon="true">
            <template #header>{{ error?.name || 'Error' }}</template>
            {{ error?.message || '未知错误' }}
          </NAlert>

          <div v-if="props.showStack && errorStack" class="stack-section">
            <div class="stack-label">错误堆栈：</div>
            <NCode :code="errorStack" language="text" word-wrap class="stack-code" />
          </div>

          <div v-if="errorInfo" class="info-section">
            <span class="info-label">触发位置：</span>
            <code class="info-value">{{ errorInfo }}</code>
          </div>

          <NSpace :size="12">
            <NButton type="primary" @click="handleReset">重试</NButton>
            <NButton quaternary @click="handleReload">刷新页面</NButton>
          </NSpace>
        </NSpace>
      </NCard>
    </div>
  </template>
</template>

<style scoped>
.error-boundary {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 400px;
  padding: 24px;
}

.error-card {
  max-width: 640px;
  width: 100%;
}

.error-icon {
  font-size: 18px;
}

.stack-section {
  margin-top: 4px;
}

.stack-label {
  font-size: 12px;
  color: var(--n-text-color-2, #6b7280);
  margin-bottom: 4px;
}

.stack-code {
  font-size: 11px;
  padding: 10px;
  border-radius: 6px;
  background: var(--n-color-target, #fafafa);
  max-height: 240px;
  overflow: auto;
}

.info-section {
  font-size: 12px;
  color: var(--n-text-color-2, #6b7280);
}

.info-label {
  margin-right: 4px;
}

.info-value {
  background: var(--n-color-target, #f5f5f5);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}
</style>
