<script setup lang="ts">
import { ref, onMounted, h } from 'vue'
import { NGrid, NGi, NCard, NStatistic, NDataTable, NSpin, NTag } from 'naive-ui'
import { getDocumentStats, listDocuments } from '@/api/documents'
import { getReviewStats, getReviewQueue } from '@/api/review'
import { getSearchStats } from '@/api/search'
import api from '@/api/index'
import type { DocumentMeta, ReviewItem, GraphStats, DocumentStats, ReviewStats } from '@/types/api'

const loading = ref(true)

const documentStats = ref<DocumentStats | null>(null)
const graphStats = ref<GraphStats | null>(null)
const reviewStats = ref<ReviewStats | null>(null)
const searchStats = ref<{ total_documents: number; total_indexed: number } | null>(null)

const recentDocuments = ref<DocumentMeta[]>([])
const recentReviews = ref<ReviewItem[]>([])

const documentColumns = [
  { title: '文件名', key: 'filename' },
  { title: '格式', key: 'format', width: 100 },
  {
    title: '大小',
    key: 'size',
    width: 120,
    render: (row: DocumentMeta) => formatFileSize(row.size),
  },
  {
    title: '状态',
    key: 'status',
    width: 120,
    render: (row: DocumentMeta) => {
      const typeMap: Record<string, 'default' | 'info' | 'success' | 'error'> = {
        uploaded: 'default',
        parsing: 'info',
        parsed: 'success',
        failed: 'error',
      }
      const labelMap: Record<string, string> = {
        uploaded: '已上传',
        parsing: '解析中',
        parsed: '已解析',
        failed: '失败',
      }
      return h(
        NTag,
        { type: typeMap[row.status] || 'default' },
        { default: () => labelMap[row.status] || row.status },
      )
    },
  },
  {
    title: '时间',
    key: 'created_at',
    width: 180,
    render: (row: DocumentMeta) => formatDate(row.created_at),
  },
]

const reviewColumns = [
  { title: '标题', key: 'title' },
  { title: '类型', key: 'type', width: 120 },
  {
    title: '状态',
    key: 'status',
    width: 120,
    render: (row: ReviewItem) => {
      const typeMap: Record<string, 'default' | 'info' | 'success' | 'error'> = {
        pending: 'info',
        approved: 'success',
        rejected: 'error',
      }
      const labelMap: Record<string, string> = {
        pending: '待审查',
        approved: '已通过',
        rejected: '已拒绝',
      }
      return h(
        NTag,
        { type: typeMap[row.status] || 'default' },
        { default: () => labelMap[row.status] || row.status },
      )
    },
  },
  {
    title: '时间',
    key: 'created_at',
    width: 180,
    render: (row: ReviewItem) => formatDate(row.created_at),
  },
]

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

async function fetchData() {
  loading.value = true
  try {
    const [docStatsRes, graphStatsRes, reviewStatsRes, searchStatsRes, docListRes, reviewQueueRes] =
      await Promise.all([
        getDocumentStats(),
        api.get<any, GraphStats>('/graph/stats'),
        getReviewStats(),
        getSearchStats(),
        listDocuments({ limit: 5 }),
        getReviewQueue(undefined, 5, 0),
      ])

    documentStats.value = docStatsRes
    graphStats.value = graphStatsRes
    reviewStats.value = reviewStatsRes
    searchStats.value = searchStatsRes
    recentDocuments.value = docListRes.documents || []
    recentReviews.value = reviewQueueRes.items || []
  } catch (error) {
    console.error('Failed to fetch dashboard data:', error)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchData()
})
</script>

<template>
  <div class="dashboard-container">
    <NSpin :show="loading" size="large">
      <NGrid :cols="4" :x-gap="16" :y-gap="16" class="stats-grid">
        <NGi>
          <NCard>
            <NStatistic label="文档总数" :value="documentStats?.total ?? 0" />
          </NCard>
        </NGi>
        <NGi>
          <NCard>
            <NStatistic label="知识实体数" :value="graphStats?.total_entities ?? 0" />
          </NCard>
        </NGi>
        <NGi>
          <NCard>
            <NStatistic
              label="审查待办"
              :value="reviewStats?.pending ?? 0"
              value-style="color: #f0a020"
            />
          </NCard>
        </NGi>
        <NGi>
          <NCard>
            <NStatistic label="搜索索引数" :value="searchStats?.total_indexed ?? 0" />
          </NCard>
        </NGi>
      </NGrid>

      <NGrid :cols="2" :x-gap="16" :y-gap="16" class="content-grid">
        <NGi>
          <NCard title="最近文档" :bordered="true">
            <NDataTable
              :columns="documentColumns"
              :data="recentDocuments"
              :bordered="false"
              size="medium"
            />
          </NCard>
        </NGi>
        <NGi>
          <NCard title="最近审查" :bordered="true">
            <NDataTable
              :columns="reviewColumns"
              :data="recentReviews"
              :bordered="false"
              size="medium"
            />
          </NCard>
        </NGi>
      </NGrid>
    </NSpin>
  </div>
</template>

<style scoped>
.dashboard-container {
  padding: 24px;
}

.stats-grid {
  margin-bottom: 24px;
}

.content-grid {
  margin-top: 0;
}
</style>
