<script setup lang="ts">
import { ref, computed, onMounted, watch, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NInput,
  NButton,
  NUpload,
  NTag,
  NSpace,
  NStatistic,
  NGrid,
  NGi,
  NCollapse,
  NCollapseItem,
  NEmpty,
  NAlert,
  NDivider,
  NCode,
  NCheckbox,
  NSteps,
  NStep,
  NProgress,
  NTabs,
  NTabPane,
  NDataTable,
  NModal,
  NForm,
  NFormItem,
  NSlider,
  useMessage,
} from 'naive-ui'
import type { UploadCustomRequestOptions, DataTableColumns } from 'naive-ui'
import { listDocuments, parseDocument } from '@/api/documents'
import { getCompileTrace, recompileSection, updateWikiPage } from '@/api/wiki'
import { useSse } from '@/composables/useSse'
import type { SseEvent } from '@/composables/useSse'
import { formatFileSize } from '@/utils/format'
import type { CompileTraceResponse, SectionTrace, DocumentMeta } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const { subscribe } = useSse()

// ========== 阶段状态机 ==========
type Phase = 'input' | 'compiling' | 'done'
const phase = ref<Phase>('input')

// ========== 第一步：文档来源 ==========
const sourceTab = ref<'upload' | 'existing'>('upload')

// --- 上传 ---
const uploadLoading = ref(false)

function handleUpload({ file, onFinish, onError }: UploadCustomRequestOptions) {
  const fileName = file.name
  const ext = fileName.split('.').pop()?.toLowerCase() || ''
  const fmt = ext === 'md' ? 'markdown' : ext
  const formData = new FormData()
  formData.append('file', file.file as File)

  uploadLoading.value = true
  parseDocument(fmt, formData)
    .then((res) => {
      message.success('上传成功')
      onFinish()
      selectedDocId.value = res.doc_id
      // 自动开始编译
      startCompile()
    })
    .catch((err) => {
      message.error('上传失败')
      console.error(err)
      onError()
    })
    .finally(() => {
      uploadLoading.value = false
    })
}

// --- 已有文档选择 ---
const existingDocs = ref<DocumentMeta[]>([])
const existingDocsLoading = ref(false)
const docSearchText = ref('')
const selectedDocId = ref('')

const docColumns: DataTableColumns<DocumentMeta> = [
  { title: '文件名', key: 'filename', ellipsis: { tooltip: true } },
  {
    title: '格式',
    key: 'format',
    width: 100,
    render: (row) => h(NTag, { size: 'small', bordered: false }, () => row.format.toUpperCase()),
  },
  {
    title: '大小',
    key: 'size',
    width: 100,
    render: (row) => h('span', {}, formatFileSize(row.size)),
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) => {
      const typeMap: Record<string, 'success' | 'info' | 'warning' | 'error'> = {
        parsed: 'success', parsing: 'warning', uploaded: 'info', failed: 'error',
      }
      return h(NTag, { size: 'small', type: typeMap[row.status] || 'info', bordered: false }, () => row.status)
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    render: (row) =>
      h(
        NButton,
        {
          size: 'small',
          type: selectedDocId.value === row.id ? 'primary' : 'default',
          onClick: () => {
            selectedDocId.value = row.id
          },
        },
        () => (selectedDocId.value === row.id ? '已选择' : '选择'),
      ),
  },
]

const filteredDocs = computed(() => {
  if (!docSearchText.value.trim()) return existingDocs.value
  const q = docSearchText.value.toLowerCase()
  return existingDocs.value.filter(
    (d) => d.filename.toLowerCase().includes(q) || d.id.toLowerCase().includes(q),
  )
})

async function loadExistingDocs() {
  existingDocsLoading.value = true
  try {
    const res = await listDocuments({ limit: 200 })
    existingDocs.value = res.documents
  } catch {
    message.error('加载文档列表失败')
  } finally {
    existingDocsLoading.value = false
  }
}

// ========== 第二步：编译进度 ==========
interface PipelineStep {
  name: string
  label: string
  status: 'pending' | 'running' | 'done' | 'error'
  duration_ms?: number | null
  error?: string | null
  subProgress?: { current: number; total: number; currentEntity: string } | null
}

const compiling = ref(false)
const compileProgress = ref(0)
const compileSteps = ref<PipelineStep[]>([
  { name: 'parse', label: '解析文档', status: 'pending' },
  { name: 'extract', label: '知识抽取', status: 'pending' },
  { name: 'compile', label: 'LLM 编译 Wiki', status: 'pending' },
  { name: 'index', label: '重建索引', status: 'pending' },
])
const compileResult = ref<{
  pages_created?: number
  pages_updated?: number
  pages_unchanged?: number
  slugs?: string[]
  errors?: string[]
  paragraph_count?: number
} | null>(null)

const stepIndex: Record<string, number> = { parse: 0, extract: 1, compile: 2, index: 3 }

function resetSteps() {
  compileSteps.value = [
    { name: 'parse', label: '解析文档', status: 'pending' },
    { name: 'extract', label: '知识抽取', status: 'pending' },
    { name: 'compile', label: 'LLM 编译 Wiki', status: 'pending' },
    { name: 'index', label: '重建索引', status: 'pending' },
  ]
  compileProgress.value = 0
  compileResult.value = null
}

function startCompile() {
  const docId = selectedDocId.value
  if (!docId) {
    message.warning('请先选择或上传文档')
    return
  }

  compiling.value = true
  phase.value = 'compiling'
  resetSteps()

  subscribe(`/llm-wiki/recompile/${docId}/stream?force=true`, {
    onEvent: (evt: SseEvent) => {
      if (evt.type === 'step_start') {
        const step = evt.data.step as string
        const idx = stepIndex[step] ?? 0
        if (step in stepIndex) {
          compileSteps.value[idx].status = 'running'
          compileProgress.value = (idx / 4) * 100
        }
      } else if (evt.type === 'step_done') {
        const step = evt.data.step as string
        if (step === 'cancelled') return
        const idx = stepIndex[step]
        if (idx !== undefined) {
          compileSteps.value[idx].status = 'done'
          compileSteps.value[idx].duration_ms = evt.data.duration_ms ?? null
          compileProgress.value = ((idx + 1) / 4) * 100
          if (step === 'compile') {
            compileResult.value = evt.data
          }
        }
      } else if (evt.type === 'page_start') {
        const data = evt.data
        compileSteps.value[2].subProgress = {
          current: data.index ?? 0,
          total: data.total ?? 0,
          currentEntity: data.entity ?? '',
        }
      } else if (evt.type === 'page_done') {
        const data = evt.data
        const sp = compileSteps.value[2].subProgress
        if (sp) {
          sp.current = (data.index ?? sp.current) + 1
          sp.currentEntity = ''
        }
      } else if (evt.type === 'progress') {
        const percent = evt.data.percent as number | undefined
        if (typeof percent === 'number' && percent > 0) {
          compileProgress.value = 50 + (percent / 100) * 25
        }
      } else if (evt.type === 'done') {
        compileProgress.value = 100
        compiling.value = false
        compileSteps.value[2].subProgress = null
        phase.value = 'done'
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
        // 直接从 done 事件获取管道追踪数据
        const pt = evt.data.pipeline_trace
        if (pt) {
          traceData.value = {
            doc_id: pt.doc_id,
            doc_title: pt.doc_title,
            available: true,
            summary: {
              duration_ms: pt.duration_ms,
              total_sections: pt.total_sections,
              total_raw_chars: pt.total_raw_chars,
              total_compiled_chars: pt.total_compiled_chars,
              sections_with_children: pt.sections_with_children,
              llm_success_count: pt.llm_success_count,
              llm_fail_count: pt.llm_fail_count,
            },
            sections: pt.sections,
          }
        } else {
          // 回退到单独调用
          loadTraceData(docId)
        }
      } else if (evt.type === 'error') {
        compiling.value = false
        phase.value = 'input'
        message.error('编译失败：' + (evt.data.message || '未知错误'))
        const step = evt.data.step as string
        const idx = stepIndex[step]
        if (idx !== undefined) {
          compileSteps.value[idx].status = 'error'
          compileSteps.value[idx].error = evt.data.message
        }
      }
    },
    onError: (err: string) => {
      compiling.value = false
      phase.value = 'input'
      message.error('编译连接失败：' + err)
    },
  })
}

// ========== 第三步：编译结果 ==========
const traceData = ref<CompileTraceResponse | null>(null)
const traceLoading = ref(false)
const showOnlyWithDiffs = ref(false)

async function loadTraceData(docId: string) {
  traceLoading.value = true
  try {
    traceData.value = await getCompileTrace(docId, true)
  } catch {
    // 静默失败
  } finally {
    traceLoading.value = false
  }
}

const filteredSections = computed(() => {
  if (!traceData.value?.sections) return []
  if (!showOnlyWithDiffs.value) return traceData.value.sections
  return traceData.value.sections.filter(hasDiff)
})

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

function formatChars(n: number): string {
  if (n >= 10000) return `${(n / 1000).toFixed(1)}k`
  if (n >= 1000) return `${(n / 1000).toFixed(2)}k`
  return String(n)
}

function calcReduction(raw: number, compiled: number): string {
  if (raw === 0) return '0%'
  const pct = ((raw - compiled) / raw) * 100
  return pct > 0 ? `-${pct.toFixed(1)}%` : `+${Math.abs(pct).toFixed(1)}%`
}

// 查看某个 wiki 页面
function viewWikiPage(slug: string) {
  router.push({ name: 'wiki', query: { slug } })
}

function resetAll() {
  phase.value = 'input'
  selectedDocId.value = ''
  compileResult.value = null
  traceData.value = null
  resetSteps()
}

// ========== 章节操作：重新生成 + 编辑保存 ==========

// 当前编辑的章节 slug
const editingSlug = ref<string | null>(null)
const editingContent = ref('')

// 重新生成弹窗
const recompileDialogVisible = ref(false)
const recompileTarget = ref<{ slug: string; title: string } | null>(null)
const recompileTemperature = ref(0.2)
const recompileSystemPrompt = ref('')
const recompileUserPrompt = ref('')
const recompilingSlug = ref<string | null>(null)

function startEdit(section: SectionTrace) {
  editingSlug.value = section.slug
  editingContent.value = section.compiled_content
}

function cancelEdit() {
  editingSlug.value = null
  editingContent.value = ''
}

async function saveEdit(section: SectionTrace) {
  if (!editingSlug.value) return
  const slug = editingSlug.value
  try {
    // 构建含 frontmatter 的完整内容
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
    // 更新本地数据
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
  if (!recompileTarget.value || !selectedDocId.value) return
  const { slug } = recompileTarget.value
  recompilingSlug.value = slug
  try {
    const result = await recompileSection({
      doc_id: selectedDocId.value,
      slug,
      temperature: recompileTemperature.value || undefined,
      system_prompt: recompileSystemPrompt.value || undefined,
      user_prompt: recompileUserPrompt.value || undefined,
    })
    message.success(`重新生成成功（${result.outcome}）`)
    // 更新本地数据
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

// ========== 初始化 ==========
onMounted(() => {
  // 从 URL 参数读取 doc_id（从 DocumentsView 跳转时带入）
  const qDocId = route.query.doc_id as string
  if (qDocId) {
    selectedDocId.value = qDocId
    sourceTab.value = 'existing'
    startCompile()
  }
  loadExistingDocs()
})

// 切换 sourceTab 时加载已有文档
watch(sourceTab, (val) => {
  if (val === 'existing' && existingDocs.value.length === 0) {
    loadExistingDocs()
  }
})
</script>

<template>
  <PageHeader
    title="LLM 编译流水线"
    description="上传文档 → 实时编译 → 查看章节级处理前后对比与统计"
  />

  <!-- ==================== 阶段导航 ==================== -->
  <NSteps
    :current="phase === 'input' ? 0 : phase === 'compiling' ? 1 : 2"
    :status="phase === 'done' ? 'finish' : 'process'"
    style="margin-bottom: 24px"
  >
    <NStep title="选择文档" description="上传或从已有文档选择" />
    <NStep title="实时编译" description="SSE 流式进度追踪" />
    <NStep title="查看结果" description="章节对比与统计" />
  </NSteps>

  <!-- ==================== 第一步：选择文档 ==================== -->
  <div v-if="phase === 'input'">
    <NTabs v-model:value="sourceTab" type="card" animated>
      <NTabPane name="upload" tab="上传新文件">
        <div style="padding: 24px 0">
          <NUpload
            :show-file-list="false"
            :custom-request="handleUpload"
            drag
            style="width: 100%"
          >
            <div style="padding: 48px 24px; text-align: center">
              <div style="font-size: 36px; margin-bottom: 12px">📤</div>
              <div style="font-size: 16px; font-weight: 500; margin-bottom: 8px">
                点击或拖拽文件到此处上传
              </div>
              <div style="font-size: 13px; color: #999">
                支持 md、docx、xlsx、pdf、html、txt、sql 等格式
              </div>
              <div style="font-size: 13px; color: #999">
                上传后自动触发编译流水线
              </div>
            </div>
          </NUpload>
        </div>
      </NTabPane>

      <NTabPane name="existing" tab="从已有文档选择">
        <div style="padding: 16px 0">
          <NSpace vertical :size="12">
            <NSpace>
              <NInput
                v-model:value="docSearchText"
                placeholder="搜索文件名或文档 ID..."
                clearable
                style="width: 320px"
              />
              <NButton size="small" @click="loadExistingDocs" :loading="existingDocsLoading">
                刷新列表
              </NButton>
            </NSpace>

            <NDataTable
              :columns="docColumns"
              :data="filteredDocs"
              :loading="existingDocsLoading"
              :row-key="(row: DocumentMeta) => row.id"
              :bordered="false"
              size="small"
              :max-height="320"
              virtual-scroll
            >
              <template #empty>
                <NEmpty description="暂无文档" size="small" />
              </template>
            </NDataTable>

            <NSpace justify="end">
              <NButton
                type="primary"
                :disabled="!selectedDocId || compiling"
                :loading="compiling"
                @click="startCompile"
              >
                开始编译
              </NButton>
            </NSpace>
          </NSpace>
        </div>
      </NTabPane>
    </NTabs>
  </div>

  <!-- ==================== 第二步：编译进度 ==================== -->
  <div v-if="phase === 'compiling' || phase === 'done'">
    <NCard title="编译进度" size="small" style="margin-bottom: 16px">
      <NProgress
        :percentage="compileProgress"
        :indicator-placement="'inside'"
        :height="24"
        :border-radius="4"
        :status="phase === 'done' ? 'success' : 'default'"
        style="margin-bottom: 20px"
      />

      <NSteps
        :current="compileSteps.filter(s => s.status === 'done').length"
        :status="compileSteps.some(s => s.status === 'error') ? 'error' : 'process'"
        vertical
      >
        <NStep
          v-for="step in compileSteps"
          :key="step.name"
          :title="step.label"
          :status="
            step.status === 'error' ? 'error' :
            step.status === 'running' ? 'process' :
            step.status === 'done' ? 'finish' : 'wait'
          "
        >
          <template v-if="step.status === 'done' && step.duration_ms" #default>
            <span style="color: #18a058">耗时 {{ (step.duration_ms / 1000).toFixed(1) }}s</span>
          </template>
          <template v-if="step.status === 'error' && step.error" #default>
            <span style="color: #d03050">{{ step.error }}</span>
          </template>
          <template
            v-if="step.status === 'running' && step.subProgress && step.subProgress.total > 0"
            #default
          >
            <span style="color: #2080f0">
              编译中 {{ step.subProgress.current }}/{{ step.subProgress.total }}
              <span v-if="step.subProgress.currentEntity">
                — {{ step.subProgress.currentEntity }}
              </span>
            </span>
          </template>
        </NStep>
      </NSteps>

      <!-- 编译结果摘要 -->
      <template v-if="compileResult && phase === 'done'">
        <NDivider />
        <NGrid :cols="4" :x-gap="12" responsive="screen">
          <NGi>
            <NStatistic label="新建页面" :value="compileResult.pages_created ?? 0" />
          </NGi>
          <NGi>
            <NStatistic label="更新页面" :value="compileResult.pages_updated ?? 0" />
          </NGi>
          <NGi>
            <NStatistic label="未变页面" :value="compileResult.pages_unchanged ?? 0" />
          </NGi>
          <NGi>
            <NStatistic label="段落数" :value="compileResult.paragraph_count ?? 0" />
          </NGi>
        </NGrid>
        <div v-if="compileResult.slugs?.length" style="margin-top: 12px">
          <span style="font-size: 13px; color: #666; margin-right: 8px">生成页面：</span>
          <NTag
            v-for="slug in compileResult.slugs"
            :key="slug"
            size="tiny"
            :bordered="false"
            type="info"
            style="margin-right: 4px; cursor: pointer"
            @click="viewWikiPage(slug)"
          >
            {{ slug }}
          </NTag>
        </div>
      </template>
    </NCard>
  </div>

  <!-- ==================== 第三步：编译结果（章节对比） ==================== -->
  <template v-if="phase === 'done'">
    <!-- 管道追踪汇总统计 -->
    <NCard
      v-if="traceData?.available && traceData.summary"
      title="管道追踪统计"
      size="small"
      style="margin-bottom: 16px"
    >
      <NGrid :x-gap="12" :y-gap="8" :cols="7" responsive="screen">
        <NGi>
          <NStatistic label="拆分章节" :value="traceData.summary.total_sections" />
        </NGi>
        <NGi>
          <NStatistic label="含子章节" :value="traceData.summary.sections_with_children" />
        </NGi>
        <NGi>
          <NStatistic label="原始字符" :value="formatChars(traceData.summary.total_raw_chars)" />
        </NGi>
        <NGi>
          <NStatistic label="编译后字符" :value="formatChars(traceData.summary.total_compiled_chars)" />
        </NGi>
        <NGi>
          <NStatistic label="LLM 成功" :value="traceData.summary.llm_success_count" />
        </NGi>
        <NGi>
          <NStatistic label="LLM 失败" :value="traceData.summary.llm_fail_count" />
        </NGi>
        <NGi>
          <NStatistic label="总耗时" :value="formatMs(traceData.summary.duration_ms)" />
        </NGi>
      </NGrid>
    </NCard>

    <!-- 章节对比 -->
    <NCard v-if="traceData?.available" size="small">
      <template #header>
        <NSpace justify="space-between" align="center">
          <span style="font-weight: 600">
            章节处理对比（共 {{ filteredSections.length }} 个章节）
          </span>
          <NCheckbox v-model:checked="showOnlyWithDiffs">
            仅显示有差异的章节
          </NCheckbox>
        </NSpace>
      </template>

      <NCollapse v-if="filteredSections.length > 0">
        <NCollapseItem
          v-for="(section, idx) in filteredSections"
          :key="section.slug || idx"
          :name="section.slug || `section-${idx}`"
        >
          <template #header>
            <NSpace align="center" :wrap="false">
              <NTag
                :bordered="false"
                size="small"
                :type="getLevelType(section.level)"
                style="font-weight: 600"
              >
                {{ getLevelLabel(section.level) }}
              </NTag>
              <span style="font-weight: 500">{{ section.title || '(无标题)' }}</span>
              <NTag size="small" :bordered="false">
                {{ section.slug }}
              </NTag>
              <NTag
                size="small"
                :bordered="false"
                :type="section.llm_success ? 'success' : 'error'"
              >
                {{ section.llm_success ? 'LLM 成功' : 'LLM 失败' }}
              </NTag>
              <span style="font-size: 12px; color: #999">
                {{ formatMs(section.processing_time_ms) }}
              </span>
              <span style="font-size: 12px; color: #999">
                {{ section.raw_chars }} → {{ section.compiled_chars }} 字符
                ({{ calcReduction(section.raw_chars, section.compiled_chars) }})
              </span>
              <NTag
                v-if="section.children_count > 0"
                size="small"
                :bordered="false"
                type="info"
              >
                {{ section.children_count }} 子章节
              </NTag>
              <NTag
                v-if="hasDiff(section)"
                size="small"
                :bordered="false"
                type="warning"
              >
                有变更
              </NTag>
              <div style="flex: 1" />
              <!-- 操作按钮 -->
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
          </template>

          <NDivider style="margin: 0 0 8px" />

          <NGrid :cols="2" :x-gap="12" responsive="screen">
            <NGi>
              <div style="margin-bottom: 4px; font-weight: 600; color: #d03050">
                处理前（原始内容）
              </div>
              <NCode
                :code="section.raw_content || '(空)'"
                language="markdown"
                word-wrap
                style="max-height: 400px; overflow: auto"
              />
            </NGi>
            <NGi>
              <div style="margin-bottom: 4px; font-weight: 600; color: #18a058">
                处理后（LLM 编译）
                <span v-if="editingSlug === section.slug" style="color: #f0a020; font-size: 12px">
                  （编辑模式）
                </span>
              </div>
              <template v-if="editingSlug === section.slug">
                <NInput
                  v-model:value="editingContent"
                  type="textarea"
                  :autosize="{ minRows: 8, maxRows: 20 }"
                  style="margin-bottom: 8px"
                />
                <NSpace>
                  <NButton size="small" type="primary" @click="saveEdit(section)">
                    保存
                  </NButton>
                  <NButton size="small" @click="cancelEdit()">取消</NButton>
                </NSpace>
              </template>
              <NCode
                v-else
                :code="section.compiled_content || '(空)'"
                language="markdown"
                word-wrap
                style="max-height: 400px; overflow: auto"
              />
            </NGi>
          </NGrid>
        </NCollapseItem>
      </NCollapse>

      <NEmpty v-else description="所有章节处理后无差异" />
    </NCard>

    <!-- 无追踪数据 -->
    <NAlert
      v-if="traceData && !traceData.available"
      type="info"
      :title="traceData.message || '该文档无管道追踪数据'"
      style="margin-top: 16px"
    />

    <!-- 底部操作 -->
    <NSpace justify="center" style="margin-top: 24px">
      <NButton @click="resetAll">重新编译</NButton>
      <NButton type="primary" @click="router.push({ name: 'wiki' })">
        查看 Wiki 页面
      </NButton>
    </NSpace>
  </template>

  <!-- ==================== 重新生成弹窗 ==================== -->
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
          <NInput
            v-model:value="recompileSystemPrompt"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 8 }"
            placeholder="留空使用默认系统提示词"
          />
        </NFormItem>

        <NFormItem label="自定义用户提示词（可选）">
          <NInput
            v-model:value="recompileUserPrompt"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 8 }"
            placeholder="留空使用默认用户提示词（含章节原文）"
          />
        </NFormItem>
      </NForm>
    </template>

    <template #footer>
      <NSpace justify="end">
        <NButton @click="closeRecompileDialog">取消</NButton>
        <NButton
          type="primary"
          :loading="!!recompilingSlug"
          @click="doRecompile"
        >
          重新生成
        </NButton>
      </NSpace>
    </template>
  </NModal>
</template>