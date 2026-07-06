<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
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
  NAlert,
  NStatistic,
  NGrid,
  NGi,
  useMessage,
} from 'naive-ui'
import { VueFlow, useVueFlow, type Node, type Edge } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import {
  getGraphVisualize,
  getGraphStats,
  searchGraph,
  getGraphEntity,
  type GraphNode,
  type GraphLink,
  type GraphStats,
  type GraphEntityDetail,
} from '@/api/graph'

const message = useMessage()

const loading = ref(false)
const graphData = ref<{ nodes: GraphNode[]; links: GraphLink[] }>({ nodes: [], links: [] })
const stats = ref<GraphStats | null>(null)
const neo4jError = ref<string>('')

const entityTypeFilter = ref<string | null>(null)
const limitFilter = ref<number>(200)
const searchKeyword = ref('')

const entityTypeOptions = [
  { label: '全部', value: '' },
  { label: 'Host (主机)', value: 'Host' },
  { label: 'Service (服务)', value: 'Service' },
  { label: 'Component (组件)', value: 'Component' },
  { label: 'Parameter (参数)', value: 'Parameter' },
  { label: 'Command (命令)', value: 'Command' },
  { label: 'Procedure (步骤)', value: 'Procedure' },
  { label: 'Incident (故障)', value: 'Incident' },
  { label: 'Symptom (现象)', value: 'Symptom' },
  { label: 'Experience (经验)', value: 'Experience' },
  { label: 'Concept (概念)', value: 'Concept' },
  { label: 'Document (文档)', value: 'Document' },
]

// 节点类型 -> 颜色
const nodeTypeColor: Record<string, string> = {
  Host: '#2080f0',
  Service: '#f0a020',
  Component: '#18a058',
  Parameter: '#7c4dff',
  Command: '#00bcd4',
  Procedure: '#ff5722',
  Incident: '#d03050',
  Symptom: '#e91e63',
  Experience: '#ff9800',
  Concept: '#607d8b',
  Document: '#9c27b0',
}

const nodeTypeLabel: Record<string, string> = {
  Host: '主机',
  Service: '服务',
  Component: '组件',
  Parameter: '参数',
  Command: '命令',
  Procedure: '步骤',
  Incident: '故障',
  Symptom: '现象',
  Experience: '经验',
  Concept: '概念',
  Document: '文档',
}

const relationColor: Record<string, string> = {
  RUNS_ON: '#2080f0',
  USES: '#18a058',
  DEPENDS_ON: '#f0a020',
  HAS_PARAMETER: '#7c4dff',
  CONFIGURED_BY: '#00bcd4',
  DESCRIBED_IN: '#9c27b0',
  INVOLVES: '#d03050',
  MANIFESTS_AS: '#e91e63',
  RESOLVED_BY: '#ff5722',
  DERIVED_FROM: '#ff9800',
  RELATED_TO: '#607d8b',
}

// Vue Flow 节点与边
const vfNodes = ref<Node[]>([])
const vfEdges = ref<Edge[]>([])

const { onNodeClick, fitView } = useVueFlow()

// 详情抽屉
const detailVisible = ref(false)
const detailLoading = ref(false)
const selectedEntity = ref<GraphEntityDetail | null>(null)
const selectedNodeName = ref<string>('')

// 搜索结果
const searchResults = ref<{ name: string; type: string; confidence?: number }[]>([])

// 简单的力导向布局（避免引入 d3-force 依赖）
// 算法：基于节点类型分组，按圆周布局；同类节点放在同一角度区间
function layoutNodes(nodes: GraphNode[], links: GraphLink[]): Node[] {
  if (!nodes.length) return []

  const placed: Record<string, { x: number; y: number }> = {}
  const cx = 600
  const cy = 400
  const radius = 280

  // 按类型分组
  const byType: Record<string, GraphNode[]> = {}
  nodes.forEach((n) => {
    const t = n.type || 'Other'
    if (!byType[t]) byType[t] = []
    byType[t].push(n)
  })

  const types = Object.keys(byType)
  const typeCount = types.length

  // 每个类型分配一个角度扇区
  types.forEach((type, ti) => {
    const groupNodes = byType[type]
    const sectorAngle = (2 * Math.PI) / typeCount
    const startAngle = ti * sectorAngle
    const groupRadius = Math.min(radius, 30 + groupNodes.length * 12)

    groupNodes.forEach((node, idx) => {
      if (groupNodes.length === 1) {
        placed[node.id] = {
          x: cx + groupRadius * Math.cos(startAngle + sectorAngle / 2),
          y: cy + groupRadius * Math.sin(startAngle + sectorAngle / 2),
        }
      } else {
        const subAngle = startAngle + (idx / (groupNodes.length - 1)) * sectorAngle
        const r = groupRadius + (idx % 2 === 0 ? 0 : 30)
        placed[node.id] = {
          x: cx + r * Math.cos(subAngle),
          y: cy + r * Math.sin(subAngle),
        }
      }
    })
  })

  // 轻量力导向迭代：让相连节点靠近，重叠节点分离
  const linkPairs = links
    .map((l) => ({ source: l.source, target: l.target }))
    .filter((l) => placed[l.source] && placed[l.target])

  for (let iter = 0; iter < 50; iter++) {
    // 重力（向中心拉近）
    nodes.forEach((n) => {
      const p = placed[n.id]
      if (!p) return
      p.x += (cx - p.x) * 0.005
      p.y += (cy - p.y) * 0.005
    })

    // 连接吸引
    linkPairs.forEach(({ source, target }) => {
      const a = placed[source]
      const b = placed[target]
      const dx = b.x - a.x
      const dy = b.y - a.y
      const dist = Math.sqrt(dx * dx + dy * dy) || 1
      const ideal = 120
      const force = (dist - ideal) * 0.02
      const fx = (dx / dist) * force
      const fy = (dy / dist) * force
      a.x += fx
      a.y += fy
      b.x -= fx
      b.y -= fy
    })

    // 节点排斥
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = placed[nodes[i].id]
        const b = placed[nodes[j].id]
        if (!a || !b) continue
        const dx = b.x - a.x
        const dy = b.y - a.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01
        if (dist < 80) {
          const force = (80 - dist) * 0.05
          const fx = (dx / dist) * force
          const fy = (dy / dist) * force
          a.x -= fx
          a.y -= fy
          b.x += fx
          b.y += fy
        }
      }
    }
  }

  return nodes.map((n) => {
    const p = placed[n.id]
    return {
      id: n.id,
      type: 'default',
      position: { x: p?.x ?? cx, y: p?.y ?? cy },
      data: { label: n.id, nodeType: n.type },
      style: {
        background: nodeTypeColor[n.type] || '#999',
        color: '#fff',
        border: `2px solid ${nodeTypeColor[n.type] || '#999'}`,
        fontSize: '11px',
        padding: '4px 8px',
        borderRadius: '14px',
        width: 'auto',
      },
    }
  })
}

function buildEdges(links: GraphLink[]): Edge[] {
  return links.map((l, idx) => ({
    id: `e-${idx}-${l.source}-${l.target}`,
    source: l.source,
    target: l.target,
    label: l.type,
    animated: l.type === 'RELATED_TO' || l.type === 'DEPENDS_ON',
    style: {
      stroke: relationColor[l.type] || '#999',
      strokeWidth: 1.5,
    },
    labelStyle: { fontSize: 10, fill: '#666' },
    labelBgStyle: { fill: '#fff' },
  }))
}

function applyGraph() {
  vfNodes.value = layoutNodes(graphData.value.nodes, graphData.value.links)
  vfEdges.value = buildEdges(graphData.value.links)
  // 自动适配视图
  setTimeout(() => fitView({ padding: 0.2 }), 100)
}

async function loadGraph() {
  loading.value = true
  neo4jError.value = ''
  try {
    const res = await getGraphVisualize(entityTypeFilter.value || undefined, limitFilter.value)
    if (res.error) {
      neo4jError.value = res.error + (res.hint ? `（${res.hint}）` : '')
      graphData.value = { nodes: [], links: [] }
    } else {
      graphData.value = { nodes: res.nodes || [], links: res.links || [] }
    }
    applyGraph()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '加载图谱失败')
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try {
    stats.value = await getGraphStats()
  } catch (err: any) {
    console.warn('stats load failed', err)
  }
}

async function doSearch() {
  const q = searchKeyword.value.trim()
  if (!q) {
    searchResults.value = []
    return
  }
  try {
    const res = await searchGraph(q, 20)
    searchResults.value = res.results || []
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '搜索失败')
  }
}

onNodeClick((e) => {
  const nodeName = e.node.id
  openEntityDetail(nodeName)
})

async function openEntityDetail(name: string) {
  detailVisible.value = true
  detailLoading.value = true
  selectedEntity.value = null
  selectedNodeName.value = name
  try {
    const res = await getGraphEntity(name)
    selectedEntity.value = res
  } catch (err: any) {
    if (err?.response?.status === 404) {
      message.warning(`未找到实体: ${name}`)
    } else {
      message.error(err?.response?.data?.detail || err?.message || '加载详情失败')
    }
  } finally {
    detailLoading.value = false
  }
}

watch(entityTypeFilter, () => loadGraph())

const statCards = computed(() => [
  { label: '实体总数', value: stats.value?.total_entities ?? 0, color: '#2080f0' },
  { label: '关系总数', value: stats.value?.total_relations ?? 0, color: '#f0a020' },
  { label: '当前节点', value: graphData.value.nodes.length, color: '#18a058' },
  { label: '当前边', value: graphData.value.links.length, color: '#d03050' },
])

onMounted(() => {
  loadStats()
  loadGraph()
})
</script>

<template>
  <div class="graph-view">
    <div class="page-header">
      <h2 class="page-title">知识图谱</h2>
      <p class="page-desc">
        基于 Neo4j 的实体-关系图（Host/Service/Concept 等 11 类实体），支持类型筛选、搜索、节点下钻
      </p>
    </div>

    <n-grid :cols="4" :x-gap="12" :y-gap="12" class="stats-grid">
      <n-gi v-for="card in statCards" :key="card.label">
        <n-card size="small">
          <n-statistic :label="card.label" :value="card.value">
            <template #prefix>
              <span :style="{ color: card.color }">●</span>
            </template>
          </n-statistic>
        </n-card>
      </n-gi>
    </n-grid>

    <n-card :bordered="true">
      <template #header>
        <n-space align="center" :size="12" wrap>
          <span>图谱可视化</span>
          <n-select
            v-model:value="entityTypeFilter"
            :options="entityTypeOptions"
            size="small"
            style="width: 200px;"
            placeholder="实体类型"
          />
          <n-button quaternary size="small" :loading="loading" @click="loadGraph">
            刷新
          </n-button>
          <n-button size="small" @click="() => fitView({ padding: 0.2 })">
            适配视图
          </n-button>
        </n-space>
      </template>

      <div class="search-bar">
        <n-input
          v-model:value="searchKeyword"
          size="small"
          placeholder="搜索实体名（按回车）"
          style="width: 280px;"
          clearable
          @keyup.enter="doSearch"
        />
        <n-button size="small" type="primary" @click="doSearch">搜索</n-button>
        <n-space v-if="searchResults.length" :size="4">
          <n-tag
            v-for="r in searchResults"
            :key="r.name"
            size="small"
            checkable
            :style="{ color: nodeTypeColor[r.type] || '#999', cursor: 'pointer' }"
            @click="openEntityDetail(r.name)"
          >
            {{ r.name }} · {{ nodeTypeLabel[r.type] || r.type }}
          </n-tag>
        </n-space>
      </div>

      <n-alert v-if="neo4jError" type="warning" style="margin: 8px 0;">
        Neo4j 不可用：{{ neo4jError }}
      </n-alert>

      <div v-if="loading" class="loading-container">
        <n-spin size="large" />
      </div>

      <n-empty
        v-else-if="!graphData.nodes.length && !neo4jError"
        description="暂无图谱数据"
        style="padding: 80px 0;"
      />

      <div v-else-if="graphData.nodes.length" class="flow-container">
        <VueFlow :nodes="vfNodes" :edges="vfEdges" :min-zoom="0.1" :max-zoom="3">
          <Background pattern-color="#e5e5e5" :gap="20" />
          <Controls />
          <MiniMap
            :node-color="(n: any) => nodeTypeColor[n.data?.nodeType] || '#999'"
            pannable
            zoomable
          />
        </VueFlow>

        <!-- 图例 -->
        <div class="legend">
          <div class="legend-title">节点类型</div>
          <div class="legend-grid">
            <div
              v-for="t in Object.keys(nodeTypeLabel)"
              :key="t"
              class="legend-item"
            >
              <span class="legend-dot" :style="{ background: nodeTypeColor[t] }"></span>
              <span>{{ nodeTypeLabel[t] }} ({{ t }})</span>
            </div>
          </div>
          <div class="legend-divider"></div>
          <div class="legend-title">关系类型</div>
          <div class="legend-grid">
            <div
              v-for="r in Object.keys(relationColor)"
              :key="r"
              class="legend-item"
            >
              <span class="legend-line" :style="{ background: relationColor[r] }"></span>
              <span>{{ r }}</span>
            </div>
          </div>
        </div>
      </div>
    </n-card>

    <!-- 实体详情抽屉 -->
    <n-drawer v-model:show="detailVisible" :width="640" placement="right">
      <n-drawer-content :title="`实体详情: ${selectedNodeName}`" closable>
        <div v-if="detailLoading" class="loading-container">
          <n-spin size="large" />
        </div>
        <template v-else-if="selectedEntity">
          <n-descriptions :column="1" bordered label-placement="left" size="small">
            <n-descriptions-item label="名称">
              <code>{{ selectedNodeName }}</code>
            </n-descriptions-item>
            <n-descriptions-item label="类型">
              <n-tag
                size="small"
                :style="{ color: nodeTypeColor[selectedEntity.entity?.entity_type] || '#999', background: 'transparent' }"
                :bordered="false"
              >
                {{ selectedEntity.entity?.entity_type || '-' }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item v-if="selectedEntity.entity?.confidence != null" label="置信度">
              {{ (selectedEntity.entity.confidence * 100).toFixed(1) }}%
            </n-descriptions-item>
            <n-descriptions-item v-if="selectedEntity.entity?.source_doc_id" label="来源文档">
              <code>{{ selectedEntity.entity.source_doc_id }}</code>
            </n-descriptions-item>
            <n-descriptions-item v-if="selectedEntity.entity?.properties" label="属性">
              <pre style="font-size: 12px; margin: 0; white-space: pre-wrap;">{{ JSON.stringify(selectedEntity.entity.properties, null, 2) }}</pre>
            </n-descriptions-item>
          </n-descriptions>

          <h4 style="margin-top: 20px; margin-bottom: 8px;">
            相关节点 ({{ selectedEntity.related?.length || 0 }})
          </h4>
          <n-empty v-if="!selectedEntity.related?.length" description="无相关节点" />
          <n-space v-else vertical :size="6">
            <n-card
              v-for="(rel, idx) in selectedEntity.related"
              :key="idx"
              size="small"
              :bordered="true"
            >
              <n-space align="center" :size="8" wrap>
                <code style="font-size: 12px;">{{ rel.source }}</code>
                <n-tag size="small" :bordered="false" :style="{ color: relationColor[rel.relation] || '#999', background: 'transparent' }">
                  → {{ rel.relation }} →
                </n-tag>
                <n-button
                  size="tiny"
                  quaternary
                  type="info"
                  @click="openEntityDetail(rel.target)"
                >
                  {{ rel.target }}
                </n-button>
                <n-tag
                  size="small"
                  :bordered="false"
                  :style="{ color: nodeTypeColor[rel.target_type] || '#999', background: 'transparent' }"
                >
                  {{ nodeTypeLabel[rel.target_type] || rel.target_type || '-' }}
                </n-tag>
                <n-tag v-if="rel.confidence != null" size="small" type="info">
                  {{ (rel.confidence * 100).toFixed(0) }}%
                </n-tag>
              </n-space>
            </n-card>
          </n-space>
        </template>
      </n-drawer-content>
    </n-drawer>
  </div>
</template>

<style scoped>
.graph-view {
  max-width: 1600px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 20px;
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

.stats-grid {
  margin-bottom: 16px;
}

.search-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  gap: 16px;
}

.flow-container {
  position: relative;
  height: 600px;
  border: 1px solid var(--n-border-color, #e5e5e5);
  border-radius: 6px;
  overflow: hidden;
}

.legend {
  position: absolute;
  top: 12px;
  right: 12px;
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid var(--n-border-color, #e5e5e5);
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 11px;
  max-width: 280px;
  max-height: 580px;
  overflow-y: auto;
  z-index: 5;
}

.legend-title {
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--n-text-color, #333);
}

.legend-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 10px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 5px;
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.legend-line {
  width: 14px;
  height: 2px;
}

.legend-divider {
  height: 1px;
  background: var(--n-border-color, #e5e5e5);
  margin: 8px 0;
}
</style>
