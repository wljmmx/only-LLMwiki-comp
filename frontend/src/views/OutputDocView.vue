<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  NCard, NTag, NSpace, NButton, NSelect, NInput, NAlert, NDivider,
  NDataTable, NModal, NSpin, NProgress, useMessage,
} from 'naive-ui'
import { getAuthToken } from '@/api/index'
import PageHeader from '@/components/common/PageHeader.vue'

const message = useMessage()
const isAuthenticated = computed(() => !!getAuthToken())

// ── 状态 ──
const loading = ref(false)
const generating = ref(false)
const generatedDocs = ref<any[]>([])
const selectedTemplate = ref<string | null>(null)
const systemName = ref('')
const customTitle = ref('')
const previewContent = ref('')
const showPreview = ref(false)

// ── 模板列表 ──
const templateOptions = ref([
  { label: '前台操作手册', value: 'operations_manual' },
  { label: '故障排查指南', value: 'troubleshooting_guide' },
  { label: '部署指南', value: 'deployment_guide' },
  { label: '系统架构文档', value: 'architecture_doc' },
  { label: '运维操作手册合集', value: 'runbook_collection' },
])

// ── 已生成文档列表 ──
const docColumns = [
  { title: '文档标题', key: 'title', ellipsis: { tooltip: true } },
  { title: '模板', key: 'template_name', width: 120 },
  { title: '生成时间', key: 'generated_at', width: 180 },
  { title: '操作', key: 'actions', width: 160 },
]

// ── 方法 ──
async function loadGeneratedDocs() {
  loading.value = true
  try {
    const token = getAuthToken()
    const resp = await fetch('/api/output/docs', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (resp.ok) {
      const data = await resp.json()
      generatedDocs.value = data.docs || []
    }
  } catch {
    // 后端不可达时静默
  } finally {
    loading.value = false
  }
}

async function generateDocument() {
  if (!selectedTemplate.value) {
    message.warning('请选择文档模板')
    return
  }
  generating.value = true
  try {
    const token = getAuthToken()
    const resp = await fetch('/api/output/generate', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        template_id: selectedTemplate.value,
        system_name: systemName.value || undefined,
        custom_title: customTitle.value || undefined,
      }),
    })
    if (resp.ok) {
      const data = await resp.json()
      message.success('文档生成成功')
      previewContent.value = data.content || ''
      showPreview.value = true
      await loadGeneratedDocs()
    } else {
      const err = await resp.json().catch(() => ({}))
      message.error(err.detail || '生成失败')
    }
  } catch (e: any) {
    message.error(e.message || '生成请求失败')
  } finally {
    generating.value = false
  }
}

function downloadDoc(docId: string) {
  const token = getAuthToken()
  const url = `/api/output/docs/${docId}/download`
  if (token) {
    const a = document.createElement('a')
    a.href = url
    a.download = `${docId}.md`
    document.body.appendChild(a)
    a.click()
    a.remove()
  } else {
    window.open(url, '_blank')
  }
}

function viewDoc(docId: string) {
  const token = getAuthToken()
  fetch(`/api/output/docs/${docId}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
    .then(r => r.json())
    .then(data => {
      previewContent.value = data.content || ''
      showPreview.value = true
    })
    .catch(() => message.error('加载文档失败'))
}

onMounted(() => {
  loadGeneratedDocs()
})
</script>

<template>
  <div class="output-doc-view">
    <PageHeader title="文档生成" subtitle="基于 Wiki 知识库生成标准化输出文档" />

    <n-space vertical size="medium">
      <!-- 生成表单 -->
      <n-card title="生成新文档" size="small">
        <n-space vertical>
          <n-space>
            <n-select
              v-model:value="selectedTemplate"
              :options="templateOptions"
              placeholder="选择文档模板"
              style="width: 240px"
              clearable
            />
            <n-input
              v-model:value="systemName"
              placeholder="目标系统名称（可选）"
              style="width: 200px"
              clearable
            />
            <n-input
              v-model:value="customTitle"
              placeholder="自定义标题（可选）"
              style="width: 240px"
              clearable
            />
            <n-button
              type="primary"
              :loading="generating"
              :disabled="!selectedTemplate"
              @click="generateDocument"
            >
              生成文档
            </n-button>
          </n-space>
          <n-alert type="info" :bordered="false" v-if="selectedTemplate">
            系统将根据选定的模板，从 Wiki 知识库中提取相关内容，自动编排生成标准化文档。
          </n-alert>
        </n-space>
      </n-card>

      <!-- 已生成文档列表 -->
      <n-card title="已生成文档" size="small">
        <n-dataTable
          :columns="docColumns"
          :data="generatedDocs"
          :loading="loading"
          :bordered="false"
          size="small"
          :emptyText="'暂无生成文档'"
        >
          <template #empty>
            <n-space vertical align="center">
              <span>暂无生成文档</span>
              <span style="color: var(--n-text-color-disabled); font-size: 12px">
                选择模板并点击「生成文档」创建第一份输出文档
              </span>
            </n-space>
          </template>
        </n-dataTable>
      </n-card>
    </n-space>

    <!-- 预览弹窗 -->
    <n-modal
      v-model:show="showPreview"
      preset="card"
      title="文档预览"
      style="width: 900px; max-height: 80vh"
      :mask-closable="true"
    >
      <div class="preview-content" v-html="previewContent" />
    </n-modal>
  </div>
</template>

<style scoped>
.output-doc-view {
  padding: 16px;
}

.preview-content {
  max-height: 60vh;
  overflow-y: auto;
  padding: 16px;
  font-family: 'Monaco', 'Menlo', monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--n-color-embedded);
  border-radius: 8px;
}
</style>