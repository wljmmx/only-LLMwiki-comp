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
  return api.get<unknown, GraphData>('/graph/visualize', {
    params: { entity_type: entityType || undefined, limit },
  })
}

/**
 * 图谱统计
 * GET /graph/stats
 */
export function getGraphStats() {
  return api.get<unknown, GraphStats>('/graph/stats')
}

/**
 * 搜索实体
 * GET /graph/search?q=&limit=
 */
export function searchGraph(q: string, limit = 20) {
  return api.get<unknown, { query: string; results: GraphSearchResult[]; count: number }>(
    '/graph/search',
    { params: { q, limit } },
  )
}

/**
 * 获取实体详情（含 1 跳邻居）
 * GET /graph/entity/{name}
 */
export function getGraphEntity(name: string) {
  return api.get<unknown, GraphEntityDetail>(`/graph/entity/${encodeURIComponent(name)}`)
}

/**
 * KNOW-14: 获取实体关联的 wiki 页面列表
 * GET /graph/entity/{name}/wiki-pages
 */
export function getGraphEntityWikiPages(name: string) {
  return api.get<unknown, {
    entity_name: string
    wiki_pages: Array<{ slug: string; title: string; match_type: string }>
  }>(`/graph/entity/${encodeURIComponent(name)}/wiki-pages`)
}

/**
 * 按类型列出实体
 * GET /graph/by-type/{entity_type}?limit=
 */
export function getGraphByType(entityType: string, limit = 50) {
  return api.get<unknown, { entity_type: string; results: GraphSearchResult[]; count: number }>(
    `/graph/by-type/${encodeURIComponent(entityType)}`,
    { params: { limit } },
  )
}

/**
 * 删除图谱实体
 * DELETE /graph/entity/{name}
 */
export function deleteGraphEntity(name: string) {
  return api.delete<unknown, { deleted: boolean; name: string; nodes_removed: number }>(
    `/graph/entity/${encodeURIComponent(name)}`,
  )
}

/**
 * 清空所有图谱数据
 * DELETE /graph/clear
 */
export function clearGraph() {
  return api.delete<unknown, { nodes_removed: number; relations_removed: number }>('/graph/clear')
}

// ────────── KNOW-16: 图谱路径分析 ──────────

export interface ShortestPathResult {
  found: boolean
  path: Array<{ name: string; type: string }>
  length: number
  depth_searched: number
  error?: string
  hint?: string
}

export interface ImpactPropagationResult {
  entity: string
  affected_count: number
  affected_entities: Array<{ name: string; type: string; distance: number }>
  error?: string
  hint?: string
}

/**
 * KNOW-16: 查找两个实体之间的最短路径
 * GET /graph/shortest-path?from=...&to=...&max_depth=5
 */
export function getShortestPath(from: string, to: string, maxDepth = 5) {
  return api.get<unknown, ShortestPathResult>('/graph/shortest-path', {
    params: { from_entity: from, to_entity: to, max_depth: maxDepth },
  })
}

/**
 * KNOW-16: 影响传播分析
 * GET /graph/impact-propagation?entity=...&depth=2
 */
export function getImpactPropagation(entity: string, depth = 2) {
  return api.get<unknown, ImpactPropagationResult>('/graph/impact-propagation', {
    params: { entity, depth },
  })
}

// ────────── KNOW-17: backlink 关系图 ──────────

export interface BacklinkGraphResult {
  entity_name: string
  backlink_count: number
  backlinks: Array<{ slug: string; title: string; count: number }>
}

/**
 * KNOW-17: 获取实体的 backlink 关系图数据
 * GET /graph/entity/{name}/backlinks
 */
export function getEntityBacklinks(name: string) {
  return api.get<unknown, BacklinkGraphResult>(`/graph/entity/${encodeURIComponent(name)}/backlinks`)
}

// ────────── KNOW-18: 实体时间演变回放 ──────────

export interface EntityHistoryResult {
  entity_name: string
  entity_type: string
  history: Array<{
    action: string
    source_doc_id: string
    timestamp: string
    confidence: number
  }>
  note: string
  error?: string
  hint?: string
}

/**
 * KNOW-18: 获取实体变更历史
 * GET /graph/entity/{name}/history?limit=50
 */
export function getEntityHistory(name: string, limit = 50) {
  return api.get<unknown, EntityHistoryResult>(`/graph/entity/${encodeURIComponent(name)}/history`, {
    params: { limit },
  })
}
