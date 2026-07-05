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
  return api.post<any, RunbookGenerateResult>('/runbook/generate', payload)
}

export function previewRunbook(
  symptom: string,
  service = '',
  host = '',
  maxDocs = 5,
) {
  return api.get<any, RunbookGenerateResult>('/runbook/preview', {
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
  [key: string]: any
}

export interface IngestResult {
  ingested: number
  incident_detections?: any[]
  [key: string]: any
}

export function ingestEvents(events: AIOpsEvent[]) {
  return api.post<any, IngestResult>('/events/ingest', { events })
}

export function correlateEvents(sinceMinutes = 60, maxEvents = 500) {
  return api.post<any, any>('/events/correlate', {
    since_minutes: sinceMinutes,
    max_events: maxEvents,
  })
}

export function listIncidents(status = 'open', limit = 50) {
  return api.get<any, { incidents: Incident[]; count: number }>(
    '/events/incidents',
    { params: { status, limit } },
  )
}

export function getIncident(incidentId: string) {
  return api.get<any, Incident>(`/events/incidents/${incidentId}`)
}

export function closeIncident(incidentId: string, note = '') {
  return api.post<any, any>(
    `/events/incidents/${incidentId}/close`,
    null,
    { params: { note } },
  )
}

export function incidentToRunbook(incidentId: string, publish = false) {
  return api.post<any, RunbookGenerateResult>(
    `/events/incidents/${incidentId}/runbook`,
    null,
    { params: { publish } },
  )
}

export function getIncidentChanges(incidentId: string) {
  return api.get<any, { incident_id: string; changes: any[]; count: number }>(
    `/events/incidents/${incidentId}/changes`,
  )
}

export function getIncidentRollbackSuggestion(incidentId: string) {
  return api.get<any, any>(`/events/incidents/${incidentId}/rollback-suggestion`)
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
  return api.post<any, IngestResult>('/changes/ingest', { changes })
}

export function correlateChanges(sinceHours = 24, timeWindowMinutes?: number) {
  return api.post<any, any>('/changes/correlate', {
    since_hours: sinceHours,
    time_window_minutes: timeWindowMinutes,
  })
}

export function listChanges(service = '', limit = 50) {
  return api.get<any, { changes: Change[]; count: number }>('/changes', {
    params: { service, limit },
  })
}

export function getChange(changeId: string) {
  return api.get<any, Change>(`/changes/${changeId}`)
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
  return api.post<any, any>('/topology/rebuild', null, {
    params: { max_docs: maxDocs },
  })
}

export function getTopology(
  nodeType?: string,
  relation?: string,
): Promise<TopologyData> {
  return api.get<any, TopologyData>('/topology', {
    params: { node_type: nodeType, relation },
  })
}

export function getNodeNeighbors(nodeName: string, depth = 1) {
  return api.get<any, any>(`/topology/nodes/${encodeURIComponent(nodeName)}`, {
    params: { depth },
  })
}

export function getImpactAnalysis(nodeName: string) {
  return api.get<any, any>(`/topology/impact/${encodeURIComponent(nodeName)}`)
}
