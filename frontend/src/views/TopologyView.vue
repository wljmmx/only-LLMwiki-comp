<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  NCard,
  NTag,
  NSpace,
  NButton,
  NSpin,
  NEmpty,
  NSelect,
  NInput,
  NDrawer,
  NDrawerContent,
  NDescriptions,
  NDescriptionsItem,
  NModal,
  NAlert,
  useMessage,
} from 'naive-ui'
import {
  getTopology,
  rebuildTopology,
  getNodeNeighbors,
  getImpactAnalysis,
  type TopologyNode,
  type TopologyEdge,
} from '@/api/aiops'
import { nodeTypeColor } from '@/utils/statusMap'

const message = useMessage()

const loading = ref(false)
const rebuilding = ref(false)
const topology = ref<{ nodes: TopologyNode[]; edges: TopologyEdge[] }>({ nodes: [], edges: [] })

const nodeTypeFilter = ref<string | null>(null)
const relationFilter = ref<string | null>(null)
const searchKeyword = ref('')

const nodeTypeOptions = [
  { label: '全部', value: '' },
  { label: 'Host', value: 'Host' },
  { label: 'Service', value: 'Service' },
  { label: 'Component', value: 'Component' },
]
const relationOptions = [
  { label: '全部', value: '' },
  { label: 'RUNS_ON', value: 'RUNS_ON' },
  { label: 'DEPENDS_ON', value: 'DEPENDS_ON' },
  { label: 'USES', value: 'USES' },
]

// 详情抽屉
const detailVisible = ref(false)
const detailLoading = ref(false)
const selectedNode = ref<TopologyNode | null>(null)
const neighbors = ref<any>(null)
const impact = ref<any>(null)

// 影响分析弹窗
const impactVisible = ref(false)
const impactLoading = ref(false)
const impactForNode = ref<string>('')
const impactResult = ref<any>(null)

// 节点类型色（P1-19: 已迁移至 @/utils/statusMap，引用 CSS 变量）

const nodeTypeBg: Record<string, string> = {
  Host: 'rgba(32, 128, 240, 0.15)',
  Service: 'rgba(240, 160, 32, 0.15)',
  Component: 'rgba(24, 160, 88, 0.15)',
}

const relationColor: Record<string, string> = {
  RUNS_ON: '#2080f0',
  DEPENDS_ON: '#f0a020',
  USES: '#18a058',
}

// 节点位置布局：基于类型分层（Host 顶层 / Service 中层 / Component 底层）
const layoutNodes = computed(() => {
  const filtered = filterNodes(topology.value.nodes)
  const byType: Record<string, TopologyNode[]> = {}
  filtered.forEach((n) => {
    const t = n.type || 'Other'
    if (!byType[t]) byType[t] = []
    byType[t].push(n)
  })

  const placed: Record<string, { x: number; y: number; node: TopologyNode }> = {}
  const layerOrder = ['Host', 'Service', 'Component', 'Other']
  const layerY: Record<string, number> = {
    Host: 60,
    Service: 240,
    Component: 420,
    Other: 600,
  }

  layerOrder.forEach((type) => {
    const arr = byType[type] || []
    const totalWidth = Math.max(900, arr.length * 160)
    arr.forEach((node, idx) => {
      const x = (totalWidth / (arr.length + 1)) * (idx + 1)
      placed[node.name] = { x, y: layerY[type] || 600, node }
    })
  })

  return { placed, byType, layerOrder }
})

const svgWidth = computed(() => {
  const maxPerLayer = Math.max(
    1,
    ...Object.values(layoutNodes.value.byType).map((arr) => arr.length),
  )
  return Math.max(900, maxPerLayer * 180)
})

const svgHeight = 700

const filteredEdges = computed(() => filterEdges(topology.value.edges))

function filterNodes(nodes: TopologyNode[]): TopologyNode[] {
  return nodes.filter((n) => {
    if (nodeTypeFilter.value && n.type !== nodeTypeFilter.value) return false
    if (searchKeyword.value.trim()) {
      const kw = searchKeyword.value.trim().toLowerCase()
      if (!n.name.toLowerCase().includes(kw)) return false
    }
    return true
  })
}

function filterEdges(edges: TopologyEdge[]): TopologyEdge[] {
  return edges.filter((e) => {
    if (relationFilter.value && e.relation !== relationFilter.value) return false
    const placed = layoutNodes.value.placed
    if (!placed[e.source] || !placed[e.target]) return false
    return true
  })
}

async function loadTopology() {
  loading.value = true
  try {
    const res = await getTopology(
      nodeTypeFilter.value || undefined,
      relationFilter.value || undefined,
    )
    topology.value = {
      nodes: res.nodes || [],
      edges: res.edges || [],
    }
  } catch (err: any) {
    message.error(err?.message || '加载拓扑失败')
  } finally {
    loading.value = false
  }
}

async function handleRebuild() {
  if (rebuilding.value) return
  rebuilding.value = true
  try {
    const res = await rebuildTopology(100)
    message.success(`重建完成: ${res.nodes?.length || 0} 节点, ${res.edges?.length || 0} 关系`)
    await loadTopology()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '重建失败')
  } finally {
    rebuilding.value = false
  }
}

async function openNodeDetail(node: TopologyNode) {
  detailVisible.value = true
  detailLoading.value = true
  selectedNode.value = node
  neighbors.value = null
  impact.value = null
  try {
    const [nb, im] = await Promise.all([
      getNodeNeighbors(node.name, 1).catch(() => null),
      getImpactAnalysis(node.name).catch(() => null),
    ])
    neighbors.value = nb
    impact.value = im
  } finally {
    detailLoading.value = false
  }
}

async function runImpactForNode(name: string) {
  impactForNode.value = name
  impactVisible.value = true
  impactLoading.value = true
  impactResult.value = null
  try {
    impactResult.value = await getImpactAnalysis(name)
  } catch (err: any) {
    message.error(err?.message || '影响分析失败')
  } finally {
    impactLoading.value = false
  }
}

onMounted(() => {
  loadTopology()
})
</script>

<template>
  <div class="topology-view">
    <div class="page-header">
      <h2 class="page-title">服务拓扑</h2>
      <p class="page-desc">从知识库抽取的服务依赖关系图，支持节点详情、邻居查询、影响分析</p>
    </div>

    <n-card :bordered="true">
      <template #header>
        <n-space align="center" :size="12" wrap>
          <span>拓扑图</span>
          <n-select
            v-model:value="nodeTypeFilter"
            :options="nodeTypeOptions"
            size="small"
            style="width: 140px"
            placeholder="节点类型"
            @update:value="loadTopology"
          />
          <n-select
            v-model:value="relationFilter"
            :options="relationOptions"
            size="small"
            style="width: 140px"
            placeholder="关系类型"
            @update:value="loadTopology"
          />
          <n-input
            v-model:value="searchKeyword"
            size="small"
            placeholder="搜索节点名"
            style="width: 200px"
            clearable
          />
          <n-button quaternary size="small" :loading="loading" @click="loadTopology">刷新</n-button>
          <n-button size="small" type="error" :loading="rebuilding" @click="handleRebuild">
            重建拓扑
          </n-button>
        </n-space>
      </template>

      <div v-if="loading" class="loading-container">
        <n-spin size="large" />
      </div>

      <n-empty
        v-else-if="!topology.nodes.length"
        description="暂无拓扑数据，请上传包含服务依赖信息的文档后点击「重建拓扑」"
        style="padding: 80px 0"
      />

      <div v-else class="topology-canvas">
        <svg :width="svgWidth" :height="svgHeight" class="topology-svg">
          <!-- 边 -->
          <g class="edges">
            <g v-for="(edge, idx) in filteredEdges" :key="`edge-${idx}`">
              <line
                v-if="layoutNodes.placed[edge.source] && layoutNodes.placed[edge.target]"
                :x1="layoutNodes.placed[edge.source].x"
                :y1="layoutNodes.placed[edge.source].y"
                :x2="layoutNodes.placed[edge.target].x"
                :y2="layoutNodes.placed[edge.target].y"
                :stroke="relationColor[edge.relation] || '#999'"
                stroke-width="1.5"
                marker-end="url(#arrow)"
              />
              <text
                v-if="layoutNodes.placed[edge.source] && layoutNodes.placed[edge.target]"
                :x="(layoutNodes.placed[edge.source].x + layoutNodes.placed[edge.target].x) / 2"
                :y="(layoutNodes.placed[edge.source].y + layoutNodes.placed[edge.target].y) / 2 - 4"
                font-size="10"
                fill="currentColor"
                text-anchor="middle"
              >
                {{ edge.relation }}
              </text>
            </g>
          </g>

          <!-- 箭头定义 -->
          <defs>
            <marker
              id="arrow"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" :fill="'#999'" />
            </marker>
          </defs>

          <!-- 节点 -->
          <g class="nodes">
            <g
              v-for="(item, name) in layoutNodes.placed"
              :key="name"
              :transform="`translate(${item.x}, ${item.y})`"
              class="node-group"
              tabindex="0"
              role="button"
              :aria-label="item.node.name"
              @click="openNodeDetail(item.node)"
              @keydown.enter="openNodeDetail(item.node)"
              @keydown.space.prevent="openNodeDetail(item.node)"
            >
              <rect
                :x="-70"
                :y="-22"
                width="140"
                height="44"
                rx="6"
                :fill="nodeTypeBg[item.node.type] || 'rgba(150,150,150,0.15)'"
                :stroke="nodeTypeColor[item.node.type] || '#999'"
                stroke-width="1.5"
              />
              <text
                x="0"
                y="-4"
                font-size="12"
                font-weight="600"
                fill="currentColor"
                text-anchor="middle"
              >
                {{
                  item.node.name.length > 16 ? item.node.name.slice(0, 14) + '…' : item.node.name
                }}
              </text>
              <text
                x="0"
                y="12"
                font-size="10"
                :fill="nodeTypeColor[item.node.type] || '#999'"
                text-anchor="middle"
              >
                {{ item.node.type }}
              </text>
            </g>
          </g>
        </svg>

        <!-- 图例 -->
        <div class="legend">
          <div class="legend-item">
            <span class="legend-dot" :style="{ background: nodeTypeColor.Host }"></span>
            <span>Host</span>
          </div>
          <div class="legend-item">
            <span class="legend-dot" :style="{ background: nodeTypeColor.Service }"></span>
            <span>Service</span>
          </div>
          <div class="legend-item">
            <span class="legend-dot" :style="{ background: nodeTypeColor.Component }"></span>
            <span>Component</span>
          </div>
          <div class="legend-divider"></div>
          <div class="legend-item">
            <span class="legend-line" :style="{ background: relationColor.RUNS_ON }"></span>
            <span>RUNS_ON</span>
          </div>
          <div class="legend-item">
            <span class="legend-line" :style="{ background: relationColor.DEPENDS_ON }"></span>
            <span>DEPENDS_ON</span>
          </div>
          <div class="legend-item">
            <span class="legend-line" :style="{ background: relationColor.USES }"></span>
            <span>USES</span>
          </div>
        </div>
      </div>
    </n-card>

    <!-- 节点详情抽屉 -->
    <n-drawer v-model:show="detailVisible" :width="720" placement="right">
      <n-drawer-content title="节点详情" closable>
        <div v-if="detailLoading" class="loading-container">
          <n-spin size="large" />
        </div>
        <template v-else-if="selectedNode">
          <n-descriptions :column="1" bordered label-placement="left" size="small">
            <n-descriptions-item label="节点名">
              <code>{{ selectedNode.name }}</code>
            </n-descriptions-item>
            <n-descriptions-item label="类型">
              <n-tag size="small" :style="{ color: nodeTypeColor[selectedNode.type] }">
                {{ selectedNode.type }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item v-if="selectedNode.attributes" label="属性">
              <pre style="font-size: 12px; margin: 0; white-space: pre-wrap">{{
                JSON.stringify(selectedNode.attributes, null, 2)
              }}</pre>
            </n-descriptions-item>
          </n-descriptions>

          <n-space style="margin-top: 16px" :size="8">
            <n-button size="small" type="warning" @click="runImpactForNode(selectedNode.name)">
              影响分析
            </n-button>
          </n-space>

          <h4 style="margin-top: 24px; margin-bottom: 8px">邻居节点</h4>
          <n-empty v-if="!neighbors?.neighbors?.length" description="无邻居" />
          <n-space v-else vertical :size="6">
            <n-card v-for="nb in neighbors.neighbors" :key="nb.name" size="small" :bordered="true">
              <n-space align="center" :size="8">
                <n-tag size="small" :style="{ color: nodeTypeColor[nb.type] }">
                  {{ nb.type }}
                </n-tag>
                <span style="font-weight: 600">{{ nb.name }}</span>
                <n-tag
                  v-if="nb.direction"
                  size="small"
                  :type="nb.direction === 'upstream' ? 'info' : 'warning'"
                >
                  {{ nb.direction }}
                </n-tag>
                <n-tag v-if="nb.relation" size="small">{{ nb.relation }}</n-tag>
              </n-space>
            </n-card>
          </n-space>
        </template>
      </n-drawer-content>
    </n-drawer>

    <!-- 影响分析弹窗 -->
    <n-modal
      v-model:show="impactVisible"
      preset="card"
      :title="`影响分析: ${impactForNode}`"
      style="width: 700px; max-width: 95vw"
    >
      <div v-if="impactLoading" class="loading-container">
        <n-spin size="large" />
      </div>
      <template v-else-if="impactResult">
        <n-alert v-if="impactResult.error" type="error" style="margin-bottom: 12px">
          {{ impactResult.error }}
        </n-alert>
        <n-descriptions :column="1" bordered size="small">
          <n-descriptions-item label="分析节点">
            <code>{{ impactResult.node || impactForNode }}</code>
          </n-descriptions-item>
          <n-descriptions-item label="上游受影响">
            {{ impactResult.upstream_affected?.length || 0 }} 个
          </n-descriptions-item>
          <n-descriptions-item label="下游受影响">
            {{ impactResult.downstream_affected?.length || 0 }} 个
          </n-descriptions-item>
        </n-descriptions>

        <div v-if="impactResult.upstream_affected?.length" style="margin-top: 12px">
          <h4 style="margin: 0 0 6px">上游受影响节点</h4>
          <n-space :size="4">
            <n-tag v-for="n in impactResult.upstream_affected" :key="n" size="small" type="info">
              {{ n }}
            </n-tag>
          </n-space>
        </div>
        <div v-if="impactResult.downstream_affected?.length" style="margin-top: 12px">
          <h4 style="margin: 0 0 6px">下游受影响节点</h4>
          <n-space :size="4">
            <n-tag
              v-for="n in impactResult.downstream_affected"
              :key="n"
              size="small"
              type="warning"
            >
              {{ n }}
            </n-tag>
          </n-space>
        </div>
      </template>
    </n-modal>
  </div>
</template>

<style scoped>
.topology-view {
  max-width: 1400px;
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

.topology-canvas {
  position: relative;
  overflow: auto;
  background: var(--n-color-target, #fafafa);
  border-radius: 6px;
  padding: 16px;
}

.topology-svg {
  display: block;
  background: transparent;
}

/* P0-2: 边标签文字色跟随主题（currentColor 引用） */
.edges {
  color: var(--opskg-text-2);
}

.node-group {
  cursor: pointer;
  transition: transform 0.15s ease;
  /* P0-2: 节点名文字色跟随主题（currentColor 引用） */
  color: var(--opskg-text-1);
}

.node-group:hover rect {
  stroke-width: 2.5;
  filter: drop-shadow(0 2px 4px rgba(0, 0, 0, 0.15));
}

/* P1-18: 键盘聚焦可见样式 */
.node-group:focus-visible {
  outline: 2px solid var(--opskg-color-primary);
  outline-offset: 2px;
}

.legend {
  position: absolute;
  top: 16px;
  right: 16px;
  background: var(--opskg-bg-elevated);
  border: 1px solid var(--n-border-color, #e5e5e5);
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  max-width: 240px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.legend-dot {
  width: 12px;
  height: 12px;
  border-radius: 3px;
}

.legend-line {
  width: 18px;
  height: 2px;
}

.legend-divider {
  width: 100%;
  height: 1px;
  background: var(--n-border-color, #e5e5e5);
  margin: 2px 0;
}
</style>
