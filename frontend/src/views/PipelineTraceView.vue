<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NInput,
  NButton,
  NCard,
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
  NModal,
  NForm,
  NFormItem,
  NSlider,
  NTooltip,
  useMessage,
} from 'naive-ui'
import { getCompileTrace, recompileSection, updateWikiPage } from '@/api/wiki'
import type { CompileTraceResponse, SectionTrace } from '@/types/api'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingState from '@/components/common/LoadingState.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const docId = ref('')
const loading = ref(false)
const traceData = ref<CompileTraceResponse | null>(null)
const showOnlyWithDiffs = ref(false)

// 章节操作
const editingSlug = ref<string | null>(null)
const editingContent = ref('')
const recompileDialogVisible = ref(false)
const recompileTarget = ref<{ slug: string; title: string } | null>(null)
const recompileTemperature = ref(0.2)
const recompileSystemPrompt = ref('')
const recompileUserPrompt = ref('')
const recompilingSlug = ref<string | null>(null)

onMounted(() => {
  const qDocId = route.query.doc_id as string
  if (qDocId) {
    docId.value = qDocId
    fetchTrace()
  }
})

async function fetchTrace() {
  const id = docId.value.trim()
  if (!id) {
    message.warning('请输入文档 ID')
    return
  }
  loading.value = true
  traceData.value = null
  try {
    traceData.value = await getCompileTrace(id, true)
  } catch (e: any) {
    message.error(e?.response?.data?.detail || e?.message || '获取管道追踪失败')
  } finally {
    loading.value = false
  }
}

function hasDiff(s: SectionTrace): boolean {
  return s.raw_content.trim() !== s.compiled_content.trim()
}

const filteredSections = computed(() => {
  if (!traceData.value?.sections) return []
  if (!showOnlyWithDiffs.value) return traceData.value.sections
  return traceData.value.sections.filter(hasDiff)
})

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

function viewPipeline(docId: string) {
  router.push({ name: 'pipeline', query: { doc_id: docId } })
}

// 章节编辑
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

// 重新生成
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
  if (!recompileTarget.value || !traceData.value?.doc_id) return
  const { slug } = recompileTarget.value
  recompilingSlug.value = slug
  try {
    const result = await recompileSection({
      doc_id: traceData.value.doc_id,
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

defineExpose({
  docId,
  traceData,
  loading,
  getLevelType,
  formatMs,
  formatChars,
  calcReduction,
  hasDiff,
  fetchTrace,
  viewWikiPage,
  viewPipeline,
  cancelEdit,
  saveEdit,
  startEdit,
  editingSlug,
  editingContent,
  openRecompileDialog,
  doRecompile,
  closeRecompileDialog,
})
</script>

<template>
  <PageHeader
    title="LLM 编译管道追踪"
    description="查看每个章节的 LLM 处理前后对比，每个章节可独立编辑、重处理"
  >
    <template #actions>
      <NButton
        v-if="traceData?.doc_id"
        size="small"
        @click="viewPipeline(traceData.doc_id)"
      >
        返回编译流水线
      </NButton>
    </template>
  </PageHeader>

  <!-- 输入区 -->
  <NCard size="small" style="margin-bottom: 16px">
    <NSpace align="center">
      <span style="white-space: nowrap; font-weight: 500">文档 ID：</span>
      <NInput
        v-model:value="docId"
        placeholder="输入文档 ID（如 abc123）"
        style="width: 320px"
        clearable
        @keyup.enter="fetchTrace"
      />
      <NButton type="primary" :loading="loading" @click="fetchTrace">
        查询管道追踪
      </NButton>
    </NSpace>
  </NCard>

  <!-- 加载中 -->
  <LoadingState v-if="loading" message="正在获取管道追踪数据..." />

  <!-- 无数据 -->
  <NEmpty
    v-else-if="!traceData"
    description="请输入文档 ID 查询管道追踪，或该文档尚未编译"
  />

  <!-- 不可用 -->
  <NAlert
    v-else-if="!traceData.available"
    type="warning"
    :title="traceData.message || '管道追踪数据不可用'"
    style="margin-bottom: 16px"
  />

  <!-- 追踪数据 -->
  <template v-else>
    <!-- 汇总统计 -->
    <NCard title="编译统计" size="small" style="margin-bottom: 16px">
      <NGrid :x-gap="12" :y-gap="8" :cols="7" responsive="screen">
        <NGi>
          <NStatistic label="拆分章节" :value="traceData.summary!.total_sections" />
        </NGi>
        <NGi>
          <NStatistic label="含子章节" :value="traceData.summary!.sections_with_children" />
        </NGi>
        <NGi>
          <NStatistic
            label="原始字符"
            :value="formatChars(traceData.summary!.total_raw_chars)"
          />
        </NGi>
        <NGi>
          <NStatistic
            label="编译后字符"
            :value="formatChars(traceData.summary!.total_compiled_chars)"
          />
        </NGi>
        <NGi>
          <NStatistic label="LLM 成功" :value="traceData.summary!.llm_success_count" />
        </NGi>
        <NGi>
          <NStatistic label="LLM 失败" :value="traceData.summary!.llm_fail_count" />
        </NGi>
        <NGi>
          <NStatistic
            label="总耗时"
            :value="formatMs(traceData.summary!.duration_ms)"
          />
        </NGi>
      </NGrid>
    </NCard>

    <!-- 章节对比列表 -->
    <NCard size="small">
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
              <NTooltip>
                <template #trigger>
                  <NButton
                    size="tiny"
                    quaternary
                    @click.stop="viewWikiPage(section.slug)"
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
                    :loading="recompilingSlug === section.slug"
                    @click.stop="openRecompileDialog(section.slug, section.title)"
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
                    :type="editingSlug === section.slug ? 'warning' : 'default'"
                    @click.stop="editingSlug === section.slug ? cancelEdit() : startEdit(section.slug, section.compiled_content)"
                  >
                    {{ editingSlug === section.slug ? '取消' : '编辑' }}
                  </NButton>
                </template>
                编辑此章节内容
              </NTooltip>
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
                  <NButton size="small" type="primary" @click="saveEdit(section.slug, section.title)">
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

      <NEmpty
        v-else
        description="所有章节处理后无差异"
      />
    </NCard>
  </template>

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