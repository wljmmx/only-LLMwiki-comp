import api from './index'

// ────────── Runbook ──────────

export interface RunbookSource {
  doc_id: string
  title: string
  score?: number
  snippet?: string
}

export interface RunbookGenerateResult {
  runbook_md: string
  sources: RunbookSource[]
  wiki_slug?: string
  wiki_published?: boolean
  incident_id?: string
  [key: string]: any
}

export function generateRunbook(payload: {
  symptom: string
  service?: string
  host?: string
  max_docs?: number
  publish?: boolean
  wiki_slug?: string
}) {
  return api.post<unknown, RunbookGenerateResult>('/runbook/generate', payload)
}

export function previewRunbook(symptom: string, service = '', host = '', maxDocs = 5) {
  return api.get<unknown, RunbookGenerateResult>('/runbook/preview', {
    params: { symptom, service, host, max_docs: maxDocs },
  })
}

// ────────── Events / Incidents ──────────

export interface AIOpsEvent {
  id?: string
  timestamp?: string
  host?: string
  service?: string
  component?: string
  severity: string
  message: string
  tags?: string[]
  source?: string
  attributes?: Record<string, any>
}

export interface Incident {
  incident_id: string
  severity: string
  status: string
  alert_count: number
  suspected_root_cause?: string
  runbook_hint?: string
  first_seen?: string
  last_seen?: string
  hosts?: string[]
  services?: string[]
  components?: string[]
  event_samples?: AIOpsEvent[]
  acknowledged_at?: string | null
  resolved_at?: string | null
  ended_at?: string | null
  assignee?: string
  transition_history?: IncidentTransition[]
  scope?: { hosts?: string[]; services?: string[]; components?: string[] }
  alerts?: AIOpsEvent[]
  [key: string]: any
}

// P2-2.2 incident 状态机
export type IncidentState =
  | 'open'
  | 'ack'
  | 'investigating'
  | 'mitigated'
  | 'resolved'
  | 'closed' // legacy alias for resolved
  | 'all'

export interface IncidentTransition {
  from: string
  to: string
  at: string
  by: string
  note: string
}

export interface IncidentStateMachine {
  states: string[]
  transitions: Record<string, string[]>
  terminal_states: string[]
  legacy_aliases: Record<string, string>
}

export interface TransitionResult {
  incident_id: string
  status: string
  transition_history: IncidentTransition[]
}

export interface IngestResult {
  ingested: number
  incident_detections?: any[]
  [key: string]: any
}

export function ingestEvents(events: AIOpsEvent[]) {
  return api.post<unknown, IngestResult>('/events/ingest', { events })
}

export function correlateEvents(sinceMinutes = 60, maxEvents = 500) {
  return api.post<unknown, any>('/events/correlate', {
    since_minutes: sinceMinutes,
    max_events: maxEvents,
  })
}

export function listIncidents(status: IncidentState | string = 'open', limit = 50) {
  return api.get<unknown, { incidents: Incident[]; count: number }>('/events/incidents', {
    params: { status, limit },
  })
}

export function getIncident(incidentId: string) {
  return api.get<unknown, Incident>(`/events/incidents/${incidentId}`)
}

export function getIncidentStates() {
  return api.get<unknown, IncidentStateMachine>('/events/incidents/states')
}

/**
 * 通用状态迁移端点（P2-2.2）
 * 推荐使用具体状态端点 ackIncident/investigateIncident/mitigateIncident/resolveIncident
 */
export function transitionIncident(
  incidentId: string,
  targetState: IncidentState | string,
  options: { note?: string; by?: string; assignee?: string } = {},
) {
  return api.post<unknown, TransitionResult>(`/events/incidents/${incidentId}/transition`, {
    target_state: targetState,
    note: options.note ?? '',
    by: options.by ?? '',
    assignee: options.assignee,
  })
}

export function ackIncident(
  incidentId: string,
  options: { note?: string; by?: string; assignee?: string } = {},
) {
  return api.post<unknown, TransitionResult>(`/events/incidents/${incidentId}/ack`, options)
}

export function investigateIncident(
  incidentId: string,
  options: { note?: string; by?: string; assignee?: string } = {},
) {
  return api.post<unknown, TransitionResult>(`/events/incidents/${incidentId}/investigate`, options)
}

export function mitigateIncident(
  incidentId: string,
  options: { note?: string; by?: string; assignee?: string } = {},
) {
  return api.post<unknown, TransitionResult>(`/events/incidents/${incidentId}/mitigate`, options)
}

export function resolveIncident(
  incidentId: string,
  options: { note?: string; by?: string; assignee?: string } = {},
) {
  return api.post<unknown, TransitionResult>(`/events/incidents/${incidentId}/resolve`, options)
}

/** Legacy close 端点（等价于 resolve） */
export function closeIncident(incidentId: string, note = '') {
  return api.post<unknown, any>(`/events/incidents/${incidentId}/close`, null, { params: { note } })
}

export function incidentToRunbook(incidentId: string, publish = false) {
  return api.post<unknown, RunbookGenerateResult>(`/events/incidents/${incidentId}/runbook`, null, {
    params: { publish },
  })
}

export function getIncidentChanges(incidentId: string) {
  return api.get<unknown, { incident_id: string; changes: any[]; count: number }>(
    `/events/incidents/${incidentId}/changes`,
  )
}

export function getIncidentRollbackSuggestion(incidentId: string) {
  return api.get<unknown, any>(`/events/incidents/${incidentId}/rollback-suggestion`)
}

// ────────── Changes ──────────

export interface Change {
  id?: string
  change_type: string
  timestamp?: string
  host?: string
  service?: string
  component?: string
  severity?: string
  author?: string
  ticket_id?: string
  description?: string
  attributes?: Record<string, any>
  status?: string
  rollback_of?: string
  [key: string]: any
}

export function ingestChanges(changes: Change[]) {
  return api.post<unknown, IngestResult>('/changes/ingest', { changes })
}

export function correlateChanges(sinceHours = 24, timeWindowMinutes?: number) {
  return api.post<unknown, any>('/changes/correlate', {
    since_hours: sinceHours,
    time_window_minutes: timeWindowMinutes,
  })
}

export function listChanges(service = '', limit = 50) {
  return api.get<unknown, { changes: Change[]; count: number }>('/changes', {
    params: { service, limit },
  })
}

export function getChange(changeId: string) {
  return api.get<unknown, Change>(`/changes/${changeId}`)
}

// ────────── Topology ──────────

export interface TopologyNode {
  name: string
  type: 'Host' | 'Service' | 'Component' | string
  attributes?: Record<string, any>
}

export interface TopologyEdge {
  source: string
  target: string
  relation: 'RUNS_ON' | 'DEPENDS_ON' | 'USES' | string
  attributes?: Record<string, any>
}

export interface TopologyData {
  nodes: TopologyNode[]
  edges: TopologyEdge[]
  [key: string]: any
}

export function rebuildTopology(maxDocs = 100) {
  return api.post<unknown, any>('/topology/rebuild', null, {
    params: { max_docs: maxDocs },
  })
}

export function getTopology(nodeType?: string, relation?: string): Promise<TopologyData> {
  return api.get<unknown, TopologyData>('/topology', {
    params: { node_type: nodeType, relation },
  })
}

export function getNodeNeighbors(nodeName: string, depth = 1) {
  return api.get<unknown, any>(`/topology/nodes/${encodeURIComponent(nodeName)}`, {
    params: { depth },
  })
}

export function getImpactAnalysis(nodeName: string) {
  return api.get<unknown, any>(`/topology/impact/${encodeURIComponent(nodeName)}`)
}
