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
  NTabs,
  NTabPane,
  NSteps,
  NStep,
  NProgress,
  NPopconfirm,
  useMessage,
} from 'naive-ui'
import type { UploadCustomRequestOptions } from 'naive-ui'
import { listDocuments, deleteDocument, parseDocument, getDocumentContent, searchDocuments, getPipelineStatus, compileToWiki, getDocument } from '@/api/documents'
import { formatFileSize, formatDateTime as formatDateTimeUtil } from '@/utils/format'
import { useSse } from '@/composables/useSse'
import type { SseEvent } from '@/composables/useSse'
import type { DocumentMeta } from '@/types/api'

const message = useMessage()
const { subscribe } = useSse()

const loading = ref(false)
const documents = ref<DocumentMeta[]>([])
const total = ref(0)
const limit = ref(10)
const offset = ref(0)
const searchText = ref('')
const isSearching = ref(false)
const compilingId = ref<string | null>(null)
const formatFilter = ref<string>('')
const statusFilter = ref<string>('')

// P1-14: 文档批量操作选中状态
const checkedRowKeys = ref<string[]>([])
const batchLoading = ref(false)

// P3-2: 流水线步骤状态
interface PipelineStep {
  name: string
  label: string
  status: 'pending' | 'running' | 'done' | 'error'
  started_at?: string | null
  duration_ms?: number | null
  error?: string | null
  // P2-5.5: compile 步骤的子进度（逐实体编译）
  subProgress?: { current: number; total: number; currentEntity: string } | null
}
const pipelineSteps = ref<PipelineStep[]>([])
const pipelineLoading = ref(false)
const pipelineProgress = ref(0)
const pipelineResult = ref<Record<string, any> | null>(null)

const drawerVisible = ref(false)
const drawerTab = ref<'info' | 'content' | 'pipeline'>('info')
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
  { label: '已解析', value: 'parsed' },
  { label: '已抽取', value: 'extracted' },
  { label: '已编译', value: 'compiled' },
  { label: '失败', value: 'error' },
]

const statusTagType: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  uploaded: 'default',
  parsed: 'info',
  extracted: 'warning',
  compiled: 'success',
  error: 'error',
}

const statusText: Record<string, string> = {
  uploaded: '已上传',
  parsed: '已解析',
  extracted: '已抽取',
  compiled: '已编译',
  error: '失败',
}

const columns = [
  // P1-14: 批量选择列
  {
    type: 'selection' as const,
    width: 50,
  },
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
            // P1-13: 删除按钮外裹 NPopconfirm，替代 window.confirm
            h(
              NPopconfirm,
              {
                onPositiveClick: () => handleDelete(row),
              },
              {
                trigger: () => h(
                  NButton,
                  { size: 'small', type: 'error', quaternary: true },
                  { default: () => '删除' },
                ),
                default: () => `确定删除文档 ${row.filename}？此操作不可撤销`,
              },
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
  pipelineLoading.value = true
  pipelineProgress.value = 0
  pipelineResult.value = null
  pipelineSteps.value = [
    { name: 'parse', label: '解析', status: 'pending' },
    { name: 'extract', label: '知识抽取', status: 'pending' },
    { name: 'compile', label: '编译 Wiki', status: 'pending' },
    { name: 'index', label: '重建索引', status: 'pending' },
  ]

  // P3-2: 使用 SSE 流式编译，实时展示步骤进度
  const stepIndex: Record<string, number> = {
    parse: 0, extract: 1, compile: 2, index: 3,
  }

  subscribe(`/llm-wiki/recompile/${doc.id}/stream?force=true`, {
    onEvent: (evt: SseEvent) => {
      if (evt.type === 'step_start') {
        const step = evt.data.step as string
        const idx = stepIndex[step] ?? 0
        if (step in stepIndex) {
          pipelineSteps.value[idx].status = 'running'
          pipelineProgress.value = (idx / 4) * 100
        }
      } else if (evt.type === 'step_done') {
        const step = evt.data.step as string
        // P2-5.5: wiki_compiler 取消时发 step_done("cancelled")，不应更新任何步骤
        if (step === 'cancelled') return
        const idx = stepIndex[step]
        if (idx !== undefined) {
          pipelineSteps.value[idx].status = 'done'
          pipelineSteps.value[idx].duration_ms = evt.data.duration_ms ?? null
          pipelineProgress.value = ((idx + 1) / 4) * 100
          if (step === 'compile') {
            pipelineResult.value = evt.data
          }
        }
      } else if (evt.type === 'page_start') {
        // P2-5.5: compile 步骤逐实体编译开始
        const data = evt.data
        pipelineSteps.value[2].subProgress = {
          current: (data.index ?? 0),
          total: data.total ?? 0,
          currentEntity: data.entity ?? '',
        }
      } else if (evt.type === 'page_done') {
        // P2-5.5: 单实体编译完成，更新子进度
        const data = evt.data
        const sp = pipelineSteps.value[2].subProgress
        if (sp) {
          sp.current = (data.index ?? sp.current) + 1
          sp.currentEntity = ''
        }
      } else if (evt.type === 'progress') {
        // P2-5.5: wiki_compiler 精确百分比进度（compile 步骤内）
        const percent = evt.data.percent as number | undefined
        if (typeof percent === 'number' && percent > 0) {
          // compile 是第 3 步（索引 2），映射到 50%-75% 区间
          pipelineProgress.value = 50 + (percent / 100) * 25
        }
      } else if (evt.type === 'done') {
        pipelineProgress.value = 100
        pipelineLoading.value = false
        compilingId.value = null
        // 清理子进度
        pipelineSteps.value[2].subProgress = null
        const created = evt.data.pages_created ?? 0
        const updated = evt.data.pages_updated ?? 0
        const errors = evt.data.errors ?? []
        if (errors.length > 0) {
          message.warning(`编译完成（${created} 创建 / ${updated} 更新），但有 ${errors.length} 个错误`)
        } else if (created === 0 && updated === 0) {
          message.info('编译完成，无新页面生成')
        } else {
          message.success(`编译成功：${created} 个页面创建，${updated} 个页面更新`)
        }
        fetchDocuments()
      } else if (evt.type === 'error') {
        pipelineLoading.value = false
        compilingId.value = null
        message.error('编译失败：' + (evt.data.message || '未知错误'))
        const step = evt.data.step as string
        const idx = stepIndex[step]
        if (idx !== undefined) {
          pipelineSteps.value[idx].status = 'error'
          pipelineSteps.value[idx].error = evt.data.message
        }
      }
    },
    onError: (err: string) => {
      pipelineLoading.value = false
      compilingId.value = null
      message.error('编译连接失败：' + err)
    },
  })
}

async function handleView(doc: DocumentMeta) {
  drawerVisible.value = true
  drawerTab.value = 'info'
  docContent.value = ''
  docContentLoading.value = true
  pipelineSteps.value = []
  pipelineLoading.value = false
  pipelineResult.value = null

  // 通过 getDocument 刷新最新元数据（避免使用表格行数据可能已过期）
  try {
    currentDoc.value = await getDocument(doc.id)
  } catch {
    // 降级：API 不可用时使用表格行数据
    currentDoc.value = doc
  }

  try {
    const res = await getDocumentContent(doc.id)
    docContent.value = res.content
  } catch (err) {
    message.error('获取文档内容失败')
    console.error(err)
  } finally {
    docContentLoading.value = false
  }

  // P3-3: 异步加载流水线状态
  loadPipelineStatus(doc.id)
}

async function loadPipelineStatus(docId: string) {
  try {
    const res = await getPipelineStatus(docId)
    pipelineSteps.value = (res.steps || []) as PipelineStep[]
  } catch {
    // 静默失败，流水线状态非关键
  }
}

// P1-13: 实际执行单行删除（确认逻辑由 NPopconfirm 处理）
async function handleDelete(doc: DocumentMeta) {
  try {
    await deleteDocument(doc.id)
    message.success('删除成功')
    // P1-14: 清空该行在 checkedRowKeys 中的项，防止脏数据
    checkedRowKeys.value = checkedRowKeys.value.filter((k) => k !== doc.id)
    fetchDocuments()
  } catch (err) {
    message.error('删除失败')
    console.error(err)
  }
}

// P1-14: 批量删除文档（确认逻辑由工具栏 NPopconfirm 处理）
async function handleBatchDelete() {
  const ids = [...checkedRowKeys.value]
  if (ids.length === 0) return
  batchLoading.value = true
  try {
    const results = await Promise.allSettled(ids.map((id) => deleteDocument(id)))
    const failed = results.filter((r) => r.status === 'rejected')
    if (failed.length === 0) {
      message.success(`成功删除 ${ids.length} 个文档`)
    } else if (failed.length === ids.length) {
      message.error('批量删除全部失败')
    } else {
      message.warning(`批量删除部分失败：成功 ${ids.length - failed.length} / 失败 ${failed.length}`)
    }
    checkedRowKeys.value = []
    fetchDocuments()
  } catch (err) {
    message.error('批量删除失败')
    console.error(err)
  } finally {
    batchLoading.value = false
  }
}

// P1-14: 批量编译为 Wiki（调用非流式 compileToWiki API）
async function handleBatchCompile() {
  const ids = [...checkedRowKeys.value]
  if (ids.length === 0) return
  batchLoading.value = true
  try {
    const results = await Promise.allSettled(ids.map((id) => compileToWiki(id)))
    const failed = results.filter((r) => r.status === 'rejected')
    if (failed.length === 0) {
      message.success(`成功编译 ${ids.length} 个文档`)
    } else if (failed.length === ids.length) {
      message.error('批量编译全部失败')
    } else {
      message.warning(`批量编译部分失败：成功 ${ids.length - failed.length} / 失败 ${failed.length}`)
    }
    checkedRowKeys.value = []
    fetchDocuments()
  } catch (err) {
    message.error('批量编译失败')
    console.error(err)
  } finally {
    batchLoading.value = false
  }
}

// P1-14: 取消批量选择
function handleClearSelection() {
  checkedRowKeys.value = []
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
      <!-- P1-14: 批量操作工具栏（仅在有选中项时显示） -->
      <div v-if="checkedRowKeys.length > 0" class="batch-toolbar">
        <NSpace align="center" size="medium">
          <span class="batch-count">已选 {{ checkedRowKeys.length }} 项</span>
          <NPopconfirm @positive-click="handleBatchDelete">
            <template #trigger>
              <NButton type="error" :loading="batchLoading" :disabled="batchLoading">
                批量删除
              </NButton>
            </template>
            确定删除选中的 {{ checkedRowKeys.length }} 个文档？此操作不可撤销
          </NPopconfirm>
          <NButton
            type="primary"
            :loading="batchLoading"
            :disabled="batchLoading"
            @click="handleBatchCompile"
          >
            批量编译为 Wiki
          </NButton>
          <NButton :disabled="batchLoading" @click="handleClearSelection">
            取消选择
          </NButton>
        </NSpace>
      </div>

      <NDataTable
        v-model:checked-row-keys="checkedRowKeys"
        :columns="columns"
        :data="documents"
        :loading="loading"
        :row-key="(row: DocumentMeta) => row.id"
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

    <NDrawer v-model:show="drawerVisible" :width="720" placement="right">
      <NDrawerContent title="文档详情" :closable="true">
        <template v-if="currentDoc">
          <NTabs v-model:value="drawerTab" type="line" animated>
            <!-- Tab 1: 文档信息 -->
            <NTabPane name="info" tab="文档信息">
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
            </NTabPane>

            <!-- Tab 2: 内容预览 -->
            <NTabPane name="content" tab="内容预览">
              <div class="content-section">
                <NSpin v-if="docContentLoading" />
                <div v-else-if="docContent" class="doc-content">
                  <pre>{{ docContent }}</pre>
                </div>
                <NEmpty v-else description="暂无内容" size="small" />
              </div>
            </NTabPane>

            <!-- Tab 3: P3-3 处理流水线 -->
            <NTabPane name="pipeline" tab="处理流水线">
              <div class="pipeline-section">
                <NProgress
                  v-if="pipelineLoading"
                  :percentage="pipelineProgress"
                  :indicator-placement="'inside'"
                  :height="24"
                  :border-radius="4"
                  style="margin-bottom: 20px"
                />
                <NSteps
                  v-if="pipelineSteps.length > 0"
                  :current="pipelineSteps.filter(s => s.status === 'done').length"
                  :status="pipelineSteps.some(s => s.status === 'error') ? 'error' : 'process'"
                  vertical
                >
                  <NStep
                    v-for="step in pipelineSteps"
                    :key="step.name"
                    :title="step.label"
                    :status="
                      step.status === 'error' ? 'error' :
                      step.status === 'running' ? 'process' :
                      step.status === 'done' ? 'finish' : 'wait'
                    "
                  >
                    <template v-if="step.status === 'done' && step.duration_ms" #default>
                      耗时 {{ (step.duration_ms / 1000).toFixed(1) }}s
                    </template>
                    <template v-if="step.status === 'error' && step.error" #default>
                      <span class="step-error">{{ step.error }}</span>
                    </template>
                    <!-- P2-5.5: compile 步骤子进度（逐实体编译） -->
                    <template
                      v-if="step.status === 'running' && step.subProgress && step.subProgress.total > 0"
                      #default
                    >
                      <span class="step-subprogress">
                        编译中 {{ step.subProgress.current }}/{{ step.subProgress.total }}
                        <span v-if="step.subProgress.currentEntity">
                          — {{ step.subProgress.currentEntity }}
                        </span>
                      </span>
                    </template>
                  </NStep>
                </NSteps>
                <NEmpty v-else description="暂无流水线记录，点击编译为Wiki开始" size="small" />

                <!-- P3-3: 编译结果摘要 -->
                <div v-if="pipelineResult" class="compile-result">
                  <div class="result-title">编译结果</div>
                  <NDescriptions :column="2" bordered size="small">
                    <NDescriptionsItem label="新建页面">
                      {{ pipelineResult.pages_created ?? 0 }}
                    </NDescriptionsItem>
                    <NDescriptionsItem label="更新页面">
                      {{ pipelineResult.pages_updated ?? 0 }}
                    </NDescriptionsItem>
                    <NDescriptionsItem label="生成 Slug" :span="2">
                      <NTag v-for="slug in (pipelineResult.slugs || [])" :key="slug" size="tiny" style="margin-right:4px">
                        {{ slug }}
                      </NTag>
                      <span v-if="!pipelineResult.slugs?.length">无</span>
                    </NDescriptionsItem>
                  </NDescriptions>
                </div>
              </div>
            </NTabPane>
          </NTabs>
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

/* P1-14: 批量操作工具栏 */
.batch-toolbar {
  margin-bottom: 12px;
  padding: 10px 12px;
  background: var(--n-color-target, #f0f9ff);
  border: 1px solid var(--n-border-color, #e5e7eb);
  border-radius: 6px;
}

.batch-toolbar .batch-count {
  font-size: 13px;
  font-weight: 500;
  color: var(--n-text-color, #111827);
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

/* P3-3: 流水线面板 */
.pipeline-section {
  padding: 8px 0;
}

.step-error {
  color: var(--n-error-color, #d03050);
  font-size: 12px;
}

.step-subprogress {
  color: var(--n-text-color-3, #999);
  font-size: 12px;
}

.compile-result {
  margin-top: 24px;
  padding: 16px;
  background: var(--n-color-target, #f0f9ff);
  border-radius: 8px;
  border: 1px solid var(--n-border-color, #e5e7eb);
}

.compile-result .result-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--n-text-color, #111827);
}
</style>
