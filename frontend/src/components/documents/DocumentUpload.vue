<script setup lang="ts">
import { NUpload } from 'naive-ui'
import type { UploadCustomRequestOptions } from 'naive-ui'

defineProps<{
  uploadHandler: (options: UploadCustomRequestOptions) => void
}>()
</script>

<template>
  <NUpload :show-file-list="false" :custom-request="uploadHandler" drag class="upload-dragger">
    <div class="upload-area">
      <div class="upload-icon">📤</div>
      <div class="upload-text">点击或拖拽文件到此处上传</div>
      <div class="upload-hint">支持 md、docx、xlsx、pdf、html、txt、sql 等格式</div>
    </div>
  </NUpload>
</template>

<style scoped>
.upload-dragger {
  width: 280px;
}

.upload-area {
  border: 2px dashed var(--n-border-color, #d1d5db);
  border-radius: 8px;
  padding: 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.3s ease;
  background: var(--n-base-color, #f9fafb);
}

.upload-area:hover {
  border-color: var(--n-primary-color, #3b82f6);
  background: var(--n-primary-color-suppl, #eff6ff);
}

.upload-icon {
  font-size: 32px;
  margin-bottom: 8px;
}

.upload-text {
  font-size: 14px;
  font-weight: 500;
  color: var(--n-text-color, #111827);
  margin-bottom: 4px;
}

.upload-hint {
  font-size: 12px;
  color: var(--n-text-color-3, #9ca3af);
}
</style>