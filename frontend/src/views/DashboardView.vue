<script setup lang="ts">
import { computed, h } from 'vue'
import { useRouter } from 'vue-router'
import { NGrid, NGi, NCard, NStatistic, NDataTable, NSkeleton, NTag, NAlert, NButton, NSpace } from 'naive-ui'
import { getDocumentStats, listDocuments } from '@/api/documents'
import { getReviewStats, getReviewQueue } from '@/api/review'
import { getSearchStats } from '@/api/search'
import api from '@/api/index'
import { useAsyncData } from '@/composables/useAsyncData'
import { formatFileSize, formatDateTime } from '@/utils/format'
import type { DocumentMeta, ReviewItem, GraphStats, DocumentStats, ReviewStats, SearchStats } from '@/types/api'

const router = useRouter()

// P2-8: 历史快照（localStorage），用于 sparkline 趋势 + 环比 delta
const SNAPSHOT_KEY = 'opskg:dashboard:snapshots'
const MAX_SNAPSHOTS = 8

interface Snapshot {
  ts: number
  docs: number
  entities: number
  pending: number
  indexed: number
}

function loadSnapshots(): Snapshot[] {
  try {
    const raw = localStorage.getItem(SNAPSHOT_KEY)
    if (!raw) return []
    return JSON.parse(raw) as Snapshot[]
  } catch {
    return []
  }
}

function saveSnapshot(s: Snapshot) {
  const snaps = loadSnapshots()
  snaps.push(s)
  if (snaps.length > MAX_SNAPSHOTS) snaps.shift()
  try {
    localStorage.setItem(SNAPSHOT_KEY, JSON.stringify(snaps))
  } catch { /* ignore */ }
}

interface DashboardData {
  documentStats: DocumentStats
  graphStats: GraphStats
  reviewStats: ReviewStats
  searchStats: SearchStats
  recentDocuments: DocumentMeta[]
  recentReviews: ReviewItem[]
}

const { data: dashboardData, loading, error, execute: fetchData } = useAsyncData<DashboardData>(
  async () => {
    const [docStatsRes, graphStatsRes, reviewStatsRes, searchStatsRes, docListRes, reviewQueueRes] =
      await Promise.all([
        getDocumentStats(),
        api.get<unknown, GraphStats>('/graph/stats'),
        getReviewStats(),
        getSearchStats(),
        listDocuments({ limit: 5 }),
        getReviewQueue(undefined, 5, 0),
      ])

    // P2-8: 保存快照（用于趋势）
    saveSnapshot({
      ts: Date.now(),
      docs: docStatsRes.total ?? 0,
      entities: graphStatsRes.total_entities ?? 0,
      pending: reviewStatsRes.pending ?? 0,
      indexed: searchStatsRes.indexed_docs ?? 0,
    })

    return {
      documentStats: docStatsRes,
      graphStats: graphStatsRes,
      reviewStats: reviewStatsRes,
      searchStats: searchStatsRes,
      recentDocuments: docListRes.documents || [],
      recentReviews: reviewQueueRes.items || [],
    }
  },
  { immediate: true },
)

// 分解为独立 computed 以保持模板与测试兼容
const documentStats = computed<DocumentStats | null>(() => dashboardData.value?.documentStats ?? null)
const graphStats = computed<GraphStats | null>(() => dashboardData.value?.graphStats ?? null)
const reviewStats = computed<ReviewStats | null>(() => dashboardData.value?.reviewStats ?? null)
const searchStats = computed<SearchStats | null>(() => dashboardData.value?.searchStats ?? null)
const recentDocuments = computed<DocumentMeta[]>(() => dashboardData.value?.recentDocuments ?? [])
const recentReviews = computed<ReviewItem[]>(() => dashboardData.value?.recentReviews ?? [])

const currentSnapshot = computed<Snapshot>(() => ({
  ts: Date.now(),
  docs: documentStats.value?.total ?? 0,
  entities: graphStats.value?.total_entities ?? 0,
  pending: reviewStats.value?.pending ?? 0,
  indexed: searchStats.value?.indexed_docs ?? 0,
}))

/** P2-8: 计算环比 delta（当前值 vs 上一快照） */
function computeDelta(current: number, key: keyof Snapshot): { value: number; pct: number } | null {
  const snaps = loadSnapshots()
  if (snaps.length < 2) return null
  const prev = snaps[snaps.length - 2][key]
  if (prev === 0) return { value: current - prev, pct: current > 0 ? 100 : 0 }
  const diff = current - prev
  const pct = Math.round((diff / prev) * 100)
  return { value: diff, pct }
}

/** P2-8: 生成 SVG sparkline 路径（使用历史快照数据点） */
function sparklinePath(key: keyof Snapshot, _color: string): string {
  const snaps = loadSnapshots()
  if (snaps.length === 0) return ''
  const values = snaps.map((s) => s[key])
  const max = Math.max(...values, 1)
  const min = Math.min(...values)
  const range = max - min || 1
  const w = 80
  const h = 24
  const padding = 2
  const points = values.map((v, i) => {
    const x = padding + (i / Math.max(values.length - 1, 1)) * (w - padding * 2)
    const y = h - padding - ((v - min) / range) * (h - padding * 2)
    return `${x},${y}`
  })
  return points.join(' ')
}

function deltaText(delta: { value: number; pct: number } | null): string {
  if (!delta) return ''
  const sign = delta.value >= 0 ? '+' : ''
  return `${sign}${delta.value} (${sign}${delta.pct}%)`
}

function deltaColor(delta: { value: number; pct: number } | null): string {
  if (!delta) return ''
  return delta.value >= 0 ? '#18a058' : '#d03050'
}

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
    render: (row: DocumentMeta) => formatDateTime(row.created_at),
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
    render: (row: ReviewItem) => formatDateTime(row.created_at),
  },
]

/** P2-8: 点击统计卡片下钻到对应页面 */
function drillTo(route: string) {
  router.push(route)
}
</script>

<template>
  <div class="dashboard-container">
    <!-- P2-9: 加载失败错误提示 + 重试按钮 -->
    <NAlert
      v-if="error && !loading"
      type="error"
      :show-icon="true"
      class="error-alert"
    >
      <template #header>{{ error || '加载仪表盘数据失败' }}</template>
      <NSpace style="margin-top: 8px">
        <NButton size="small" type="primary" @click="fetchData">重试</NButton>
      </NSpace>
    </NAlert>

    <template v-if="loading">
      <NGrid :cols="4" :x-gap="16" :y-gap="16" class="stats-grid">
        <NGi v-for="n in 4" :key="`stat-skel-${n}`">
          <NCard>
            <NSkeleton text :width="80" :height="14" />
            <NSkeleton text :width="120" :height="32" style="margin-top: 12px" />
          </NCard>
        </NGi>
      </NGrid>
      <NGrid :cols="2" :x-gap="16" :y-gap="16" class="content-grid">
        <NGi v-for="n in 2" :key="`content-skel-${n}`">
          <NCard>
            <NSkeleton text :width="120" :height="18" style="margin-bottom: 16px" />
            <NSkeleton text :repeat="5" />
          </NCard>
        </NGi>
      </NGrid>
    </template>
    <template v-else-if="!error">
      <NGrid :cols="4" :x-gap="16" :y-gap="16" class="stats-grid">
        <!-- P2-8: 统计卡片带 sparkline + delta + 点击下钻 -->
        <NGi>
          <NCard hoverable class="stat-card" @click="drillTo('/documents')">
            <NStatistic label="文档总数" :value="documentStats?.total ?? 0" />
            <div class="stat-extra">
              <svg
                v-if="loadSnapshots().length > 1"
                class="sparkline"
                width="80" height="24" viewBox="0 0 80 24"
              >
                <polyline
                  :points="sparklinePath('docs', '#2080f0')"
                  fill="none"
                  stroke="#2080f0"
                  stroke-width="1.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              <span
                v-if="computeDelta(currentSnapshot.docs, 'docs')"
                class="stat-delta"
                :style="{ color: deltaColor(computeDelta(currentSnapshot.docs, 'docs')) }"
              >
                {{ deltaText(computeDelta(currentSnapshot.docs, 'docs')) }}
              </span>
            </div>
          </NCard>
        </NGi>
        <NGi>
          <NCard hoverable class="stat-card" @click="drillTo('/graph')">
            <NStatistic label="知识实体数" :value="graphStats?.total_entities ?? 0" />
            <div class="stat-extra">
              <svg
                v-if="loadSnapshots().length > 1"
                class="sparkline"
                width="80" height="24" viewBox="0 0 80 24"
              >
                <polyline
                  :points="sparklinePath('entities', '#18a058')"
                  fill="none"
                  stroke="#18a058"
                  stroke-width="1.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              <span
                v-if="computeDelta(currentSnapshot.entities, 'entities')"
                class="stat-delta"
                :style="{ color: deltaColor(computeDelta(currentSnapshot.entities, 'entities')) }"
              >
                {{ deltaText(computeDelta(currentSnapshot.entities, 'entities')) }}
              </span>
            </div>
          </NCard>
        </NGi>
        <NGi>
          <NCard hoverable class="stat-card" @click="drillTo('/review')">
            <NStatistic
              label="审查待办"
              :value="reviewStats?.pending ?? 0"
              value-style="color: #f0a020"
            />
            <div class="stat-extra">
              <svg
                v-if="loadSnapshots().length > 1"
                class="sparkline"
                width="80" height="24" viewBox="0 0 80 24"
              >
                <polyline
                  :points="sparklinePath('pending', '#f0a020')"
                  fill="none"
                  stroke="#f0a020"
                  stroke-width="1.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              <span
                v-if="computeDelta(currentSnapshot.pending, 'pending')"
                class="stat-delta"
                :style="{ color: deltaColor(computeDelta(currentSnapshot.pending, 'pending')) }"
              >
                {{ deltaText(computeDelta(currentSnapshot.pending, 'pending')) }}
              </span>
            </div>
          </NCard>
        </NGi>
        <NGi>
          <NCard hoverable class="stat-card" @click="drillTo('/search')">
            <NStatistic label="搜索索引数" :value="searchStats?.indexed_docs ?? 0" />
            <div class="stat-extra">
              <svg
                v-if="loadSnapshots().length > 1"
                class="sparkline"
                width="80" height="24" viewBox="0 0 80 24"
              >
                <polyline
                  :points="sparklinePath('indexed', '#7c4dff')"
                  fill="none"
                  stroke="#7c4dff"
                  stroke-width="1.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              <span
                v-if="computeDelta(currentSnapshot.indexed, 'indexed')"
                class="stat-delta"
                :style="{ color: deltaColor(computeDelta(currentSnapshot.indexed, 'indexed')) }"
              >
                {{ deltaText(computeDelta(currentSnapshot.indexed, 'indexed')) }}
              </span>
            </div>
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
    </template>
  </div>
</template>

<style scoped>
.dashboard-container {
  padding: 24px;
}

.error-alert {
  margin-bottom: 20px;
}

.stats-grid {
  margin-bottom: 24px;
}

.content-grid {
  margin-top: 0;
}

/* P2-8: 统计卡片增强 */
.stat-card {
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--opskg-shadow-hover);
}

.stat-extra {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  min-height: 24px;
}

.sparkline {
  flex-shrink: 0;
  opacity: 0.7;
}

.stat-delta {
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}
</style>