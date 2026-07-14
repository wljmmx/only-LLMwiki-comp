<script setup lang="ts">
import { ref, computed, onMounted, h } from 'vue'
import {
  NCard,
  NDataTable,
  NTag,
  NSpace,
  NButton,
  NButtonGroup,
  NEmpty,
  NDrawer,
  NDrawerContent,
  NTabs,
  NTabPane,
  NDescriptions,
  NDescriptionsItem,
  NModal,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  listIncidents,
  getIncident,
  closeIncident,
  incidentToRunbook,
  getIncidentChanges,
  getIncidentRollbackSuggestion,
  type Incident,
} from '@/api/aiops'
import { renderWikiMarkdown } from '@/utils/wikiRender'
import { formatDateTime } from '@/utils/format'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingState from '@/components/common/LoadingState.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const message = useMessage()

const loading = ref(false)
const incidents = ref<Incident[]>([])
const statusFilter = ref<'open' | 'closed' | 'all'>('open')

const detailVisible = ref(false)
const detailLoading = ref(false)
const currentIncident = ref<Incident | null>(null)
const incidentChanges = ref<any[]>([])
const rollbackSuggestion = ref<any>(null)

const runbookVisible = ref(false)
const runbookLoading = ref(false)
const runbookMd = ref('')
const runbookSlug = ref('')

const closing = ref(false)

const severityTagType: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  info: 'default',
  low: 'info',
  warning: 'warning',
  high: 'error',
  critical: 'error',
  fatal: 'error',
}

function tagSeverity(s: string) {
  return severityTagType[s] || 'default'
}

const columns = computed<DataTableColumns<Incident>>(() => [
  {
    title: 'Incident ID',
    key: 'incident_id',
    width: 180,
    render: (row) =>
      h('span', { style: 'font-family: monospace; font-size: 12px;' }, row.incident_id),
  },
  {
    title: '严重度',
    key: 'severity',
    width: 100,
    render: (row) =>
      h(NTag, { type: tagSeverity(row.severity), size: 'small' }, { default: () => row.severity }),
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) =>
      h(
        NTag,
        { type: row.status === 'open' ? 'warning' : 'default', size: 'small' },
        { default: () => row.status },
      ),
  },
  {
    title: '告警数',
    key: 'alert_count',
    width: 90,
  },
  {
    title: '根因推断',
    key: 'suspected_root_cause',
    ellipsis: { tooltip: true },
  },
  {
    title: '最近告警',
    key: 'last_seen',
    width: 180,
    render: (row) => (row.last_seen ? formatDateTime(row.last_seen) : '-'),
  },
  {
    title: '操作',
    key: 'actions',
    width: 160,
    render: (row) =>
      h(
        NButton,
        { size: 'small', quaternary: true, onClick: () => openDetail(row.incident_id) },
        { default: () => '查看详情' },
      ),
  },
])

async function loadIncidents() {
  loading.value = true
  try {
    const res = await listIncidents(statusFilter.value, 100)
    incidents.value = res.incidents || []
  } catch (err: any) {
    message.error(err?.message || '加载 incident 失败')
  } finally {
    loading.value = false
  }
}

async function openDetail(incidentId: string) {
  detailVisible.value = true
  detailLoading.value = true
  currentIncident.value = null
  incidentChanges.value = []
  rollbackSuggestion.value = null
  try {
    const [inc, ch, rb] = await Promise.all([
      getIncident(incidentId),
      getIncidentChanges(incidentId).catch(() => ({ changes: [], count: 0 })),
      getIncidentRollbackSuggestion(incidentId).catch(() => null),
    ])
    currentIncident.value = inc
    incidentChanges.value = ch?.changes || []
    rollbackSuggestion.value = rb
  } catch (err: any) {
    message.error(err?.message || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

async function handleClose() {
  if (!currentIncident.value || closing.value) return
  closing.value = true
  try {
    await closeIncident(currentIncident.value.incident_id, '通过前端关闭')
    message.success('Incident 已关闭')
    detailVisible.value = false
    await loadIncidents()
  } catch (err: any) {
    message.error(err?.message || '关闭失败')
  } finally {
    closing.value = false
  }
}

async function handleGenerateRunbook(publish: boolean) {
  if (!currentIncident.value || runbookLoading.value) return
  runbookLoading.value = true
  runbookMd.value = ''
  runbookSlug.value = ''
  runbookVisible.value = true
  try {
    const res = await incidentToRunbook(currentIncident.value.incident_id, publish)
    runbookMd.value = res.runbook_md || ''
    runbookSlug.value = res.wiki_slug || ''
    if (publish && res.wiki_slug) {
      message.success(`已发布为 Wiki: ${res.wiki_slug}`)
    }
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '生成 Runbook 失败')
  } finally {
    runbookLoading.value = false
  }
}

const renderedRunbook = computed(() => {
  if (!runbookMd.value) return ''
  return renderWikiMarkdown(runbookMd.value)
})

onMounted(() => {
  loadIncidents()
})
</script>

<template>
  <div class="incidents-view">
    <PageHeader title="Incident 管理" description="事件关联生成的 Incident 列表，支持详情查看、关闭、自动生成 Runbook" />

    <n-card :bordered="true">
      <template #header>
        <n-space align="center" :size="12">
          <span>Incident 列表</span>
          <n-button-group>
            <n-button
              v-for="s in ['open', 'closed', 'all'] as const"
              :key="s"
              :type="statusFilter === s ? 'primary' : 'default'"
              size="small"
              @click="
                statusFilter = s;
                loadIncidents();
              "
            >
              {{ s === 'open' ? '开放' : s === 'closed' ? '已关闭' : '全部' }}
            </n-button>
          </n-button-group>
          <n-button quaternary size="small" @click="loadIncidents">刷新</n-button>
        </n-space>
      </template>

      <LoadingState v-if="loading" />
      <EmptyState v-else-if="!incidents.length" description="暂无 incident" />
      <n-data-table
        v-else
        :columns="columns"
        :data="incidents"
        :bordered="false"
        :pagination="{ pageSize: 15 }"
        size="small"
      />
    </n-card>

    <!-- 详情抽屉 -->
    <n-drawer v-model:show="detailVisible" :width="720" placement="right">
      <n-drawer-content title="Incident 详情" closable>
        <LoadingState v-if="detailLoading" />
        <template v-else-if="currentIncident">
          <n-tabs default-value="basic" type="line">
            <n-tab-pane name="basic" tab="基本信息">
              <n-descriptions :column="2" bordered label-placement="left" size="small">
                <n-descriptions-item label="Incident ID">
                  <code>{{ currentIncident.incident_id }}</code>
                </n-descriptions-item>
                <n-descriptions-item label="状态">
                  <n-tag
                    :type="currentIncident.status === 'open' ? 'warning' : 'default'"
                    size="small"
                  >
                    {{ currentIncident.status }}
                  </n-tag>
                </n-descriptions-item>
                <n-descriptions-item label="严重度">
                  <n-tag :type="tagSeverity(currentIncident.severity)" size="small">
                    {{ currentIncident.severity }}
                  </n-tag>
                </n-descriptions-item>
                <n-descriptions-item label="告警数">
                  {{ currentIncident.alert_count || 0 }}
                </n-descriptions-item>
                <n-descriptions-item label="首次告警">
                  {{ formatDateTime(currentIncident.first_seen || '') || '-' }}
                </n-descriptions-item>
                <n-descriptions-item label="最近告警">
                  {{ formatDateTime(currentIncident.last_seen || '') || '-' }}
                </n-descriptions-item>
                <n-descriptions-item label="受影响主机" :span="2">
                  <n-space v-if="currentIncident.hosts?.length" :size="4">
                    <n-tag v-for="h in currentIncident.hosts" :key="h" size="small">{{ h }}</n-tag>
                  </n-space>
                  <span v-else>-</span>
                </n-descriptions-item>
                <n-descriptions-item label="受影响服务" :span="2">
                  <n-space v-if="currentIncident.services?.length" :size="4">
                    <n-tag v-for="s in currentIncident.services" :key="s" size="small" type="info">
                      {{ s }}
                    </n-tag>
                  </n-space>
                  <span v-else>-</span>
                </n-descriptions-item>
                <n-descriptions-item label="根因推断" :span="2">
                  {{ currentIncident.suspected_root_cause || '-' }}
                </n-descriptions-item>
              </n-descriptions>

              <n-space style="margin-top: 16px" :size="8">
                <n-button
                  v-if="currentIncident.status === 'open'"
                  type="error"
                  size="small"
                  :loading="closing"
                  @click="handleClose"
                >
                  关闭 Incident
                </n-button>
                <n-button size="small" @click="handleGenerateRunbook(false)">生成 Runbook</n-button>
                <n-button size="small" type="primary" @click="handleGenerateRunbook(true)">
                  生成并发布 Wiki
                </n-button>
              </n-space>
            </n-tab-pane>

            <n-tab-pane name="changes" :tab="`关联变更 (${incidentChanges.length})`">
              <n-empty v-if="!incidentChanges.length" description="无关联变更" />
              <n-space v-else vertical :size="8">
                <n-card
                  v-for="ch in incidentChanges"
                  :key="ch.change_id || ch.id"
                  size="small"
                  :bordered="true"
                >
                  <n-space align="center" :size="8">
                    <n-tag size="small" type="info">{{ ch.change_type }}</n-tag>
                    <n-tag v-if="ch.severity" size="small" :type="tagSeverity(ch.severity)">
                      {{ ch.severity }}
                    </n-tag>
                    <span style="font-size: 13px; font-weight: 600">
                      {{ ch.description || ch.ticket_id || '-' }}
                    </span>
                  </n-space>
                  <div style="font-size: 12px; color: #888; margin-top: 4px">
                    {{ ch.author || '-' }} · {{ formatDateTime(ch.timestamp || '') || '-' }}
                    <span v-if="ch.service">· {{ ch.service }}</span>
                  </div>
                </n-card>
              </n-space>
            </n-tab-pane>

            <n-tab-pane name="rollback" tab="回滚建议">
              <div v-if="!rollbackSuggestion">无回滚建议</div>
              <div v-else>
                <n-descriptions :column="1" bordered label-placement="left" size="small">
                  <n-descriptions-item label="建议">
                    {{ rollbackSuggestion.recommendation || rollbackSuggestion.suggestion || '-' }}
                  </n-descriptions-item>
                  <n-descriptions-item label="关联变更数">
                    {{ rollbackSuggestion.related_changes?.length || 0 }}
                  </n-descriptions-item>
                  <n-descriptions-item v-if="rollbackSuggestion.risk_level" label="风险等级">
                    <n-tag size="small" :type="tagSeverity(rollbackSuggestion.risk_level)">
                      {{ rollbackSuggestion.risk_level }}
                    </n-tag>
                  </n-descriptions-item>
                </n-descriptions>
                <pre
                  v-if="rollbackSuggestion.detail"
                  style="
                    margin-top: 12px;
                    font-size: 12px;
                    background: #f5f5f5;
                    padding: 8px;
                    border-radius: 4px;
                    overflow-x: auto;
                  "
                  >{{ JSON.stringify(rollbackSuggestion, null, 2) }}</pre>
              </div>
            </n-tab-pane>

            <n-tab-pane
              v-if="currentIncident.event_samples?.length"
              name="events"
              :tab="`告警样本 (${currentIncident.event_samples.length})`"
            >
              <n-space vertical :size="6">
                <n-card
                  v-for="(ev, idx) in currentIncident.event_samples"
                  :key="idx"
                  size="small"
                  :bordered="true"
                >
                  <n-space align="center" :size="6">
                    <n-tag size="small" :type="tagSeverity(ev.severity)">{{ ev.severity }}</n-tag>
                    <span style="font-size: 13px">{{ ev.message }}</span>
                  </n-space>
                  <div style="font-size: 12px; color: #888; margin-top: 4px">
                    {{ formatDateTime(ev.timestamp || '') || '-' }}
                    <span v-if="ev.host">· {{ ev.host }}</span>
                    <span v-if="ev.service">· {{ ev.service }}</span>
                  </div>
                </n-card>
              </n-space>
            </n-tab-pane>
          </n-tabs>
        </template>
      </n-drawer-content>
    </n-drawer>

    <!-- Runbook 弹窗 -->
    <n-modal
      v-model:show="runbookVisible"
      preset="card"
      title="Incident Runbook"
      style="width: 800px; max-width: 95vw"
    >
      <LoadingState v-if="runbookLoading" text="生成 Runbook 中..." />
      <template v-else>
        <n-space v-if="runbookSlug" style="margin-bottom: 12px" align="center">
          <n-tag type="success" size="small">已发布 Wiki</n-tag>
          <code>{{ runbookSlug }}</code>
        </n-space>
        <div class="markdown-rendered" v-html="renderedRunbook" />
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.incidents-view {
  max-width: 1200px;
  margin: 0 auto;
}

.markdown-rendered {
  font-size: 14px;
  line-height: 1.8;
  color: var(--n-text-color, #111827);
}

.markdown-rendered :deep(pre) {
  background: var(--n-code-color, #f5f5f5);
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
}

.markdown-rendered :deep(code) {
  background: var(--n-code-color, #f5f5f5);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: monospace;
}

.markdown-rendered :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
}

.markdown-rendered :deep(th),
.markdown-rendered :deep(td) {
  border: 1px solid var(--n-border-color, #ddd);
  padding: 6px 12px;
}

.n-button-group {
  display: inline-flex;
}
</style>
