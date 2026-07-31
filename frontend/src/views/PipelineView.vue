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
  NSpin,
  NTooltip,
  useMessage,
} from 'naive-ui'
import type { UploadCustomRequestOptions, DataTableColumns } from 'naive-ui'
import { listDocuments, parseDocument } from '@/api/documents'
import { getCompileTrace, recompileSection, updateWikiPage, pauseCompile, resumeCompile } from '@/api/wiki'
import { useSse } from '@/composables/useSse'
import type { SseEvent } from '@/composables/useSse'
import { formatFileSize } from '@/utils/format'
import type { CompileTraceResponse, SectionTrace, DocumentMeta } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const { subscribe, unsubscribe } = useSse()

// 保存当前 SSE 取消函数
let cancelSse: (() => void) | null = null

// 编译流水线控制
const pipelineRunId = ref<string | null>(null)
const isPaused = ref(false)

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
          onClick: () => { selectedDocId.value = row.id },
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
    existingDocs.value = res.data.items || []
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
  status: 'pending' | 'running' | 'done' | 'error' | 'skipped'
  duration_ms?: number | null
  error?: string | null
  subProgress?: { current: number; total: number; currentEntity: string } | null
  details?: string | null
  // 每个环节的产出数据
  output?: {
    // parse
    elements?: number
    heading_tree_count?: number
    heading_tree_titles?: Array<{ title: string; level: number }>
    // extract
    entities?: number
    entity_names?: string[]
    // compile
    pages?: number
    slugs?: string[]
    llm_error_count?: number
    llm_errors?: Array<{ entity: string; error: string }>
    // struct_compile
    sections?: number
    pages_created?: number
    pages_updated?: number
    // index
    index_rebuilt?: boolean
    slugs_count?: number
  } | null
  // 步骤级错误列表（每个实体的错误详情）
  errors?: Array<{ entity: string; error: string; status: string }>
}

// 章节节点（独立管道节点）
interface SectionNode {
  slug: string
  title: string
  level: number
  status: 'pending' | 'running' | 'done' | 'error'
  outcome?: string
  raw_chars?: number
  compiled_chars?: number
  llm_success?: boolean
  processing_time_ms?: number
  children_count?: number
  error?: string
  raw_content?: string
  compiled_content?: string
}

const compiling = ref(false)
const compileProgress = ref(0)
const startFromStage = ref<string | null>(null)  // P3: 从指定阶段重跑
const compileSteps = ref<PipelineStep[]>([
  { name: 'parse', label: '解析文档', status: 'pending' },
  { name: 'extract', label: '知识抽取', status: 'pending' },
  { name: 'compile', label: 'LLM 编译 Wiki', status: 'pending' },
  { name: 'struct_compile', label: '结构编译（章节处理）', status: 'pending' },
  { name: 'extract_compiled', label: '编译后实体抽取', status: 'pending' },
  { name: 'index', label: '重建索引', status: 'pending' },
])

// 章节级节点列表（独立展示每个章节的处理状态）
const sectionNodes = ref<SectionNode[]>([])

// ── 实体进度追踪（按步骤分离，避免错位）──
interface EntityProgressItem {
  name: string
  status: 'pending' | 'running' | 'done' | 'error' | 'skipped'
  error?: string
  started_at?: number
  done_at?: number
  extra?: Record<string, any>  // 扩展字段（confidence, section, entity_type 等）
}

// 知识抽取环节的实体进度（step 2）
const extractEntities = ref<EntityProgressItem[]>([])
// LLM 编译 Wiki 环节的实体进度（step 3）
const compileEntities = ref<EntityProgressItem[]>([])
const compileStepStartTime = ref<number>(0)

// 当前正在编译的实体名（用于醒目标识）
const currentCompileEntity = computed(() => {
  const running = compileEntities.value.find(e => e.status === 'running')
  return running?.name ?? ''
})

// 根据步骤名获取对应的实体进度列表
function getStepEntityList(step: typeof compileSteps.value[0]): EntityProgressItem[] {
  if (step.name === 'extract') return extractEntities.value
  if (step.name === 'compile') return compileEntities.value
  return []
}

const compileResult = ref<{
  pages_created?: number
  pages_updated?: number
  pages_unchanged?: number
  slugs?: string[]
  errors?: string[]
  paragraph_count?: number
} | null>(null)

const stepIndex: Record<string, number> = { parse: 0, extract: 1, compile: 2, struct_compile: 3, extract_compiled: 4, index: 5 }

function resetSteps() {
  compileSteps.value = [
    { name: 'parse', label: '解析文档', status: 'pending' },
    { name: 'extract', label: '知识抽取', status: 'pending' },
    { name: 'compile', label: 'LLM 编译 Wiki', status: 'pending' },
    { name: 'struct_compile', label: '结构编译（章节处理）', status: 'pending' },
    { name: 'extract_compiled', label: '编译后实体抽取', status: 'pending' },
    { name: 'index', label: '重建索引', status: 'pending' },
  ]
  compileProgress.value = 0
  compileResult.value = null
  sectionNodes.value = []
  pipelineRunId.value = null
  isPaused.value = false
}

// P3: 从指定阶段重跑
function restartFromStage(stageName: string) {
  startFromStage.value = stageName
  startCompile()
}

// P3: 取消编译
function cancelCompile() {
  if (cancelSse) {
    cancelSse()
    cancelSse = null
  }
  compiling.value = false
  isPaused.value = false
  // 当前运行中的步骤标记为 error
  for (const step of compileSteps.value) {
    if (step.status === 'running') {
      step.status = 'error'
      step.error = '用户取消'
    }
  }
  message.info('编译已取消')
}

async function doPause() {
  if (!pipelineRunId.value) return
  try {
    await pauseCompile(pipelineRunId.value)
    isPaused.value = true
    message.info('编译已暂停')
  } catch (e: any) {
    message.error('暂停失败：' + (e?.response?.data?.detail || e?.message || '未知错误'))
  }
}

async function doResume() {
  if (!pipelineRunId.value) return
  try {
    await resumeCompile(pipelineRunId.value)
    isPaused.value = false
    message.success('编译已继续')
  } catch (e: any) {
    message.error('继续失败：' + (e?.response?.data?.detail || e?.message || '未知错误'))
  }
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

  // 构建 SSE URL，支持 start_from_stage 参数
  let url = `/llm-wiki/recompile/${docId}/stream?force=true`
  const stage = startFromStage.value
  if (stage) {
    url += `&start_from_stage=${stage}`
    // 重跑时，前面阶段标记为 skipped
    const stageOrder = ['parse', 'extract', 'compile', 'struct_compile', 'extract_compiled', 'index']
    const startIdx = stageOrder.indexOf(stage)
    if (startIdx > 0) {
      for (let i = 0; i < startIdx; i++) {
        compileSteps.value[i].status = 'skipped'
        compileSteps.value[i].details = '跳过（从缓存加载）'
        compileProgress.value = (i / 6) * 100
      }
    }
  }
  startFromStage.value = null  // 重置

  // 取消之前的 SSE 连接（如果有）
  if (cancelSse) {
    cancelSse()
    cancelSse = null
  }

  cancelSse = subscribe(url, {
    onEvent: (evt: SseEvent) => {
      if (evt.type === 'run_id') {
        pipelineRunId.value = evt.data.run_id as string
      } else if (evt.type === 'step_start') {
        const step = evt.data.step as string
        const idx = stepIndex[step] ?? 0
        if (step in stepIndex) {
          compileSteps.value[idx].status = 'running'
          compileProgress.value = (idx / 6) * 100
          compileStepStartTime.value = Date.now()
          // 步骤开始时清空对应的实体进度列表
          if (step === 'extract') {
            extractEntities.value = []
            compileSteps.value[idx].details = '正在连接 LLM 服务，构建抽取上下文...'
          } else if (step === 'compile') {
            compileEntities.value = []
            compileSteps.value[idx].details = '正在连接 LLM 服务，准备编译 Wiki 页面...'
          }
        }
      } else if (evt.type === 'step_done') {
        const step = evt.data.step as string
        if (step === 'cancelled') return
        const idx = stepIndex[step]
        if (idx !== undefined) {
          compileSteps.value[idx].status = 'done'
          compileSteps.value[idx].duration_ms = evt.data.duration_ms ?? null
          compileProgress.value = ((idx + 1) / 6) * 100
          if (step === 'parse') {
            const elements = evt.data.elements ?? 0
            const headingTreeCount = evt.data.heading_tree_count ?? 0
            const headingTreeTitles = evt.data.heading_tree_titles ?? []
            compileSteps.value[idx].details = `解析完成：${elements} 个元素，${headingTreeCount} 个章节`
            compileSteps.value[idx].output = {
              elements,
              heading_tree_count: headingTreeCount,
              heading_tree_titles: headingTreeTitles,
            }
          } else if (step === 'extract') {
            const entities = evt.data.entities ?? 0
            const entityNames = evt.data.entity_names ?? []
            compileSteps.value[idx].details = `抽取完成：${entities} 个实体`
            compileSteps.value[idx].output = {
              entities,
              entity_names: entityNames,
            }
          } else if (step === 'compile') {
            const pages = evt.data.pages ?? 0
            const slugs = evt.data.slugs ?? []
            const llmErrorCount = evt.data.llm_error_count ?? 0
            const llmErrors = evt.data.llm_errors ?? []
            const message = evt.data.message ?? ''
            compileSteps.value[idx].details = message || `编译完成：${pages} 个页面`
            compileSteps.value[idx].output = {
              pages,
              slugs,
              llm_error_count: llmErrorCount,
              llm_errors: llmErrors,
            }
            // 如果有 LLM 错误，标记步骤需要审查
            if (llmErrorCount > 0) {
              compileSteps.value[idx].status = 'error'
              compileSteps.value[idx].details += ` ⚠️ ${llmErrorCount} 个 LLM 错误`
              compileSteps.value[idx].errors = llmErrors.map((e: any) => ({
                entity: e.entity,
                error: e.error,
                status: 'llm_error',
              }))
            }
          } else if (step === 'struct_compile') {
            const sections = evt.data.sections ?? 0
            const pagesCreated = evt.data.pages_created ?? 0
            const pagesUpdated = evt.data.pages_updated ?? 0
            const error = evt.data.error
            if (error) {
              compileSteps.value[idx].details = `结构编译失败：${error}`
            } else {
              compileSteps.value[idx].details = `章节处理完成：${sections} 个章节，${pagesCreated} 创建，${pagesUpdated} 更新`
            }
            compileSteps.value[idx].output = {
              sections,
              pages_created: pagesCreated,
              pages_updated: pagesUpdated,
            }
          } else if (step === 'extract_compiled') {
            const entities = evt.data.entities ?? 0
            const newEntities = evt.data.new_entities ?? 0
            const entityNames = evt.data.entity_names ?? []
            const error = evt.data.error
            if (error) {
              compileSteps.value[idx].details = `编译后抽取失败：${error}`
            } else {
              compileSteps.value[idx].details = `编译后抽取完成：${entities} 个实体（${newEntities} 个新增）`
            }
            compileSteps.value[idx].output = {
              entities,
              entity_names: entityNames,
            }
          } else if (step === 'index') {
            const indexRebuilt = evt.data.index_rebuilt ?? false
            const slugsCount = evt.data.slugs_count ?? 0
            compileSteps.value[idx].details = `索引重建${indexRebuilt ? '完成' : '跳过'}（${slugsCount} 个页面）`
            compileSteps.value[idx].output = {
              index_rebuilt: indexRebuilt,
              slugs_count: slugsCount,
            }
          } else if (step === 'compile_summary') {
            // 最终编译汇总，更新 compile 步骤的 slugs（struct_compile 可能追加了页面）
            const slugs = evt.data.slugs ?? []
            const compileIdx = stepIndex['compile']
            if (compileIdx !== undefined) {
              const compileStep = compileSteps.value[compileIdx]
              if (compileStep.output) {
                compileStep.output.slugs = slugs
                compileStep.output.pages = slugs.length
              }
              compileStep.details = `编译完成：${slugs.length} 个页面`
            }
          }
        }
      } else if (evt.type === 'page_start') {
        const data = evt.data
        const runningIdx = compileSteps.value.findIndex(s => s.status === 'running')
        if (runningIdx < 0) return
        const runningStep = compileSteps.value[runningIdx]
        const stepName = runningStep.name

        // 更新子进度
        runningStep.subProgress = {
          current: data.index ?? 0,
          total: data.total ?? 0,
          currentEntity: data.entity ?? data.section ?? '',
        }

        // 步骤详情文本
        if (stepName === 'extract') {
          const section = data.section ? `章节「${data.section}」` : ''
          const entity = data.entity ? `实体 ${data.entity}` : '抽取中'
          runningStep.details = `正在${section}抽取：${entity} (${(data.index ?? 0) + 1}/${data.total ?? '?'})`
        } else if (stepName === 'compile') {
          runningStep.details = `正在编译：${data.entity ?? '...'} (${(data.index ?? 0) + 1}/${data.total ?? '?'})`
        }

        // ── 按步骤路由实体进度到对应列表 ──
        const targetList: EntityProgressItem[] | null =
          stepName === 'extract' ? extractEntities.value :
          stepName === 'compile' ? compileEntities.value : null

        if (targetList && (data.entity || data.section)) {
          const itemName = data.entity || data.section
          const existing = targetList.find(e => e.name === itemName)
          if (existing) {
            existing.status = 'running'
            existing.started_at = Date.now()
            existing.extra = { confidence: data.confidence, entity_type: data.entity_type, section: data.section }
          } else {
            targetList.push({
              name: itemName,
              status: 'running',
              started_at: Date.now(),
              extra: { confidence: data.confidence, entity_type: data.entity_type, section: data.section },
            })
          }
        }
      } else if (evt.type === 'page_done') {
        const data = evt.data
        const runningIdx = compileSteps.value.findIndex(s => s.status === 'running')
        if (runningIdx < 0) return
        const runningStep = compileSteps.value[runningIdx]
        const stepName = runningStep.name

        const sp = runningStep.subProgress
        if (sp) {
          sp.current = data.index ?? sp.current
          if (data.status === 'error') {
            sp.currentEntity = `❌ ${data.entity || data.section} 失败：${data.error?.substring(0, 100) || '未知错误'}`
          } else if (data.status === 'skipped') {
            sp.currentEntity = `⏭ ${data.entity || data.section} 跳过`
          } else if (data.llm_error) {
            sp.currentEntity = `⚠️ ${data.entity || data.section} 完成（LLM 警告：${data.llm_error.substring(0, 80)}）`
          } else {
            sp.currentEntity = `✅ ${data.entity || data.section} 完成`
          }
        }

        // 收集错误
        if (data.status === 'error' || data.llm_error) {
          if (!runningStep.errors) runningStep.errors = []
          runningStep.errors.push({
            entity: data.entity || data.section,
            error: data.error || data.llm_error,
            status: data.status || 'llm_warning',
          })
        }

        // ── 按步骤路由更新 ──
        const targetList: EntityProgressItem[] | null =
          stepName === 'extract' ? extractEntities.value :
          stepName === 'compile' ? compileEntities.value : null

        const itemName = data.entity || data.section
        if (targetList && itemName) {
          const item = targetList.find(e => e.name === itemName)
          if (item) {
            item.status = data.status === 'error' ? 'error'
              : data.status === 'skipped' ? 'skipped'
              : 'done'
            item.done_at = Date.now()
            if (data.error || data.llm_error) {
              item.error = data.error || data.llm_error
            }
          }
        }

        // 步骤详情更新
        if (stepName === 'compile' && targetList) {
          const done = targetList.filter(e => e.status === 'done' || e.status === 'error' || e.status === 'skipped').length
          const total = targetList.length
          runningStep.details = `正在编译：${done}/${total} 个实体完成`
        } else if (stepName === 'extract' && targetList) {
          const done = targetList.filter(e => e.status === 'done' || e.status === 'error' || e.status === 'skipped').length
          const total = targetList.length
          runningStep.details = `抽取进度：${done}/${total} 个实体完成`
        }
      } else if (evt.type === 'progress') {
        const data = evt.data
        const percent = data.percent as number | undefined
        const current = data.current as number | undefined
        const total = data.total as number | undefined
        // 更新当前运行步骤的 subProgress
        const runningIdx = compileSteps.value.findIndex(s => s.status === 'running')
        if (runningIdx >= 0 && typeof current === 'number') {
          compileSteps.value[runningIdx].subProgress = {
            current: current,
            total: total ?? 0,
            currentEntity: data.message as string ?? '',
          }
        }
        if (typeof percent === 'number' && percent > 0 && runningIdx >= 0) {
          // 进度公式：步骤起始百分比 + 步骤内进度
          compileProgress.value = ((runningIdx * 100 + percent) / 6)
        }
      } else if (evt.type === 'section_start') {
        // 添加新章节节点
        const data = evt.data
        sectionNodes.value.push({
          slug: data.slug as string,
          title: data.title as string,
          level: data.level as number,
          status: 'running',
          children_count: data.children_count as number ?? 0,
        })
        compileSteps.value[3].status = 'running'
        compileSteps.value[3].subProgress = {
          current: data.index as number ?? 0,
          total: data.total as number ?? 0,
          currentEntity: `处理章节: ${data.title}`,
        }
        compileProgress.value = ((3 * 100) + (data.index as number ?? 0) / Math.max(data.total as number ?? 1, 1) * 100) / 6
      } else if (evt.type === 'section_done') {
        // 更新章节节点状态
        const data = evt.data
        const node = sectionNodes.value.find((n) => n.slug === data.slug)
        if (node) {
          node.status = data.outcome === 'error' ? 'error' : 'done'
          node.outcome = data.outcome as string
          node.raw_chars = data.raw_chars as number
          node.compiled_chars = data.compiled_chars as number
          node.llm_success = data.llm_success as boolean
          node.processing_time_ms = data.processing_time_ms as number
          node.error = data.error as string | undefined
        }
        if (compileSteps.value[3].subProgress) {
          compileSteps.value[3].subProgress.current = data.index as number ?? 0
          compileSteps.value[3].subProgress.currentEntity = `完成章节: ${data.title}`
        }
        // struct_compile 进度：第4步，占 3/6 = 50% 到 4/6 = 66.7%
        compileProgress.value = ((3 * 100) + (data.index as number ?? 0) / Math.max(data.total as number ?? 1, 1) * 100) / 6
      } else if (evt.type === 'section_progress') {
        // 兼容旧版 section_progress 事件
        const data = evt.data
        compileSteps.value[3].subProgress = {
          current: data.current as number ?? 0,
          total: data.total as number ?? 0,
          currentEntity: data.status === 'processing' ? `处理章节: ${data.title}` : `完成章节: ${data.title}`,
        }
        compileProgress.value = ((3 * 100) + (data.percent as number ?? 0)) / 6
      } else if (evt.type === 'done') {
        compileProgress.value = 100
        compiling.value = false
        isPaused.value = false
        compileSteps.value[2].subProgress = null
        compileSteps.value[3].subProgress = null
        const indexIdx = stepIndex['index']
        if (indexIdx !== undefined) {
          compileSteps.value[indexIdx].status = 'done'
          compileSteps.value[indexIdx].details = '索引重建完成'
        }
        phase.value = 'done'
        compileResult.value = evt.data
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
        // 从 done 事件获取管道追踪数据
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
          // 将 trace 数据合并到 sectionNodes
          if (pt.sections) {
            for (const s of pt.sections) {
              const node = sectionNodes.value.find((n) => n.slug === s.slug)
              if (node) {
                node.raw_content = s.raw_content
                node.compiled_content = s.compiled_content
                node.raw_chars = s.raw_chars
                node.compiled_chars = s.compiled_chars
              }
            }
          }
        } else {
          loadTraceData(docId)
        }
      } else if (evt.type === 'error') {
        // 如果编译已成功完成，忽略后续 error 事件（防止页面跳转）
        if (phase.value === 'done') return
        // 编译过程中出错，保持当前页面状态，不跳回 input
        compiling.value = false
        message.error('编译失败：' + (evt.data.message || '未知错误'))
        const step = evt.data.step as string
        const idx = stepIndex[step]
        if (idx !== undefined) {
          compileSteps.value[idx].status = 'error'
          compileSteps.value[idx].error = evt.data.message
        }
        // 关键：不将 phase 重置为 'input'，保持当前页面展示已完成的步骤
      }
    },
    onError: (err: string) => {
      // 编译完成后不重置页面
      if (phase.value === 'done') {
        return
      }
      // 编译过程中连接丢失，保持当前状态，不跳回 input
      // 用户可以看到当前已完成的步骤和进度
      if (compiling.value) {
        // 保持编译状态，仅标记连接断开
        message.warning('编译连接断开，已完成的步骤结果保留')
      } else {
        phase.value = 'input'
        message.error('编译连接失败：' + err)
      }
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

function viewWikiPage(slug: string) {
  router.push({ name: 'wiki', query: { slug } })
}

function resetAll() {
  phase.value = 'input'
  selectedDocId.value = ''
  compileResult.value = null
  traceData.value = null
  sectionNodes.value = []
  extractEntities.value = []
  compileEntities.value = []
  compileStepStartTime.value = 0
  resetSteps()
}

// ========== 章节操作：重新生成 + 编辑保存 ==========

const editingSlug = ref<string | null>(null)
const editingContent = ref('')

const recompileDialogVisible = ref(false)
const recompileTarget = ref<{ slug: string; title: string } | null>(null)
const recompileTemperature = ref(0.2)
const recompileSystemPrompt = ref('')
const recompileUserPrompt = ref('')
const recompilingSlug = ref<string | null>(null)

function startEdit(slug: string, content: string) {
  editingSlug.value = slug
  editingContent.value = content
}

function cancelEdit() {
  editingSlug.value = null
  editingContent.value = ''
}

async function saveEdit(slug: string, title: string) {
  if (!editingSlug.value) return
  try {
    const fm = `---
slug: ${slug}
title: ${title}
type: concept
tags: []
review_status: auto
edited_by_human: true
---
`
    const fullContent = fm + editingContent.value
    await updateWikiPage(slug, {
      content: fullContent,
      title: title,
      change_summary: '用户手工编辑',
    })
    message.success('保存成功')
    // 更新本地数据
    const node = sectionNodes.value.find((n) => n.slug === slug)
    if (node) {
      node.compiled_content = editingContent.value
      node.compiled_chars = editingContent.value.length
    }
    const section = traceData.value?.sections?.find((s) => s.slug === slug)
    if (section) {
      section.compiled_content = editingContent.value
      section.compiled_chars = editingContent.value.length
    }
    cancelEdit()
  } catch (e: any) {
    message.error('保存失败：' + (e?.response?.data?.detail || e?.message || '未知错误'))
  }
}

function openRecompileDialog(slug: string, title: string) {
  recompileTarget.value = { slug, title }
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
    const node = sectionNodes.value.find((n) => n.slug === slug)
    if (node) {
      node.compiled_content = result.compiled_content
      node.compiled_chars = result.compiled_chars
    }
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

// 章节节点统计
const sectionStats = computed(() => {
  const done = sectionNodes.value.filter((n) => n.status === 'done').length
  const error = sectionNodes.value.filter((n) => n.status === 'error').length
  const running = sectionNodes.value.filter((n) => n.status === 'running').length
  return { done, error, running, total: sectionNodes.value.length }
})

// ========== 初始化 ==========
onMounted(() => {
  const qDocId = route.query.doc_id as string
  if (qDocId) {
    selectedDocId.value = qDocId
    sourceTab.value = 'existing'
    startCompile()
  }
  loadExistingDocs()
})

watch(sourceTab, (val) => {
  if (val === 'existing' && existingDocs.value.length === 0) {
    loadExistingDocs()
  }
})
</script>

<template>
  <PageHeader
    title="LLM 编译流水线"
    description="上传文档 → 实时编译 → 查看章节级处理对比与统计，每个章节独立展示、可编辑、可重处理"
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
              <div class="meta-text" style="font-size: 13px">
                支持 md、docx、xlsx、pdf、html、txt、sql 等格式
              </div>
              <div class="meta-text" style="font-size: 13px">
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

      <!-- 控制按钮 -->
      <div v-if="phase === 'compiling'" style="margin-bottom: 16px; text-align: right">
        <NSpace justify="end">
          <NButton
            v-if="!isPaused"
            type="warning"
            size="small"
            @click="doPause"
            :disabled="!pipelineRunId"
          >
            暂停编译
          </NButton>
          <NButton
            v-if="isPaused"
            type="success"
            size="small"
            @click="doResume"
            :disabled="!pipelineRunId"
          >
            继续编译
          </NButton>
          <NButton type="error" size="small" @click="cancelCompile" :disabled="!compiling">
            停止编译
          </NButton>
        </NSpace>
        <div v-if="isPaused" style="margin-top: 8px">
          <NAlert type="warning" size="small">
            编译已暂停，点击「继续编译」恢复处理
          </NAlert>
        </div>
      </div>

      <!-- 高层级 5 步骤 -->
      <NSteps
        :current="compileSteps.filter(s => s.status === 'done').length"
        :status="compileSteps.some(s => s.status === 'error') ? 'error' : 'process'"
        vertical
        style="margin-bottom: 20px"
      ><NStep
          v-for="step in compileSteps"
          :key="step.name"
          :title="step.label"
          :status="
            step.status === 'error' ? 'error' :
            step.status === 'running' ? 'process' :
            step.status === 'done' || step.status === 'skipped' ? 'finish' : 'wait'
          "
        >
          <template #default>
            <div style="display: flex; flex-direction: column; gap: 4px">
              <template v-if="step.status === 'done' && step.details">
                <span class="success-text">{{ step.details }}</span>
              </template>
              <template v-if="step.status === 'running' && step.details">
                <span class="primary-text">{{ step.details }}</span>
              </template>
              <template v-if="step.status === 'skipped' && step.details">
                <span class="meta-text">{{ step.details }}</span>
              </template>
              <template v-if="step.status === 'error' && step.error">
                <span class="danger-text">{{ step.error }}</span>
              </template>

              <!-- 步骤级错误详情（LLM 调用错误、实体处理失败等） -->
              <template v-if="step.errors && step.errors.length > 0">
                <div class="error-box" style="margin-top: 4px; padding: 4px 8px">
                  <div class="danger-text" style="font-size: 12px; margin-bottom: 2px">
                    ⚠️ {{ step.errors.length }} 个错误：
                  </div>
                  <div v-for="(err, i) in step.errors.slice(0, 5)" :key="i" class="secondary-text" style="font-size: 11px; line-height: 18px">
                    {{ err.entity }}: {{ err.error?.substring(0, 120) || '未知错误' }}
                  </div>
                  <div v-if="step.errors.length > 5" class="meta-text" style="font-size: 11px">
                    ...还有 {{ step.errors.length - 5 }} 个错误
                  </div>
                </div>
              </template>
              <template
                v-if="step.status === 'running' && step.subProgress && step.subProgress.total > 0"
              >
                <span class="primary-text">
                  {{ step.name === 'struct_compile' ? '章节' : '编译' }}中 {{ step.subProgress.current }}/{{ step.subProgress.total }}
                  <span v-if="step.subProgress.currentEntity">
                    — {{ step.subProgress.currentEntity }}
                  </span>
                </span>
              </template>

              <!-- ── 实体/章节进度卡片（知识抽取 + LLM 编译 Wiki 通用） ── -->
              <template v-if="(step.name === 'extract' && extractEntities.length > 0) || (step.name === 'compile' && compileEntities.length > 0)">
                <div class="compile-entities-panel">
                  <!-- 进度条 -->
                  <div class="compile-entities-progress">
                    <NProgress
                      :percentage="Math.round(
                        (getStepEntityList(step).filter(e => e.status === 'done' || e.status === 'error' || e.status === 'skipped').length / getStepEntityList(step).length) * 100
                      )"
                      :height="6"
                      :border-radius="3"
                      :color="getStepEntityList(step).some(e => e.status === 'error') ? '#f0a020' : '#18a058'"
                    />
                    <span class="meta-text" style="font-size: 11px; margin-top: 2px">
                      {{ getStepEntityList(step).filter(e => e.status === 'done' || e.status === 'error' || e.status === 'skipped').length }}/{{ getStepEntityList(step).length }} {{ step.name === 'extract' ? '个实体' : '个页面' }}
                    </span>
                  </div>
                  <!-- 实体/章节列表 -->
                  <div class="compile-entities-list">
                    <div
                      v-for="item in getStepEntityList(step)"
                      :key="item.name"
                      class="compile-entity-item"
                      :class="{
                        'entity-running': item.status === 'running',
                        'entity-done': item.status === 'done',
                        'entity-error': item.status === 'error',
                        'entity-skipped': item.status === 'skipped',
                      }"
                    >
                      <!-- 状态图标 -->
                      <span class="entity-status-icon">
                        <template v-if="item.status === 'running'">
                          <span class="entity-spinner" />
                        </template>
                        <template v-else-if="item.status === 'done'">
                          <span class="entity-check">&#10003;</span>
                        </template>
                        <template v-else-if="item.status === 'error'">
                          <span class="entity-cross">&#10007;</span>
                        </template>
                        <template v-else-if="item.status === 'skipped'">
                          <span class="entity-skip">&#8645;</span>
                        </template>
                        <template v-else>
                          <span class="entity-pending">&#9679;</span>
                        </template>
                      </span>
                      <!-- 名称 -->
                      <span class="entity-name" :title="item.name">{{ item.name }}</span>
                      <!-- 章节标签（抽取环节） -->
                      <span v-if="item.extra?.section && step.name === 'extract'" class="entity-section-tag">
                        {{ item.extra.section }}
                      </span>
                      <!-- 附加信息（抽取环节：置信度 + 类型） -->
                      <span v-if="item.extra?.confidence" class="entity-extra meta-text" :title="`置信度: ${item.extra.confidence.toFixed(2)}`">
                        {{ item.extra.confidence.toFixed(2) }}
                      </span>
                      <span v-if="item.extra?.entity_type" class="entity-type-tag">
                        {{ item.extra.entity_type }}
                      </span>
                      <!-- 耗时 -->
                      <span v-if="item.status === 'done' && item.started_at && item.done_at" class="entity-time meta-text">
                        {{ ((item.done_at - item.started_at) / 1000).toFixed(1) }}s
                      </span>
                      <span v-else-if="item.status === 'running' && item.started_at" class="entity-time meta-text">
                        {{ ((Date.now() - item.started_at) / 1000).toFixed(0) }}s...
                      </span>
                      <!-- 错误信息 -->
                      <span v-if="item.error" class="entity-error-text danger-text" :title="item.error">
                        {{ item.error.substring(0, 60) }}{{ item.error.length > 60 ? '...' : '' }}
                      </span>
                    </div>
                  </div>
                </div>
              </template>

              <!-- 环节产出详情 -->
              <template v-if="(step.output && (step.status === 'done' || step.status === 'skipped')) || (step.status === 'error' && step.output)">
                <NCollapse style="margin-top: 4px">
                  <NCollapseItem :title="'查看详情'" name="detail">
                    <div v-if="step.name === 'parse' && step.output.heading_tree_titles" style="max-height: 200px; overflow-y: auto">
                      <div class="secondary-text" style="font-size: 12px; margin-bottom: 4px">文档章节结构：</div>
                      <div v-for="(h, i) in step.output.heading_tree_titles" :key="i" :style="{ paddingLeft: (h.level - 1) * 16 + 'px', fontSize: '12px', lineHeight: '20px' }">
                        <NTag size="tiny" :bordered="false" :type="getLevelType(h.level)" style="margin-right: 4px">H{{ h.level }}</NTag>
                        {{ h.title || '(无标题)' }}
                      </div>
                      <NEmpty v-if="!step.output.heading_tree_titles.length" description="无章节结构" size="small" />
                    </div>
                    <div v-else-if="step.name === 'extract' && step.output.entity_names" style="max-height: 200px; overflow-y: auto">
                      <div class="secondary-text" style="font-size: 12px; margin-bottom: 4px">抽取实体列表：</div>
                      <NSpace :size="4" wrap>
                        <NTag v-for="name in step.output.entity_names" :key="name" size="tiny" :bordered="false" type="info">
                          {{ name }}
                        </NTag>
                      </NSpace>
                      <NEmpty v-if="!step.output.entity_names.length" description="无实体" size="small" />
                    </div>
                    <div v-else-if="step.name === 'compile' && step.output.slugs" style="max-height: 200px; overflow-y: auto">
                      <div class="secondary-text" style="font-size: 12px; margin-bottom: 4px">生成 Wiki 页面：</div>
                      <NSpace :size="4" wrap>
                        <NTag v-for="slug in step.output.slugs" :key="slug" size="tiny" :bordered="false" type="success" style="cursor: pointer" @click="viewWikiPage(slug)">
                          {{ slug }}
                        </NTag>
                      </NSpace>
                      <NEmpty v-if="!step.output.slugs.length" description="无页面" size="small" />
                      <!-- LLM 错误详情 -->
                      <template v-if="step.output.llm_errors && step.output.llm_errors.length > 0">
                        <div class="error-bg" style="margin-top: 8px; padding: 6px">
                          <div class="danger-text" style="font-size: 12px; margin-bottom: 4px">
                            ⚠️ LLM 编译错误（{{ step.output.llm_error_count }} 个）：
                          </div>
                          <div v-for="(err, i) in step.output.llm_errors" :key="i" class="secondary-text" style="font-size: 11px; line-height: 18px">
                            <b>{{ err.entity }}</b>: {{ err.error?.substring(0, 150) }}
                          </div>
                        </div>
                      </template>
                    </div>
                    <div v-else-if="step.name === 'struct_compile'">
                      <div class="secondary-text" style="font-size: 12px">
                        处理章节数：{{ step.output.sections ?? 0 }}，创建：{{ step.output.pages_created ?? 0 }}，更新：{{ step.output.pages_updated ?? 0 }}
                      </div>
                    </div>
                    <div v-else-if="step.name === 'index'">
                      <div class="secondary-text" style="font-size: 12px">
                        索引重建：{{ step.output.index_rebuilt ? '已完成' : '已跳过' }}，页面数：{{ step.output.slugs_count ?? 0 }}
                      </div>
                    </div>
                  </NCollapseItem>
                </NCollapse>
              </template>

              <!-- P3: 从该阶段重跑按钮 -->
              <template v-if="step.status === 'done' || step.status === 'error'">
                <NButton
                  size="tiny"
                  quaternary
                  type="warning"
                  style="margin-top: 4px"
                  @click="restartFromStage(step.name)"
                >
                  从「{{ step.label }}」重跑
                </NButton>
              </template>
            </div>
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
          <span class="secondary-text" style="font-size: 13px; margin-right: 8px">生成页面：</span>
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

    <!-- ==================== 章节节点（独立管道节点） ==================== -->
    <NCard
      v-if="sectionNodes.length > 0"
      title="章节处理节点"
      size="small"
      style="margin-bottom: 16px"
    >
      <template #header-extra>
        <NSpace :size="12">
          <NTag size="small" type="info" :bordered="false">
            总计 {{ sectionStats.total }}
          </NTag>
          <NTag size="small" type="success" :bordered="false">
            完成 {{ sectionStats.done }}
          </NTag>
          <NTag v-if="sectionStats.error > 0" size="small" type="error" :bordered="false">
            失败 {{ sectionStats.error }}
          </NTag>
          <NTag v-if="sectionStats.running > 0" size="small" type="warning" :bordered="false">
            处理中 {{ sectionStats.running }}
          </NTag>
        </NSpace>
      </template>

      <div
        v-for="node in sectionNodes"
        :key="node.slug"
        class="border-light" style="display: flex; align-items: center; padding: 8px 12px; gap: 8px"
      >
        <!-- 状态图标 -->
        <NSpin v-if="node.status === 'running'" :size="16" />
        <span v-else-if="node.status === 'done'" class="success-text" style="font-size: 16px">✅</span>
        <span v-else-if="node.status === 'error'" class="danger-text" style="font-size: 16px">❌</span>
        <span v-else class="muted-text" style="font-size: 16px">⏳</span>

        <!-- 层级标签 -->
        <NTag
          :bordered="false"
          size="tiny"
          :type="getLevelType(node.level)"
          style="font-weight: 600; min-width: 32px; text-align: center"
        >
          {{ getLevelLabel(node.level) }}
        </NTag>

        <!-- 标题 -->
        <span style="font-weight: 500; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
          {{ node.title }}
        </span>

        <!-- slug -->
        <NTag size="tiny" :bordered="false" style="max-width: 160px; overflow: hidden; text-overflow: ellipsis">
          {{ node.slug }}
        </NTag>

        <!-- 处理时间 -->
        <span v-if="node.processing_time_ms" class="meta-text" style="font-size: 12px; white-space: nowrap">
          {{ formatMs(node.processing_time_ms) }}
        </span>

        <!-- 字符变化 -->
        <span v-if="node.raw_chars !== undefined && node.compiled_chars !== undefined" class="meta-text" style="font-size: 12px; white-space: nowrap">
          {{ node.raw_chars }} → {{ node.compiled_chars }}
        </span>

        <!-- LLM 状态 -->
        <NTag v-if="node.status === 'done'" size="tiny" :bordered="false" :type="node.llm_success ? 'success' : 'error'">
          {{ node.llm_success ? 'LLM 成功' : 'LLM 失败' }}
        </NTag>

        <!-- 操作按钮 -->
        <template v-if="node.status === 'done' || node.status === 'error'">
          <NTooltip>
            <template #trigger>
              <NButton
                size="tiny"
                quaternary
                @click="viewWikiPage(node.slug)"
              >
                查看
              </NButton>
            </template>
            查看 Wiki 页面
          </NTooltip>
          <NTooltip>
            <template #trigger>
              <NButton
                size="tiny"
                quaternary
                :loading="recompilingSlug === node.slug"
                @click="openRecompileDialog(node.slug, node.title)"
              >
                重处理
              </NButton>
            </template>
            重新生成此章节
          </NTooltip>
          <NTooltip>
            <template #trigger>
              <NButton
                size="tiny"
                quaternary
                :type="editingSlug === node.slug ? 'warning' : 'default'"
                @click="editingSlug === node.slug ? cancelEdit() : startEdit(node.slug, node.compiled_content || '')"
              >
                {{ editingSlug === node.slug ? '取消' : '编辑' }}
              </NButton>
            </template>
            编辑此章节内容
          </NTooltip>
        </template>
      </div>

      <NEmpty v-if="sectionNodes.length === 0" description="暂无章节节点" size="small" />
    </NCard>

    <!-- 章节编辑内联区域 -->
    <NCard
      v-if="editingSlug"
      title="编辑章节"
      size="small"
      style="margin-bottom: 16px"
    >
      <NInput
        v-model:value="editingContent"
        type="textarea"
        :autosize="{ minRows: 8, maxRows: 20 }"
        style="margin-bottom: 8px"
      />
      <NSpace>
        <NButton
          size="small"
          type="primary"
          @click="saveEdit(editingSlug, sectionNodes.find(n => n.slug === editingSlug)?.title || '')"
        >
          保存
        </NButton>
        <NButton size="small" @click="cancelEdit()">取消</NButton>
      </NSpace>
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
              <span class="meta-text" style="font-size: 12px">
                {{ formatMs(section.processing_time_ms) }}
              </span>
              <span class="meta-text" style="font-size: 12px">
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
              <NButton
                size="tiny"
                quaternary
                :loading="recompilingSlug === section.slug"
                @click.stop="openRecompileDialog(section.slug, section.title)"
              >
                重新生成
              </NButton>
              <NButton
                size="tiny"
                quaternary
                :type="editingSlug === section.slug ? 'warning' : 'default'"
                @click.stop="editingSlug === section.slug ? cancelEdit() : startEdit(section.slug, section.compiled_content)"
              >
                {{ editingSlug === section.slug ? '取消编辑' : '编辑' }}
              </NButton>
            </NSpace>
          </template>

          <NDivider style="margin: 0 0 8px" />

          <NGrid :cols="2" :x-gap="12" responsive="screen">
            <NGi>
              <div class="danger-text" style="margin-bottom: 4px; font-weight: 600">
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
              <div class="success-text" style="margin-bottom: 4px; font-weight: 600">
                处理后（LLM 编译）
              </div>
              <NCode
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
      <p class="secondary-text" style="margin-bottom: 16px">
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
          <div class="meta-text" style="font-size: 11px; margin-top: 4px">
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

<style scoped>
.meta-text { color: var(--opskg-text-3); }
.secondary-text { color: var(--opskg-text-2); }
.danger-text { color: var(--opskg-color-danger); }
.success-text { color: var(--opskg-color-success); }
.primary-text { color: var(--opskg-color-primary); }
.muted-text { color: var(--opskg-text-3); }
.border-light { border-bottom: 1px solid var(--opskg-border-color); }
.error-box { background: color-mix(in srgb, var(--opskg-color-danger) 8%, transparent); border: 1px solid color-mix(in srgb, var(--opskg-color-danger) 20%, transparent); border-radius: 4px; }
.error-bg { background: color-mix(in srgb, var(--opskg-color-danger) 8%, transparent); border-radius: 4px; }

/* ── 实体编译实时进度卡片 ── */
.compile-entities-panel {
  margin-top: 8px;
  padding: 10px;
  background: var(--opskg-body-color);
  border: 1px solid var(--opskg-border-color);
  border-radius: 6px;
}
.compile-entities-progress {
  margin-bottom: 8px;
}
.compile-entities-list {
  max-height: 240px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.compile-entity-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 4px;
  font-size: 12px;
  transition: background 0.2s;
}
.compile-entity-item.entity-running {
  background: color-mix(in srgb, var(--opskg-color-primary) 6%, transparent);
  font-weight: 500;
}
.compile-entity-item.entity-done {
  color: var(--opskg-text-2);
}
.compile-entity-item.entity-error {
  background: color-mix(in srgb, var(--opskg-color-danger) 6%, transparent);
}
.compile-entity-item.entity-skipped {
  color: var(--opskg-text-3);
  text-decoration: line-through;
}

.entity-status-icon {
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.entity-check { color: var(--opskg-color-success); font-size: 14px; }
.entity-cross { color: var(--opskg-color-danger); font-size: 14px; }
.entity-skip { color: var(--opskg-text-3); font-size: 14px; }
.entity-pending { color: var(--opskg-text-4); font-size: 8px; }

.entity-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.entity-extra {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  padding: 1px 4px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--opskg-color-primary) 8%, transparent);
}
.entity-type-tag {
  flex-shrink: 0;
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--opskg-color-primary) 12%, transparent);
  color: var(--opskg-color-primary);
  font-weight: 500;
}
.entity-section-tag {
  flex-shrink: 0;
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 3px;
  background: color-mix(in srgb, var(--opskg-color-warning, #f0a020) 12%, transparent);
  color: var(--opskg-color-warning, #f0a020);
  font-weight: 400;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.entity-time {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  min-width: 36px;
  text-align: right;
}
.entity-error-text {
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
  flex-shrink: 1;
}

/* 旋转动画 */
.entity-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--opskg-border-color);
  border-top-color: var(--opskg-color-primary);
  border-radius: 50%;
  animation: entity-spin 0.8s linear infinite;
}
@keyframes entity-spin {
  to { transform: rotate(360deg); }
}
</style>