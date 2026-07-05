<script setup lang="ts">
import { ref, computed, onMounted, h } from 'vue'
import {
  NDataTable,
  NButton,
  NCard,
  NSpace,
  NTag,
  NModal,
  NInput,
  NStatistic,
  NGrid,
  NGi,
  NSelect,
  NSpin,
  NEmpty,
  useMessage,
} from 'naive-ui'
import {
  getReviewQueue,
  getReviewStats,
  approveReview,
  rejectReview,
  batchApprove,
} from '@/api/review'
import type { ReviewItem, ReviewStats } from '@/types/api'

const message = useMessage()

const loading = ref(false)
const statsLoading = ref(false)
const items = ref<ReviewItem[]>([])
const total = ref(0)
const limit = ref(10)
const offset = ref(0)
const statusFilter = ref<string | null>(null)

const reviewStats = ref<ReviewStats | null>(null)

const checkedRowKeys = ref<string[]>([])

const rejectModalVisible = ref(false)
const currentRejectItem = ref<ReviewItem | null>(null)
const rejectReason = ref('')

const statusOptions = [
  { label: '全部', value: null },
  { label: '待审', value: 'pending' },
  { label: '已批准', value: 'approved' },
  { label: '已拒绝', value: 'rejected' },
]

const statusTagType: Record<string, 'warning' | 'success' | 'error'> = {
  pending: 'warning',
  approved: 'success',
  rejected: 'error',
}

const statusText: Record<string, string> = {
  pending: '待审',
  approved: '已批准',
  rejected: '已拒绝',
}

const statCards = computed(() => [
  { label: '总数', value: reviewStats.value?.total ?? 0, color: '#2080f0' },
  { label: '待审', value: reviewStats.value?.pending ?? 0, color: '#f0a020' },
  { label: '已批准', value: reviewStats.value?.approved ?? 0, color: '#18a058' },
  { label: '已拒绝', value: reviewStats.value?.rejected ?? 0, color: '#d03050' },
])

const columns = [
  {
    type: 'selection' as const,
    width: 50,
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
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render(row: ReviewItem) {
      return h(NTag, { type: statusTagType[row.status], size: 'small' }, { default: () => statusText[row.status] })
    },
  },
  {
    title: '来源文档',
    key: 'source_doc_id',
    width: 180,
    ellipsis: { tooltip: true },
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 180,
    render(row: ReviewItem) {
      return formatDate(row.created_at)
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 160,
    render(row: ReviewItem) {
      const disabled = row.status !== 'pending'
      return h(NSpace, { size: 'small' }, {
        default: () => [
          h(NButton, { size: 'small', type: 'primary', disabled, onClick: () => handleApprove(row.id) }, { default: () => '批准' }),
          h(NButton, { size: 'small', type: 'error', disabled, onClick: () => handleReject(row) }, { default: () => '拒绝' }),
        ],
      })
    },
  },
]

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

async function fetchStats() {
  statsLoading.value = true
  try {
    reviewStats.value = await getReviewStats()
  } catch (e) {
    message.error('获取统计数据失败')
  } finally {
    statsLoading.value = false
  }
}

async function fetchItems() {
  loading.value = true
  try {
    const response = await getReviewQueue(
      statusFilter.value || undefined,
      limit.value,
      offset.value,
    )
    items.value = response.items
    total.value = response.total
  } catch (e) {
    message.error('获取审查列表失败')
  } finally {
    loading.value = false
  }
}

async function handleApprove(itemId: string) {
  try {
    await approveReview(itemId)
    message.success('已批准')
    await Promise.all([fetchStats(), fetchItems()])
  } catch (e) {
    message.error('批准失败')
  }
}

function handleReject(item: ReviewItem) {
  currentRejectItem.value = item
  rejectReason.value = ''
  rejectModalVisible.value = true
}

async function confirmReject() {
  if (!currentRejectItem.value) return
  try {
    await rejectReview(currentRejectItem.value.id, rejectReason.value)
    message.success('已拒绝')
    rejectModalVisible.value = false
    await Promise.all([fetchStats(), fetchItems()])
  } catch (e) {
    message.error('拒绝失败')
  }
}

async function handleBatchApprove() {
  if (checkedRowKeys.value.length === 0) {
    message.warning('请先选择要批准的项目')
    return
  }
  try {
    await batchApprove(checkedRowKeys.value)
    message.success(`已批准 ${checkedRowKeys.value.length} 项`)
    checkedRowKeys.value = []
    await Promise.all([fetchStats(), fetchItems()])
  } catch (e) {
    message.error('批量批准失败')
  }
}

function handlePageChange(page: number) {
  offset.value = (page - 1) * limit.value
  fetchItems()
}

function handlePageSizeChange(size: number) {
  limit.value = size
  offset.value = 0
  fetchItems()
}

function handleStatusChange() {
  offset.value = 0
  checkedRowKeys.value = []
  fetchItems()
}

onMounted(() => {
  fetchStats()
  fetchItems()
})
</script>

<template>
  <div class="review-view">
    <NSpin :show="statsLoading">
      <NGrid :cols="4" :x-gap="16" :y-gap="16" class="stats-grid">
        <NGi v-for="card in statCards" :key="card.label">
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
      <div class="action-bar">
        <NSpace size="medium">
          <NButton
            type="primary"
            :disabled="checkedRowKeys.length === 0"
            @click="handleBatchApprove"
          >
            批量批准 ({{ checkedRowKeys.length }})
          </NButton>
          <NSelect
            :options="statusOptions"
            v-model:value="statusFilter"
            style="width: 150px"
            @update:value="handleStatusChange"
          />
        </NSpace>
      </div>

      <NDataTable
        :columns="columns"
        :data="items"
        :loading="loading"
        :row-key="(row: ReviewItem) => row.id"
        v-model:checked-row-keys="checkedRowKeys"
        :pagination="{
          total,
          pageSize: limit,
          showSizePicker: true,
          pageSizes: [10, 20, 50],
          onChange: handlePageChange,
          onUpdatePageSize: handlePageSizeChange,
        }"
      >
        <template #empty>
          <NEmpty description="暂无数据" />
        </template>
      </NDataTable>
    </NCard>

    <NModal
      v-model:show="rejectModalVisible"
      preset="dialog"
      title="拒绝原因"
      positive-text="确认拒绝"
      negative-text="取消"
      @positive-click="confirmReject"
    >
      <div class="reject-form">
        <p v-if="currentRejectItem" class="reject-item">
          拒绝项目：<strong>{{ currentRejectItem.title }}</strong>
        </p>
        <NInput
          v-model:value="rejectReason"
          type="textarea"
          placeholder="请输入拒绝原因..."
          :rows="4"
        />
      </div>
    </NModal>
  </div>
</template>

<style scoped>
.review-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.stats-grid {
  margin-bottom: 4px;
}

.table-card {
  flex: 1;
}

.action-bar {
  margin-bottom: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.reject-form {
  margin-top: 12px;
}

.reject-item {
  margin-bottom: 12px;
  color: var(--n-text-color, #333);
}
</style>
