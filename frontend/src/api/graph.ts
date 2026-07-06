import api from './index'

// ────────── Knowledge Graph（F14 知识图谱可视化） ──────────

export interface GraphNode {
  id: string // = Entity.name
  type: string // Host / Service / Component / Parameter / Command / Procedure / Incident / Symptom / Experience / Concept / Document
  group: number // D3 颜色分组 1-11
}

export interface GraphLink {
  source: string
  target: string
  type: string // RUNS_ON / USES / DEPENDS_ON / HAS_PARAMETER / CONFIGURED_BY / DESCRIBED_IN / INVOLVES / MANIFESTS_AS / RESOLVED_BY / DERIVED_FROM / RELATED_TO
  confidence?: number | null
}

export interface GraphData {
  nodes: GraphNode[]
  links: GraphLink[]
  node_count: number
  link_count: number
  error?: string
  hint?: string
}

export interface GraphStats {
  total_entities: number
  total_relations: number
  by_type: { type: string; count: number }[]
  error?: string
  hint?: string
}

export interface GraphSearchResult {
  name: string
  type: string
  confidence?: number
}

export interface GraphRelatedItem {
  source: string
  relation: string
  target: string
  target_type: string
  confidence?: number | null
}

export interface GraphEntityDetail {
  entity: Record<string, any>
  related: GraphRelatedItem[]
}

/**
 * 获取知识图谱可视化数据（D3.js force-directed 格式）
 * GET /graph/visualize?entity_type=&limit=
 * 注意：Neo4j 未连接时返回 {nodes:[], links:[], error, hint}
 */
export function getGraphVisualize(entityType?: string, limit = 200) {
  return api.get<any, GraphData>('/graph/visualize', {
    params: { entity_type: entityType || undefined, limit },
  })
}

/**
 * 图谱统计
 * GET /graph/stats
 */
export function getGraphStats() {
  return api.get<any, GraphStats>('/graph/stats')
}

/**
 * 搜索实体
 * GET /graph/search?q=&limit=
 */
export function searchGraph(q: string, limit = 20) {
  return api.get<any, { query: string; results: GraphSearchResult[]; count: number }>(
    '/graph/search',
    { params: { q, limit } },
  )
}

/**
 * 获取实体详情（含 1 跳邻居）
 * GET /graph/entity/{name}
 */
export function getGraphEntity(name: string) {
  return api.get<any, GraphEntityDetail>(
    `/graph/entity/${encodeURIComponent(name)}`,
  )
}

/**
 * 按类型列出实体
 * GET /graph/by-type/{entity_type}?limit=
 */
export function getGraphByType(entityType: string, limit = 50) {
  return api.get<any, { entity_type: string; results: GraphSearchResult[]; count: number }>(
    `/graph/by-type/${encodeURIComponent(entityType)}`,
    { params: { limit } },
  )
}
