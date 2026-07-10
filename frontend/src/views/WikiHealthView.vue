<script setup lang="ts">
import { ref, computed, onMounted, h, watch } from 'vue'
import {
  NTabs,
  NTabPane,
  NCard,
  NButton,
  NDataTable,
  NTag,
  NSpace,
  NSpin,
  NEmpty,
  NStatistic,
  NGrid,
  NGi,
  NAlert,
  useMessage,
} from 'naive-ui'
import {
  runWikiLint,
  getWikiOrphans,
  getWikiStale,
  recompileStale,
  rebuildIndex,
  ignoreLintIssue,
  type LintIssue,
  type LintReport,
} from '@/api/wiki'
import type { WikiPage } from '@/types/api'

const message = useMessage()

const activeTab = ref<'lint' | 'drift' | 'orphan'>('lint')

// ============ Tab 1: Lint 报告 ============
const lintLoading = ref(false)
const lintReport = ref<LintReport | null>(null)
/** P1-12b: 正在忽略中的 issue_key 集合（按钮 loading 态） */
const ignoringKeys = ref<Set<string>>(new Set())

const typeTagMap: Record<
  string,
  { type: 'error' | 'warning' | 'info' | 'default' | 'success' | 'primary'; label: string }
> = {
  contradiction: { type: 'error', label: '矛盾' },
  stale: { type: 'warning', label: '过时' },
  orphan: { type: 'default', label: '孤岛' },
  missing_concept: { type: 'info', label: '缺失概念' },
  missing_type_section: { type: 'warning', label: '缺失章节' },
  empty_section: { type: 'default', label: '空章节' },
}

// 对齐后端 SEV_ERROR/SEV_WARN/SEV_INFO（error/warn/info）
const severityTagMap: Record<
  string,
  { type: 'error' | 'warning' | 'info' | 'default'; label: string }
> = {
  error: { type: 'error', label: '严重' },
  warn: { type: 'warning', label: '警告' },
  info: { type: 'info', label: '提示' },
}

const lintStatCards = computed(() => [
  { label: '检查页面数', value: lintReport.value?.pages_checked ?? 0, color: '#2080f0' },
  { label: '总问题数', value: lintReport.value?.total_issues ?? 0, color: '#d03050' },
  { label: '已忽略', value: lintReport.value?.ignored_count ?? 0, color: '#909399' },
  { label: '严重', value: lintReport.value?.by_severity?.error ?? 0, color: '#d03050' },
])

const lintColumns = [
  {
    title: '类型',
    key: 'type',
    width: 130,
    render(row: LintIssue) {
      const cfg = typeTagMap[row.type] || { type: 'default' as const, label: row.type }
      return h(NTag, { type: cfg.type, size: 'small' }, { default: () => cfg.label })
    },
  },
  {
    title: '严重度',
    key: 'severity',
    width: 100,
    render(row: LintIssue) {
      const cfg = severityTagMap[row.severity] || { type: 'default' as const, label: row.severity }
      return h(NTag, { type: cfg.type, size: 'small' }, { default: () => cfg.label })
    },
  },
  {
    title: 'Slug',
    key: 'slug',
    width: 220,
    ellipsis: { tooltip: true },
  },
  {
    title: '消息',
    key: 'message',
    ellipsis: { tooltip: true },
  },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    fixed: 'right',
    render(row: LintIssue) {
      const ignoring = ignoringKeys.value.has(row.issue_key)
      return h(
        NButton,
        {
          size: 'small',
          quaternary: true,
          type: 'default',
          loading: ignoring,
          disabled: ignoring,
          onClick: () => handleIgnoreIssue(row),
        },
        { default: () => '忽略' },
      )
    },
  },
]

async function handleRunLint() {
  lintLoading.value = true
  try {
    const report = await runWikiLint(true)
    lintReport.value = report
    if (report.total_issues === 0) {
      message.success('检查完成，未发现问题')
    } else {
      message.success(`检查完成，共发现 ${report.total_issues} 个问题`)
    }
  } catch (e) {
    message.error('运行检查失败')
  } finally {
    lintLoading.value = false
  }
}

/** P1-12b: 忽略一个 lint issue（本地移除并调整计数，后端持久化） */
async function handleIgnoreIssue(issue: LintIssue) {
  if (!issue.issue_key || ignoringKeys.value.has(issue.issue_key)) return
  const next = new Set(ignoringKeys.value)
  next.add(issue.issue_key)
  ignoringKeys.value = next
  try {
    await ignoreLintIssue(issue.issue_key, {
      type: issue.type,
      slug: issue.slug,
      message: issue.message,
    })
    message.success('已忽略该问题')
    if (lintReport.value) {
      const issues = lintReport.value.issues.filter(
        (i) => i.issue_key !== issue.issue_key,
      )
      const by_type = { ...lintReport.value.by_type }
      by_type[issue.type] = Math.max(0, (by_type[issue.type] || 0) - 1)
      const by_severity = { ...lintReport.value.by_severity }
      by_severity[issue.severity] = Math.max(0, (by_severity[issue.severity] || 0) - 1)
      lintReport.value = {
        ...lintReport.value,
        issues,
        total_issues: issues.length,
        ignored_count: (lintReport.value.ignored_count ?? 0) + 1,
        by_type,
        by_severity,
      }
    }
  } catch (e) {
    message.error('忽略失败')
  } finally {
    const rest = new Set(ignoringKeys.value)
    rest.delete(issue.issue_key)
    ignoringKeys.value = rest
  }
}

// ============ Tab 2: 漂移监控 ============
const staleLoading = ref(false)
const recompilingAll = ref(false)
const rebuildingIndex = ref(false)
const stalePages = ref<WikiPage[]>([])
const recompilingSlugs = ref<Record<string, boolean>>({})

const staleColumns = [
  {
    title: 'Slug',
    key: 'slug',
    width: 240,
    ellipsis: { tooltip: true },
  },
  {
    title: '标题',
    key: 'title',
    ellipsis: { tooltip: true },
  },
  {
    title: '更新时间',
    key: 'updated_at',
    width: 180,
    render(row: WikiPage) {
      return formatDate(row.updated_at)
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 130,
    render(row: WikiPage) {
      const isRecompiling = !!recompilingSlugs.value[row.slug]
      const anyRecompiling =
        isRecompiling || Object.keys(recompilingSlugs.value).length > 0 || recompilingAll.value
      return h(
        NButton,
        {
          size: 'small',
          type: 'primary',
          loading: isRecompiling,
          disabled: anyRecompiling && !isRecompiling,
          onClick: () => handleRecompileOne(row.slug),
        },
        { default: () => '重编译' },
      )
    },
  },
]

async function fetchStale() {
  staleLoading.value = true
  try {
    const res = await getWikiStale()
    stalePages.value = res.pages
  } catch (e) {
    message.error('获取 stale 列表失败')
  } finally {
    staleLoading.value = false
  }
}

async function handleRecompileOne(slug: string) {
  recompilingSlugs.value = { ...recompilingSlugs.value, [slug]: true }
  try {
    await recompileStale(true)
    message.success('重编译完成')
    await fetchStale()
  } catch (e) {
    message.error('重编译失败')
  } finally {
    const next = { ...recompilingSlugs.value }
    delete next[slug]
    recompilingSlugs.value = next
  }
}

async function handleRecompileAll() {
  if (recompilingAll.value || Object.keys(recompilingSlugs.value).length > 0) return
  recompilingAll.value = true
  try {
    await recompileStale(true)
    message.success('全部重编译完成')
    await fetchStale()
  } catch (e) {
    message.error('重编译失败')
  } finally {
    recompilingAll.value = false
  }
}

async function handleRebuildIndex() {
  if (rebuildingIndex.value) return
  rebuildingIndex.value = true
  try {
    await rebuildIndex()
    message.success('索引重建完成')
  } catch (e) {
    message.error('索引重建失败')
  } finally {
    rebuildingIndex.value = false
  }
}

// ============ Tab 3: 孤岛页面 ============
const orphanLoading = ref(false)
const orphanPages = ref<WikiPage[]>([])
const orphanLoaded = ref(false)

const orphanColumns = [
  {
    title: 'Slug',
    key: 'slug',
    width: 240,
    ellipsis: { tooltip: true },
  },
  {
    title: '标题',
    key: 'title',
    ellipsis: { tooltip: true },
  },
  {
    title: '类型',
    key: 'type',
    width: 120,
    render(row: WikiPage) {
      return row.type
    },
  },
  {
    title: '更新时间',
    key: 'updated_at',
    width: 180,
    render(row: WikiPage) {
      return formatDate(row.updated_at)
    },
  },
]

async function fetchOrphans() {
  orphanLoading.value = true
  try {
    const res = await getWikiOrphans()
    orphanPages.value = res.pages
  } catch (e) {
    message.error('获取孤岛页面失败')
  } finally {
    orphanLoading.value = false
    orphanLoaded.value = true
  }
}

// ============ 通用工具 ============
function formatDate(dateStr: string) {
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

watch(activeTab, (val) => {
  if (val === 'drift' && stalePages.value.length === 0) {
    fetchStale()
  } else if (val === 'orphan' && !orphanLoaded.value) {
    fetchOrphans()
  }
})

onMounted(() => {
  // Lint 报告由用户主动点击「运行检查」触发，不自动加载
})
</script>

<template>
  <div class="wiki-health-view">
    <NTabs v-model:value="activeTab" type="line" animated>
      <!-- Tab 1: Lint 报告 -->
      <NTabPane name="lint" tab="Lint 报告">
        <div class="tab-content">
          <NCard size="small" class="action-card">
            <NSpace align="center" justify="space-between">
              <NButton type="primary" :loading="lintLoading" @click="handleRunLint">
                运行检查
              </NButton>
              <span class="hint-text">点击按钮运行 Wiki 健康检查</span>
            </NSpace>
          </NCard>

          <NSpin :show="lintLoading">
            <NGrid :cols="4" :x-gap="16" :y-gap="16" class="stats-grid">
              <NGi v-for="card in lintStatCards" :key="card.label">
                <NCard>
                  <NStatistic :label="card.label" :value="card.value">
                    <template #prefix>
                      <span :style="{ color: card.color }">●</span>
                    </template>
                  </NStatistic>
                </NCard>
              </NGi>
            </NGrid>
          </NSpin>

          <NCard class="table-card">
            <NDataTable
              :columns="lintColumns"
              :data="lintReport?.issues || []"
              :loading="lintLoading"
              :row-key="(row: LintIssue) => row.issue_key"
              :max-height="600"
              :scroll-x="700"
            >
              <template #empty>
                <NEmpty :description="lintReport ? '未发现问题' : '点击「运行检查」开始检测'" />
              </template>
            </NDataTable>
          </NCard>
        </div>
      </NTabPane>

      <!-- Tab 2: 漂移监控 -->
      <NTabPane name="drift" tab="漂移监控">
        <div class="tab-content">
          <NCard size="small" class="action-card">
            <NSpace>
              <NButton
                type="primary"
                :loading="recompilingAll"
                :disabled="Object.keys(recompilingSlugs).length > 0"
                @click="handleRecompileAll"
              >
                重编译所有 stale
              </NButton>
              <NButton :loading="rebuildingIndex" @click="handleRebuildIndex">重建索引</NButton>
            </NSpace>
          </NCard>

          <NAlert type="info" :show-icon="true" class="hint-alert">
            stale 页面指 wiki 页面 updated_at 早于其引用的 raw 文档 updated_at
            的页面，需要重编译以保持同步。
          </NAlert>

          <NCard class="table-card">
            <NDataTable
              :columns="staleColumns"
              :data="stalePages"
              :loading="staleLoading"
              :row-key="(row: WikiPage) => row.slug"
              :max-height="600"
              :scroll-x="600"
            >
              <template #empty>
                <NEmpty description="暂无 stale 页面" />
              </template>
            </NDataTable>
          </NCard>
        </div>
      </NTabPane>

      <!-- Tab 3: 孤岛页面 -->
      <NTabPane name="orphan" tab="孤岛页面">
        <div class="tab-content">
          <NAlert type="info" :show-icon="true" class="hint-alert">
            孤岛页面指无任何入链（backlink 为空）且不在 index.md
            中的页面，建议评估是否需要建立链接或归档。
          </NAlert>

          <NCard class="table-card">
            <NDataTable
              :columns="orphanColumns"
              :data="orphanPages"
              :loading="orphanLoading"
              :row-key="(row: WikiPage) => row.slug"
              :max-height="600"
              :scroll-x="600"
            >
              <template #empty>
                <NEmpty description="暂无孤岛页面" />
              </template>
            </NDataTable>
          </NCard>
        </div>
      </NTabPane>
    </NTabs>
  </div>
</template>

<style scoped>
.wiki-health-view {
  height: 100%;
  width: 100%;
}

.tab-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-top: 8px;
}

.stats-grid {
  margin-bottom: 0;
}

.table-card {
  flex: 1;
}

.hint-text {
  color: var(--n-text-color-3, #6b7280);
  font-size: 13px;
}

.hint-alert {
  margin-bottom: 0;
}
</style>
