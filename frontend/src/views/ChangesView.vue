<script setup lang="ts">
import { ref, computed, onMounted, h, reactive } from 'vue'
import {
  NCard,
  NDataTable,
  NTag,
  NSpace,
  NButton,
  NSpin,
  NEmpty,
  NDrawer,
  NDrawerContent,
  NDescriptions,
  NDescriptionsItem,
  NInput,
  NModal,
  NForm,
  NFormItem,
  NInputNumber,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  listChanges,
  getChange,
  correlateChanges,
  ingestChanges,
  type Change,
} from '@/api/aiops'

const message = useMessage()

const loading = ref(false)
const changes = ref<Change[]>([])
const serviceFilter = ref('')

const detailVisible = ref(false)
const detailLoading = ref(false)
const currentChange = ref<Change | null>(null)

const correlateVisible = ref(false)
const correlateLoading = ref(false)
const correlateResult = ref<any>(null)
const correlateForm = reactive({
  sinceHours: 24,
  timeWindowMinutes: 30,
})

const ingestVisible = ref(false)
const ingestLoading = ref(false)
const ingestJson = ref(`[
  {
    "change_type": "deployment",
    "host": "web-prod-01",
    "service": "order-service",
    "severity": "normal",
    "author": "alice",
    "ticket_id": "OPS-1234",
    "description": "上线订单服务 v2.3.1",
    "status": "completed"
  }
]`)

const severityTagType: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  normal: 'default',
  warning: 'warning',
  high: 'error',
  info: 'info',
  low: 'info',
  critical: 'error',
  fatal: 'error',
}

function tagSeverity(s?: string) {
  return severityTagType[s || ''] || 'default'
}

const changeTypeColor: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error' | 'primary'> = {
  deployment: 'primary',
  config_change: 'warning',
  migration: 'info',
  scaling: 'info',
  restart: 'default',
  rollback: 'error',
  patch: 'success',
  other: 'default',
}

function formatTime(t?: string): string {
  if (!t) return '-'
  try {
    return new Date(t).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return t
  }
}

const columns = computed<DataTableColumns<Change>>(() => [
  {
    title: '变更类型',
    key: 'change_type',
    width: 120,
    render: (row) =>
      h(
        NTag,
        { type: changeTypeColor[row.change_type] || 'default', size: 'small' },
        { default: () => row.change_type },
      ),
  },
  {
    title: '描述',
    key: 'description',
    ellipsis: { tooltip: true },
  },
  {
    title: '服务',
    key: 'service',
    width: 140,
    render: (row) => row.service || '-',
  },
  {
    title: '主机',
    key: 'host',
    width: 140,
    render: (row) => row.host || '-',
  },
  {
    title: '严重度',
    key: 'severity',
    width: 90,
    render: (row) =>
      h(NTag, { type: tagSeverity(row.severity), size: 'small' }, { default: () => row.severity || '-' }),
  },
  {
    title: '状态',
    key: 'status',
    width: 110,
    render: (row) => {
      const t = row.status === 'completed' ? 'success' : row.status === 'failed' ? 'error' : 'warning'
      return h(NTag, { type: t, size: 'small' }, { default: () => row.status || '-' })
    },
  },
  {
    title: '作者',
    key: 'author',
    width: 100,
  },
  {
    title: '时间',
    key: 'timestamp',
    width: 180,
    render: (row) => formatTime(row.timestamp),
  },
  {
    title: '操作',
    key: 'actions',
    width: 120,
    render: (row) =>
      h(
        NButton,
        { size: 'small', quaternary: true, onClick: () => openDetail(row.change_id || row.id || '') },
        { default: () => '详情' },
      ),
  },
])

async function loadChanges() {
  loading.value = true
  try {
    const res = await listChanges(serviceFilter.value.trim(), 100)
    changes.value = res.changes || []
  } catch (err: any) {
    message.error(err?.message || '加载变更失败')
  } finally {
    loading.value = false
  }
}

async function openDetail(changeId: string) {
  if (!changeId) return
  detailVisible.value = true
  detailLoading.value = true
  currentChange.value = null
  try {
    const ch = await getChange(changeId)
    currentChange.value = ch
  } catch (err: any) {
    message.error(err?.message || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

async function handleCorrelate() {
  if (correlateLoading.value) return
  correlateLoading.value = true
  correlateResult.value = null
  try {
    const res = await correlateChanges(
      correlateForm.sinceHours,
      correlateForm.timeWindowMinutes,
    )
    correlateResult.value = res
    message.success('关联完成')
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '关联失败')
  } finally {
    correlateLoading.value = false
  }
}

async function handleIngest() {
  if (ingestLoading.value) return
  let parsed: any[]
  try {
    parsed = JSON.parse(ingestJson.value)
    if (!Array.isArray(parsed)) throw new Error('必须是数组')
  } catch (e: any) {
    message.error('JSON 解析失败: ' + e.message)
    return
  }
  ingestLoading.value = true
  try {
    const res = await ingestChanges(parsed)
    message.success(`已接收 ${res.ingested || parsed.length} 条变更`)
    ingestVisible.value = false
    await loadChanges()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '接收失败')
  } finally {
    ingestLoading.value = false
  }
}

onMounted(() => {
  loadChanges()
})
</script>

<template>
  <div class="changes-view">
    <div class="page-header">
      <h2 class="page-title">变更关联</h2>
      <p class="page-desc">接收变更事件、查询变更列表、关联变更与 Incident</p>
    </div>

    <n-card :bordered="true">
      <template #header>
        <n-space align="center" :size="12">
          <span>变更列表</span>
          <n-input
            v-model:value="serviceFilter"
            placeholder="按服务过滤"
            size="small"
            style="width: 200px;"
            clearable
            @keyup.enter="loadChanges"
          />
          <n-button quaternary size="small" @click="loadChanges">刷新</n-button>
          <n-button size="small" type="primary" @click="ingestVisible = true">
            录入变更
          </n-button>
          <n-button size="small" type="warning" @click="correlateVisible = true">
            关联分析
          </n-button>
        </n-space>
      </template>

      <div v-if="loading" class="loading-container">
        <n-spin size="large" />
      </div>
      <n-empty
        v-else-if="!changes.length"
        description="暂无变更记录"
        style="padding: 60px 0;"
      />
      <n-data-table
        v-else
        :columns="columns"
        :data="changes"
        :bordered="false"
        :pagination="{ pageSize: 15 }"
        size="small"
      />
    </n-card>

    <!-- 详情抽屉 -->
    <n-drawer v-model:show="detailVisible" :width="600" placement="right">
      <n-drawer-content title="变更详情" closable>
        <div v-if="detailLoading" class="loading-container">
          <n-spin size="large" />
        </div>
        <template v-else-if="currentChange">
          <n-descriptions :column="1" bordered label-placement="left" size="small">
            <n-descriptions-item label="变更 ID">
              <code>{{ currentChange.change_id || currentChange.id }}</code>
            </n-descriptions-item>
            <n-descriptions-item label="变更类型">
              <n-tag :type="changeTypeColor[currentChange.change_type] || 'default'" size="small">
                {{ currentChange.change_type }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="描述">
              {{ currentChange.description || '-' }}
            </n-descriptions-item>
            <n-descriptions-item label="服务 / 主机">
              {{ currentChange.service || '-' }} / {{ currentChange.host || '-' }}
            </n-descriptions-item>
            <n-descriptions-item label="严重度">
              <n-tag :type="tagSeverity(currentChange.severity)" size="small">
                {{ currentChange.severity || '-' }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="状态">
              <n-tag
                :type="currentChange.status === 'completed' ? 'success' : currentChange.status === 'failed' ? 'error' : 'warning'"
                size="small"
              >
                {{ currentChange.status || '-' }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="作者">
              {{ currentChange.author || '-' }}
            </n-descriptions-item>
            <n-descriptions-item label="工单">
              {{ currentChange.ticket_id || '-' }}
            </n-descriptions-item>
            <n-descriptions-item label="时间">
              {{ formatTime(currentChange.timestamp) }}
            </n-descriptions-item>
            <n-descriptions-item v-if="currentChange.rollback_of" label="回滚目标">
              <code>{{ currentChange.rollback_of }}</code>
            </n-descriptions-item>
            <n-descriptions-item v-if="currentChange.attributes" label="附加属性">
              <pre style="font-size: 12px; margin: 0; white-space: pre-wrap;">{{ JSON.stringify(currentChange.attributes, null, 2) }}</pre>
            </n-descriptions-item>
            <n-descriptions-item v-if="currentChange.related_incidents?.length" label="关联 Incident">
              <n-space :size="4">
                <n-tag
                  v-for="inc in currentChange.related_incidents"
                  :key="inc.incident_id"
                  size="small"
                  type="warning"
                >
                  {{ inc.incident_id }}
                </n-tag>
              </n-space>
            </n-descriptions-item>
          </n-descriptions>
        </template>
      </n-drawer-content>
    </n-drawer>

    <!-- 关联分析弹窗 -->
    <n-modal
      v-model:show="correlateVisible"
      preset="card"
      title="变更 ↔ Incident 关联分析"
      style="width: 700px; max-width: 95vw;"
    >
      <n-form label-placement="left" :show-feedback="false" inline>
        <n-form-item label="时间范围（小时）">
          <n-input-number v-model:value="correlateForm.sinceHours" :min="1" :max="720" />
        </n-form-item>
        <n-form-item label="时间窗口（分钟）">
          <n-input-number v-model:value="correlateForm.timeWindowMinutes" :min="1" :max="1440" />
        </n-form-item>
        <n-form-item>
          <n-button type="primary" :loading="correlateLoading" @click="handleCorrelate">
            执行关联
          </n-button>
        </n-form-item>
      </n-form>

      <div v-if="correlateLoading" class="loading-container">
        <n-spin size="large" />
      </div>
      <template v-else-if="correlateResult">
        <n-descriptions :column="2" bordered size="small" style="margin-top: 16px;">
          <n-descriptions-item label="扫描变更数">
            {{ correlateResult.changes_scanned ?? correlateResult.total_changes ?? '-' }}
          </n-descriptions-item>
          <n-descriptions-item label="关联对数">
            {{ correlateResult.correlations?.length ?? correlateResult.total_correlations ?? '-' }}
          </n-descriptions-item>
        </n-descriptions>
        <pre style="margin-top: 12px; font-size: 12px; background: #f5f5f5; padding: 8px; border-radius: 4px; max-height: 400px; overflow: auto;">{{ JSON.stringify(correlateResult, null, 2) }}</pre>
      </template>
    </n-modal>

    <!-- 录入变更弹窗 -->
    <n-modal
      v-model:show="ingestVisible"
      preset="card"
      title="录入变更事件"
      style="width: 700px; max-width: 95vw;"
    >
      <p style="font-size: 13px; color: #888; margin: 0 0 8px;">
        请粘贴变更事件 JSON 数组，字段说明见 API 文档
      </p>
      <n-input
        v-model:value="ingestJson"
        type="textarea"
        :rows="14"
        placeholder="[{...}]"
        style="font-family: monospace; font-size: 12px;"
      />
      <n-space style="margin-top: 12px;" justify="end">
        <n-button @click="ingestVisible = false">取消</n-button>
        <n-button type="primary" :loading="ingestLoading" @click="handleIngest">
          提交
        </n-button>
      </n-space>
    </n-modal>
  </div>
</template>

<style scoped>
.changes-view {
  max-width: 1200px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 24px;
}

.page-title {
  font-size: 24px;
  font-weight: 600;
  margin: 0 0 8px;
}

.page-desc {
  font-size: 14px;
  color: var(--n-text-color-2, #6b7280);
  margin: 0;
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  gap: 16px;
}
</style>
