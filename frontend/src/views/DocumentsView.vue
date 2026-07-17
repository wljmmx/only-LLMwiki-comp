<script setup lang="ts">
import { ref, onMounted, watch, h } from 'vue'
import { useRouter } from 'vue-router'
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
  NCode,
  NCheckbox,
  NModal,
  NForm,
  NFormItem,
  NSlider,
  useMessage,
} from 'naive-ui'
import type { UploadCustomRequestOptions } from 'naive-ui'
import { listDocuments, deleteDocument, parseDocument, getDocumentContent, searchDocuments, getPipelineStatus, compileToWiki } from '@/api/documents'
import { getCompileTrace, recompileSection, updateWikiPage } from '@/api/wiki'
import { formatFileSize, formatDateTime as formatDateTimeUtil } from '@/utils/format'
import type { DocumentMeta, CompileTraceResponse, SectionTrace } from '@/types/api'

const message = useMessage()
const router = useRouter()

const loading = ref(false)
const documents = ref<DocumentMeta[]>([])
const total = ref(0)
const limit = ref(10)
const offset = ref(0)
const searchText = ref('')
const isSearching = ref(false)
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

// 章节对比相关状态
const traceData = ref<CompileTraceResponse | null>(null)
const traceLoading = ref(false)
const showOnlyWithDiffs = ref(false)
const editingSlug = ref<string | null>(null)
const editingContent = ref('')

// 重新生成弹窗
const recompileDialogVisible = ref(false)
const recompileTarget = ref<{ slug: string; title: string } | null>(null)
const recompileTemperature = ref(0.2)
const recompileSystemPrompt = ref('')
const recompileUserPrompt = ref('')
const recompilingSlug = ref<string | null>(null)

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
                onClick: () => router.push({ name: 'pipeline', query: { doc_id: row.id } }),
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

async function handleView(doc: DocumentMeta) {
  currentDoc.value = doc
  drawerVisible.value = true
  drawerTab.value = 'info'
  docContent.value = ''
  docContentLoading.value = true
  pipelineSteps.value = []
  pipelineLoading.value = false
  pipelineResult.value = null
  traceData.value = null
  traceLoading.value = false
  editingSlug.value = null
  editingContent.value = ''
  try {
    const res = await getDocumentContent(doc.id)
    docContent.value = res.content
  } catch (err) {
    message.error('获取文档内容失败')
    console.error(err)
  } finally {
    docContentLoading.value = false
  }

  // P3-3: 异步加载流水线状态和章节追踪数据
  loadPipelineStatus(doc.id)
  loadTraceData(doc.id)
}

async function loadPipelineStatus(docId: string) {
  try {
    const res = await getPipelineStatus(docId)
    pipelineSteps.value = (res.steps || []) as PipelineStep[]
  } catch {
    // 静默失败，流水线状态非关键
  }
}

async function loadTraceData(docId: string) {
  if (!docId) return
  traceLoading.value = true
  try {
    const res = await getCompileTrace(docId, false)
    traceData.value = res
  } catch {
    // 静默失败，追踪数据非关键
  } finally {
    traceLoading.value = false
  }
}

function hasDiff(s: SectionTrace): boolean {
  return s.raw_content.trim() !== s.compiled_content.trim()
}

function getLevelLabel(level: number): string {
  return `H${level}`
}

function getLevelType(level: number): 'success' | 'info' | 'warning' {
  const types: Record<number, 'success' | 'info' | 'warning'> = { 1: 'success', 2: 'info', 3: 'warning' }
  return types[level] || 'info'
}

function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  if (ms >= 1) return `${ms.toFixed(0)}ms`
  return '<1ms'
}

function calcReduction(raw: number, compiled: number): string {
  if (raw === 0) return '0%'
  const pct = ((raw - compiled) / raw) * 100
  return pct > 0 ? `-${pct.toFixed(1)}%` : `+${Math.abs(pct).toFixed(1)}%`
}

function startEdit(section: SectionTrace) {
  editingSlug.value = section.slug
  editingContent.value = section.compiled_content
}

function cancelEdit() {
  editingSlug.value = null
  editingContent.value = ''
}

async function saveEdit(section: SectionTrace) {
  if (!editingSlug.value || !currentDoc.value) return
  const slug = editingSlug.value
  try {
    const fm = `---
slug: ${slug}
title: ${section.title}
type: concept
tags: []
review_status: auto
edited_by_human: true
---
`
    const fullContent = fm + editingContent.value
    await updateWikiPage(slug, {
      content: fullContent,
      title: section.title,
      change_summary: '用户手工编辑',
    })
    message.success('保存成功')
    section.compiled_content = editingContent.value
    section.compiled_chars = editingContent.value.length
    cancelEdit()
  } catch (e: any) {
    message.error('保存失败：' + (e?.response?.data?.detail || e?.message || '未知错误'))
  }
}

function openRecompileDialog(section: SectionTrace) {
  recompileTarget.value = { slug: section.slug, title: section.title }
  recompileTemperature.value = 0.2
  recompileSystemPrompt.value = ''
  recompileUserPrompt.value = ''
  recompileDialogVisible.value = true
}

function closeRecompileDialog() {
  recompileDialogVisible.value = false
  recompileTarget.value = null
}

async function doRecompile() {
  if (!recompileTarget.value || !currentDoc.value) return
  const { slug } = recompileTarget.value
  recompilingSlug.value = slug
  try {
    const result = await recompileSection({
      doc_id: currentDoc.value.id,
      slug,
      temperature: recompileTemperature.value || undefined,
      system_prompt: recompileSystemPrompt.value || undefined,
      user_prompt: recompileUserPrompt.value || undefined,
    })
    message.success(`重新生成成功（${result.outcome}）`)
    const section = traceData.value?.sections?.find((s) => s.slug === slug)
    if (section) {
      section.compiled_content = result.compiled_content
      section.compiled_chars = result.compiled_chars
    }
    closeRecompileDialog()
  } catch (e: any) {
    message.error('重新生成失败：' + (e?.response?.data?.detail || e?.message || '未知错误'))
  } finally {
    recompilingSlug.value = null
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

                <!-- 章节对比 -->
                <div v-if="traceData?.available" class="sections-compare">
                  <div class="sections-header">
                    <NSpace justify="space-between" align="center">
                      <span style="font-weight: 600">章节处理对比</span>
                      <NCheckbox v-model:checked="showOnlyWithDiffs">仅显示有差异</NCheckbox>
                    </NSpace>
                  </div>
                  <NSpin v-if="traceLoading" />
                  <div v-else-if="traceData.sections && traceData.sections.length > 0" class="sections-list">
                    <div
                      v-for="(section, idx) in (showOnlyWithDiffs ? traceData.sections.filter(hasDiff) : traceData.sections)"
                      :key="section.slug || idx"
                      class="section-item"
                    >
                      <div class="section-header">
                        <NSpace align="center" :wrap="false">
                          <NTag :bordered="false" size="small" :type="getLevelType(section.level)">
                            {{ getLevelLabel(section.level) }}
                          </NTag>
                          <span style="font-weight: 500">{{ section.title || '(无标题)' }}</span>
                          <NTag size="small" :bordered="false">{{ section.slug }}</NTag>
                          <NTag
                            size="small"
                            :bordered="false"
                            :type="section.llm_success ? 'success' : 'error'"
                          >
                            {{ section.llm_success ? 'LLM 成功' : 'LLM 失败' }}
                          </NTag>
                          <span style="font-size: 12px; color: #999">{{ formatMs(section.processing_time_ms) }}</span>
                          <span style="font-size: 12px; color: #999">
                            {{ section.raw_chars }} → {{ section.compiled_chars }} 字符
                            ({{ calcReduction(section.raw_chars, section.compiled_chars) }})
                          </span>
                          <NTag v-if="hasDiff(section)" size="small" :bordered="false" type="warning">有变更</NTag>
                          <div style="flex: 1" />
                          <NButton
                            size="tiny"
                            quaternary
                            :loading="recompilingSlug === section.slug"
                            @click.stop="openRecompileDialog(section)"
                          >
                            重新生成
                          </NButton>
                          <NButton
                            size="tiny"
                            quaternary
                            :type="editingSlug === section.slug ? 'warning' : 'default'"
                            @click.stop="editingSlug === section.slug ? cancelEdit() : startEdit(section)"
                          >
                            {{ editingSlug === section.slug ? '取消编辑' : '编辑' }}
                          </NButton>
                        </NSpace>
                      </div>
                      <div class="section-content">
                        <div class="content-col">
                          <div class="col-label" style="color: #d03050">处理前（原始内容）</div>
                          <NCode
                            :code="section.raw_content || '(空)'"
                            language="markdown"
                            word-wrap
                            style="max-height: 200px; overflow: auto"
                          />
                        </div>
                        <div class="content-col">
                          <div class="col-label" style="color: #18a058">
                            处理后（LLM 编译）
                            <span v-if="editingSlug === section.slug" style="color: #f0a020; font-size: 12px">（编辑模式）</span>
                          </div>
                          <template v-if="editingSlug === section.slug">
                            <textarea
                              v-model="editingContent"
                              class="edit-textarea"
                              style="min-height: 150px; margin-bottom: 8px"
                            />
                            <NSpace>
                              <NButton size="small" type="primary" @click="saveEdit(section)">保存</NButton>
                              <NButton size="small" @click="cancelEdit()">取消</NButton>
                            </NSpace>
                          </template>
                          <NCode
                            v-else
                            :code="section.compiled_content || '(空)'"
                            language="markdown"
                            word-wrap
                            style="max-height: 200px; overflow: auto"
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                  <NEmpty v-else description="所有章节处理后无差异" size="small" />
                </div>

                <!-- 无追踪数据 -->
                <div v-if="traceData && !traceData.available" class="no-trace">
                  <span style="color: #999">{{ traceData.message || '该文档无管道追踪数据' }}</span>
                </div>
              </div>
            </NTabPane>
          </NTabs>
        </template>
      </NDrawerContent>
    </NDrawer>
  </div>
<!-- 重新生成弹窗 -->
    <NModal
      v-model:show="recompileDialogVisible"
      title="重新生成章节"
      style="width: 600px"
      preset="card"
    >
      <template v-if="recompileTarget">
        <p style="margin-bottom: 16px; color: #666">
          章节：<strong>{{ recompileTarget.title }}</strong>（{{ recompileTarget.slug }}）
        </p>
        <NForm label-placement="top" size="small">
          <NFormItem label="Temperature（创造性）">
            <NSpace align="center">
              <NSlider
                v-model:value="recompileTemperature"
                :min="0"
                :max="2"
                :step="0.05"
                style="flex: 1"
              />
              <span style="width: 40px; text-align: right">{{ recompileTemperature.toFixed(2) }}</span>
            </NSpace>
            <div style="font-size: 11px; color: #999; margin-top: 4px">
              0 = 确定性输出，1 = 创造性，2 = 高度随机
            </div>
          </NFormItem>
          <NFormItem label="自定义系统提示词（可选）">
            <textarea
              v-model="recompileSystemPrompt"
              class="edit-textarea"
              placeholder="留空使用默认系统提示词"
              style="min-height: 80px"
            />
          </NFormItem>
          <NFormItem label="自定义用户提示词（可选）">
            <textarea
              v-model="recompileUserPrompt"
              class="edit-textarea"
              placeholder="留空使用默认用户提示词（含章节原文）"
              style="min-height: 80px"
            />
          </NFormItem>
        </NForm>
      </template>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="closeRecompileDialog">取消</NButton>
          <NButton type="primary" :loading="!!recompilingSlug" @click="doRecompile">
            重新生成
          </NButton>
        </NSpace>
      </template>
    </NModal>
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

.sections-compare {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--n-border-color, #e5e7eb);
}

.sections-header {
  margin-bottom: 12px;
}

.sections-list {
  max-height: 500px;
  overflow-y: auto;
}

.section-item {
  margin-bottom: 16px;
  padding: 12px;
  background: var(--n-base-color, #f9fafb);
  border-radius: 6px;
  border: 1px solid var(--n-border-color, #e5e7eb);
}

.section-header {
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--n-border-color, #e5e7eb);
}

.section-content {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.content-col {
  display: flex;
  flex-direction: column;
}

.col-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 4px;
}

.edit-textarea {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--n-border-color, #e5e7eb);
  border-radius: 6px;
  font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
  box-sizing: border-box;
}

.edit-textarea:focus {
  outline: none;
  border-color: var(--n-primary-color, #3b82f6);
}

.no-trace {
  margin-top: 16px;
  padding: 12px;
  background: var(--n-info-color-suppl, #f0f5ff);
  border-radius: 6px;
}
</style>
