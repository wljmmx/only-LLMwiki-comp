<script setup lang="ts">
import { ref, onMounted, watch, h } from 'vue'
import {
  NDataTable,
  NButton,
  NUpload,
  NInput,
  NSelect,
  NSpace,
  NDrawer,
  NDrawerContent,
  NTag,
  NDescriptions,
  NDescriptionsItem,
  NSpin,
  NEmpty,
  useMessage,
} from 'naive-ui'
import type { UploadCustomRequestOptions } from 'naive-ui'
import { listDocuments, deleteDocument, parseDocument, getDocumentContent, searchDocuments } from '@/api/documents'
import { recompileDocument } from '@/api/wiki'
import { formatFileSize, formatDateTime as formatDateTimeUtil } from '@/utils/format'
import type { DocumentMeta } from '@/types/api'

const message = useMessage()

const loading = ref(false)
const documents = ref<DocumentMeta[]>([])
const total = ref(0)
const limit = ref(10)
const offset = ref(0)
const searchText = ref('')
// P2-11：服务端搜索模式（true 时禁用分页，搜索跨全表）
const isSearching = ref(false)
// 正在编译为 Wiki 的文档 ID（同一时间只允许一个，LLM 编译较慢）
const compilingId = ref<string | null>(null)
const formatFilter = ref<string>('')
const statusFilter = ref<string>('')

const drawerVisible = ref(false)
const currentDoc = ref<DocumentMeta | null>(null)
const docContent = ref('')
const docContentLoading = ref(false)

const formatOptions = [
  { label: '全部格式', value: '' },
  { label: 'Markdown', value: 'md' },
  { label: 'Word', value: 'docx' },
  { label: 'Excel', value: 'xlsx' },
  { label: 'PDF', value: 'pdf' },
  { label: 'HTML', value: 'html' },
  { label: '文本', value: 'txt' },
  { label: 'SQL', value: 'sql' },
]

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '已上传', value: 'uploaded' },
  { label: '解析中', value: 'parsing' },
  { label: '已解析', value: 'parsed' },
  { label: '失败', value: 'failed' },
]

const statusTagType: Record<string, 'default' | 'info' | 'success' | 'error'> = {
  uploaded: 'default',
  parsing: 'info',
  parsed: 'success',
  failed: 'error',
}

const statusText: Record<string, string> = {
  uploaded: '已上传',
  parsing: '解析中',
  parsed: '已解析',
  failed: '失败',
}

const columns = [
  {
    title: '文件名',
    key: 'filename',
    ellipsis: { tooltip: true },
  },
  {
    title: '格式',
    key: 'format',
    width: 100,
    render(row: DocumentMeta) {
      return row.format.toUpperCase()
    },
  },
  {
    title: '大小',
    key: 'size',
    width: 120,
    render(row: DocumentMeta) {
      return formatFileSize(row.size)
    },
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render(row: DocumentMeta) {
      return h(
        NTag,
        { type: statusTagType[row.status], size: 'small' },
        { default: () => statusText[row.status] },
      )
    },
  },
  {
    title: '上传时间',
    key: 'created_at',
    width: 180,
    render(row: DocumentMeta) {
      return formatDateTimeUtil(row.created_at)
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 240,
    render(row: DocumentMeta) {
      return h(
        NSpace,
        { size: 'small' },
        {
          default: () => [
            h(
              NButton,
              { size: 'small', type: 'primary', quaternary: true, onClick: () => handleView(row) },
              { default: () => '查看' },
            ),
            h(
              NButton,
              {
                size: 'small',
                type: 'info',
                quaternary: true,
                loading: compilingId.value === row.id,
                disabled: compilingId.value !== null && compilingId.value !== row.id,
                onClick: () => handleCompileToWiki(row),
              },
              { default: () => '编译为Wiki' },
            ),
            h(
              NButton,
              { size: 'small', type: 'error', quaternary: true, onClick: () => handleDelete(row) },
              { default: () => '删除' },
            ),
          ],
        },
      )
    },
  },
]

async function fetchDocuments() {
  // 退出搜索模式，回到分页列表
  isSearching.value = false
  loading.value = true
  try {
    const params: Record<string, any> = {
      limit: limit.value,
      offset: offset.value,
    }
    if (formatFilter.value) params.format = formatFilter.value
    if (statusFilter.value) params.status = statusFilter.value

    const res = await listDocuments(params)
    documents.value = res.documents
    total.value = res.stats.total
  } catch (err) {
    message.error('获取文档列表失败')
    console.error(err)
  } finally {
    loading.value = false
  }
}

/** P2-11：服务端搜索（跨全表 LIKE，不受分页限制） */
async function doSearch(q: string) {
  isSearching.value = true
  loading.value = true
  try {
    const res = await searchDocuments(q)
    documents.value = res.results
    total.value = res.count
  } catch (err) {
    message.error('搜索失败')
    console.error(err)
  } finally {
    loading.value = false
  }
}

// 搜索输入防抖（300ms）：空值回到分页列表，非空走服务端搜索
let searchTimer: ReturnType<typeof setTimeout> | null = null
function handleSearchInput(val: string) {
  searchText.value = val
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    const trimmed = val.trim()
    if (trimmed) doSearch(trimmed)
    else fetchDocuments()
  }, 300)
}

function handleUpload({ file, onFinish, onError }: UploadCustomRequestOptions) {
  const fileName = file.name
  const ext = fileName.split('.').pop()?.toLowerCase() || ''
  const fmt = ext === 'md' ? 'markdown' : ext

  const formData = new FormData()
  formData.append('file', file.file as File)

  parseDocument(fmt, formData)
    .then(() => {
      message.success('上传成功，正在解析...')
      onFinish()
      fetchDocuments()
    })
    .catch((err) => {
      message.error('上传失败')
      console.error(err)
      onError()
    })
}

async function handleCompileToWiki(doc: DocumentMeta) {
  compilingId.value = doc.id
  message.loading('正在编译为 Wiki，LLM 处理中，请稍候...', { duration: 0 })
  try {
    const res = await recompileDocument(doc.id)
    message.destroyAll()
    const created = res.pages_created ?? 0
    const updated = res.pages_updated ?? 0
    const errors = res.errors ?? []
    if (errors.length > 0) {
      message.warning(`编译完成（${created} 创建 / ${updated} 更新），但有 ${errors.length} 个错误`)
    } else if (created === 0 && updated === 0) {
      message.info('编译完成，无新页面生成（内容可能未变化或 LLM 不可用降级为模板）')
    } else {
      message.success(`编译成功：${created} 个页面创建，${updated} 个页面更新`)
    }
  } catch (err: any) {
    message.destroyAll()
    message.error('编译失败：' + (err?.response?.data?.detail || err?.message || '未知错误'))
    console.error(err)
  } finally {
    compilingId.value = null
  }
}

async function handleView(doc: DocumentMeta) {
  currentDoc.value = doc
  drawerVisible.value = true
  docContent.value = ''
  docContentLoading.value = true
  try {
    const res = await getDocumentContent(doc.id)
    docContent.value = res.content
  } catch (err) {
    message.error('获取文档内容失败')
    console.error(err)
  } finally {
    docContentLoading.value = false
  }
}

async function handleDelete(doc: DocumentMeta) {
  if (!window.confirm(`确定要删除文档 "${doc.filename}" 吗？`)) {
    return
  }
  try {
    await deleteDocument(doc.id)
    message.success('删除成功')
    fetchDocuments()
  } catch (err) {
    message.error('删除失败')
    console.error(err)
  }
}

function handlePageChange(page: number) {
  offset.value = (page - 1) * limit.value
  fetchDocuments()
}

function handlePageSizeChange(size: number) {
  limit.value = size
  offset.value = 0
  fetchDocuments()
}

watch([formatFilter, statusFilter], () => {
  offset.value = 0
  // P2-11：切换过滤器退出搜索模式（搜索不携带 format/status），清空搜索框并取消待发搜索
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  searchText.value = ''
  fetchDocuments()
})

onMounted(() => {
  fetchDocuments()
})
</script>

<template>
  <div class="documents-view">
    <div class="toolbar">
      <NSpace align="center" wrap>
        <NUpload :show-file-list="false" :custom-request="handleUpload" drag class="upload-dragger">
          <div class="upload-area">
            <div class="upload-icon">📤</div>
            <div class="upload-text">点击或拖拽文件到此处上传</div>
            <div class="upload-hint">支持 md、docx、xlsx、pdf、html、txt、sql 等格式</div>
          </div>
        </NUpload>

        <NInput
          :value="searchText"
          placeholder="搜索文件名/标题..."
          clearable
          style="width: 240px"
          @update:value="handleSearchInput"
        >
          <template #prefix>🔍</template>
        </NInput>

        <NSelect
          v-model:value="formatFilter"
          :options="formatOptions"
          placeholder="格式筛选"
          style="width: 140px"
        />

        <NSelect
          v-model:value="statusFilter"
          :options="statusOptions"
          placeholder="状态筛选"
          style="width: 140px"
        />
      </NSpace>
    </div>

    <div class="table-container">
      <NDataTable
        :columns="columns"
        :data="documents"
        :loading="loading"
        :pagination="isSearching
          ? false
          : {
            page: offset / limit + 1,
            pageSize: limit,
            itemCount: total,
            pageSizes: [10, 20, 50],
            showSizePicker: true,
            onUpdatePage: handlePageChange,
            onUpdatePageSize: handlePageSizeChange,
        }"
        :bordered="false"
        size="medium"
      >
        <template #empty>
          <NEmpty description="暂无文档" />
        </template>
      </NDataTable>
    </div>

    <NDrawer v-model:show="drawerVisible" :width="640" placement="right">
      <NDrawerContent title="文档详情" :closable="true">
        <template v-if="currentDoc">
          <NDescriptions :column="2" bordered size="small" class="doc-info">
            <NDescriptionsItem label="文件名">
              {{ currentDoc.filename }}
            </NDescriptionsItem>
            <NDescriptionsItem label="格式">
              {{ currentDoc.format.toUpperCase() }}
            </NDescriptionsItem>
            <NDescriptionsItem label="大小">
              {{ formatFileSize(currentDoc.size) }}
            </NDescriptionsItem>
            <NDescriptionsItem label="状态">
              <NTag :type="statusTagType[currentDoc.status]" size="small">
                {{ statusText[currentDoc.status] }}
              </NTag>
            </NDescriptionsItem>
            <NDescriptionsItem label="上传时间" :span="2">
              {{ formatDateTimeUtil(currentDoc.created_at) }}
            </NDescriptionsItem>
          </NDescriptions>

          <div class="content-section">
            <div class="content-title">内容预览</div>
            <div class="content-preview">
              <NSpin v-if="docContentLoading" />
              <div v-else-if="docContent" class="doc-content">
                <pre>{{ docContent }}</pre>
              </div>
              <NEmpty v-else description="暂无内容" size="small" />
            </div>
          </div>
        </template>
      </NDrawerContent>
    </NDrawer>
  </div>
</template>

<style scoped>
.documents-view {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.toolbar {
  margin-bottom: 16px;
  padding: 16px;
  background: var(--n-card-color, #fff);
  border-radius: 8px;
  border: 1px solid var(--n-border-color, #e5e7eb);
}

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

.table-container {
  flex: 1;
  background: var(--n-card-color, #fff);
  border-radius: 8px;
  border: 1px solid var(--n-border-color, #e5e7eb);
  padding: 16px;
  overflow: hidden;
}

.doc-info {
  margin-bottom: 24px;
}

.content-section {
  display: flex;
  flex-direction: column;
  height: calc(100% - 200px);
}

.content-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
  margin-bottom: 12px;
}

.content-preview {
  flex: 1;
  border: 1px solid var(--n-border-color, #e5e7eb);
  border-radius: 6px;
  padding: 16px;
  overflow-y: auto;
  background: var(--n-base-color, #f9fafb);
  min-height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.doc-content {
  width: 100%;
  height: 100%;
  align-self: flex-start;
}

.doc-content pre {
  margin: 0;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: var(--n-text-color, #111827);
}
</style>
