<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
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
  useMessage,
} from 'naive-ui'
import { getCompileTrace } from '@/api/wiki'
import type { CompileTraceResponse, SectionTrace } from '@/types/api'
import PageHeader from '@/components/layout/PageHeader.vue'
import LoadingState from '@/components/layout/LoadingState.vue'

const route = useRoute()
const message = useMessage()

const docId = ref('')
const loading = ref(false)
const traceData = ref<CompileTraceResponse | null>(null)
const showOnlyWithDiffs = ref(false)

// 从 URL query 读取 doc_id
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

function getLevelColor(level: number): string {
  const colors: Record<number, string> = { 1: '#18a058', 2: '#2080f0', 3: '#f0a020' }
  return colors[level] || '#909399'
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
</script>

<template>
  <PageHeader
    title="LLM 编译管道追踪"
    description="查看每个章节的 LLM 处理前后对比，以及拆分、编译统计"
  />

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
                :color="getLevelColor(section.level)"
                style="color: #fff; font-weight: 600"
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
            </NSpace>
          </template>

          <NDivider style="margin: 0 0 8px" />

          <NGrid :cols="2" :x-gap="12" responsive="screen">
            <!-- 处理前 -->
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
            <!-- 处理后 -->
            <NGi>
              <div style="margin-bottom: 4px; font-weight: 600; color: #18a058">
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

      <NEmpty
        v-else
        description="所有章节处理后无差异"
      />
    </NCard>
  </template>
</template>