<script setup lang="ts">
import { ref } from 'vue'
import { NUpload, NUploadDragger, NIcon, NProgress, NText, NP, useMessage } from 'naive-ui'
import type { UploadOnChange, UploadFileInfo } from 'naive-ui'
import { CloudUploadOutline } from '@vicons/ionicons5'

const props = defineProps<{
  uploadHandler: (file: UploadFileInfo) => Promise<void>
}>()

const emit = defineEmits<{
  (e: 'uploaded', docId: string): void
}>()

const message = useMessage()

// P0: 上传进度状态
const uploading = ref(false)
const uploadProgress = ref(0)

// P0: 接受的文件格式
const acceptedFormats = '.md,.txt,.docx,.xlsx,.pdf,.html,.sql,.csv,.json,.xml,.yaml,.yml,.log,.conf'

// P0: 文件大小限制 (50MB)
const MAX_FILE_SIZE = 50 * 1024 * 1024

async function handleUpload(data: { file: UploadFileInfo; fileList: UploadFileInfo[]; event?: Event | ProgressEvent }) {
  const file = data.file

  // P0: 文件大小检查
  if (file.file && file.file.size > MAX_FILE_SIZE) {
    message.error(`文件 "${file.name}" 超过 50MB 限制，请压缩后上传`)
    return
  }

  uploading.value = true
  uploadProgress.value = 0

  // P0: 模拟上传进度
  const progressTimer = setInterval(() => {
    if (uploadProgress.value < 90) {
      uploadProgress.value += Math.random() * 15
      if (uploadProgress.value > 90) uploadProgress.value = 90
    }
  }, 200)

  try {
    await props.uploadHandler(file)
    clearInterval(progressTimer)
    uploadProgress.value = 100
    message.success(`文件 "${file.name}" 上传成功，正在解析...`)
    setTimeout(() => { uploadProgress.value = 0 }, 1500)
  } catch (err) {
    clearInterval(progressTimer)
    uploadProgress.value = 0
    message.error(`文件 "${file.name}" 上传失败，请重试`)
  } finally {
    uploading.value = false
  }
}

// P0: 文件格式到图标映射
const formatIcons: Record<string, string> = {
  md: '📝',
  txt: '📄',
  docx: '📘',
  xlsx: '📊',
  pdf: '📕',
  html: '🌐',
  sql: '🗄️',
  csv: '📋',
  json: '📦',
  xml: '📰',
  yaml: '⚙️',
  yml: '⚙️',
  log: '📜',
  conf: '🔧',
}

function getFormatIcon(filename: string): string {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  return formatIcons[ext] || '📎'
}
</script>

<template>
  <div class="upload-wrapper">
    <!-- P0: 拖拽上传区域，带视觉反馈 -->
    <n-upload
      :accept="acceptedFormats"
      :max="10"
      :show-file-list="false"
      :default-upload="false"
      multiple
      @change="handleUpload"
      directory-dnd
    >
      <n-upload-dragger>
        <div style="margin-bottom: 12px">
          <n-icon size="48" :depth="3">
            <CloudUploadOutline />
          </n-icon>
        </div>
        <n-text style="font-size: 16px">
          点击或拖拽文件到此处上传
        </n-text>
        <n-p depth="3" style="margin: 8px 0 0 0">
          支持 Markdown、TXT、Word、PDF、HTML、SQL 等格式
        </n-p>
        <n-p depth="3">
          单个文件不超过 50MB，一次最多 10 个文件
        </n-p>
      </n-upload-dragger>
    </n-upload>

    <!-- P0: 上传进度条 -->
    <n-progress
      v-if="uploading"
      type="line"
      :percentage="Math.round(uploadProgress)"
      :indicator-placement="'inside'"
      :height="24"
      :border-radius="4"
      processing
      style="margin-top: 12px"
    />
  </div>
</template>

<style scoped>
.upload-wrapper {
  width: 320px;
}

/* P0: 拖拽区域悬停高亮 */
.upload-wrapper :deep(.n-upload-dragger) {
  border: 2px dashed var(--n-border-color, #d1d5db);
  border-radius: 8px;
  padding: 28px 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.3s ease;
  background: var(--n-base-color, #f9fafb);
}

.upload-wrapper :deep(.n-upload-dragger:hover) {
  border-color: var(--n-primary-color, #3b82f6);
  background: var(--n-primary-color-suppl, #eff6ff);
}

.upload-wrapper :deep(.n-upload-dragger.n-upload-dragger--dragover) {
  border-color: var(--n-primary-color, #3b82f6);
  background: var(--n-primary-color-suppl, #dbeafe);
  border-style: solid;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
}
</style>