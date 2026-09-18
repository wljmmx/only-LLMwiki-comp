import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/index', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import api from '@/api/index'
import {
  generateRunbook,
  previewRunbook,
  ingestEvents,
  correlateEvents,
  listIncidents,
  getIncident,
  getIncidentStates,
  transitionIncident,
  ackIncident,
  investigateIncident,
  mitigateIncident,
  resolveIncident,
  closeIncident,
  incidentToRunbook,
  getIncidentChanges,
  getIncidentRollbackSuggestion,
  ingestChanges,
  correlateChanges,
  listChanges,
  getChange,
  rebuildTopology,
  getTopology,
  getNodeNeighbors,
  getImpactAnalysis,
} from './aiops'

describe('api/aiops.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('Runbook', () => {
    it('generateRunbook POST /runbook/generate 携带 payload', async () => {
      const data = { runbook_md: '# r', sources: [], wiki_slug: 's' }
      ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue(data)
      const payload = { symptom: '502', service: 'nginx', publish: true }
      await expect(generateRunbook(payload)).resolves.toEqual(data)
      expect(api.post).toHaveBeenCalledWith('/runbook/generate', payload)
    })

    it('previewRunbook GET /runbook/preview 携带默认 max_docs=5 与参数', async () => {
      const data = { runbook_md: '# p', sources: [] }
      ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(data)
      await previewRunbook('502', 'nginx', 'h1')
      expect(api.get).toHaveBeenCalledWith('/runbook/preview', {
        params: { symptom: '502', service: 'nginx', host: 'h1', max_docs: 5 },
      })
    })
  })

  describe('Events / Incidents', () => {
    it('ingestEvents POST /events/ingest', async () => {
      const data = { ingested: 1 }
      ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue(data)
      const events = [{ severity: 'critical', message: 'down' }]
      await ingestEvents(events)
      expect(api.post).toHaveBeenCalledWith('/events/ingest', { events })
    })

    it('correlateEvents POST /events/correlate 映射参数名', async () => {
      ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
      await correlateEvents(120, 999)
      expect(api.post).toHaveBeenCalledWith('/events/correlate', {
        since_minutes: 120,
        max_events: 999,
      })
    })

    it('listIncidents GET /events/incidents 携带 status/limit', async () => {
      const data = { incidents: [], count: 0 }
      ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(data)
      await listIncidents('open', 25)
      expect(api.get).toHaveBeenCalledWith('/events/incidents', {
        params: { status: 'open', limit: 25 },
      })
    })

    it('getIncident / getIncidentStates / getIncidentChanges / getIncidentRollbackSuggestion', async () => {
      const inc = { incident_id: 'i1' }
      ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(inc)
      await getIncident('i1')
      expect(api.get).toHaveBeenCalledWith('/events/incidents/i1')
      await getIncidentStates()
      expect(api.get).toHaveBeenCalledWith('/events/incidents/states')
      await getIncidentChanges('i1')
      expect(api.get).toHaveBeenCalledWith('/events/incidents/i1/changes')
      await getIncidentRollbackSuggestion('i1')
      expect(api.get).toHaveBeenCalledWith('/events/incidents/i1/rollback-suggestion')
    })

    it('transitionIncident POST 携带 target_state/note/by/assignee', async () => {
      const data = { incident_id: 'i1', status: 'resolved', transition_history: [] }
      ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue(data)
      await transitionIncident('i1', 'resolved', { note: 'n', by: 'u', assignee: 'a' })
      expect(api.post).toHaveBeenCalledWith('/events/incidents/i1/transition', {
        target_state: 'resolved',
        note: 'n',
        by: 'u',
        assignee: 'a',
      })
    })

    it('状态机便捷端点 ack/investigate/mitigate/resolve 命中各自路径', async () => {
      const opts = { note: 'n', by: 'u', assignee: 'a' }
      const data = { incident_id: 'i1', status: 'x', transition_history: [] }
      ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue(data)
      await ackIncident('i1', opts)
      expect(api.post).toHaveBeenCalledWith('/events/incidents/i1/ack', opts)
      await investigateIncident('i1', opts)
      expect(api.post).toHaveBeenCalledWith('/events/incidents/i1/investigate', opts)
      await mitigateIncident('i1', opts)
      expect(api.post).toHaveBeenCalledWith('/events/incidents/i1/mitigate', opts)
      await resolveIncident('i1', opts)
      expect(api.post).toHaveBeenCalledWith('/events/incidents/i1/resolve', opts)
    })

    it('closeIncident POST params.note；incidentToRunbook POST params.publish', async () => {
      ;(api.post as ReturnType<typeof vi.fn>).mockImplementation(async () => ({}))
      await closeIncident('i1', 'closed it')
      expect(api.post).toHaveBeenCalledWith('/events/incidents/i1/close', null, {
        params: { note: 'closed it' },
      })
      await incidentToRunbook('i1', true)
      expect(api.post).toHaveBeenCalledWith('/events/incidents/i1/runbook', null, {
        params: { publish: true },
      })
    })
  })

  describe('Changes', () => {
    it('ingestChanges POST /changes/ingest', async () => {
      ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ingested: 2 })
      await ingestChanges([{ change_type: 'deploy' }])
      expect(api.post).toHaveBeenCalledWith('/changes/ingest', {
        changes: [{ change_type: 'deploy' }],
      })
    })

    it('correlateChanges POST 携带 since_hours / time_window_minutes', async () => {
      ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
      await correlateChanges(48, 30)
      expect(api.post).toHaveBeenCalledWith('/changes/correlate', {
        since_hours: 48,
        time_window_minutes: 30,
      })
    })

    it('listChanges / getChange 命中路径', async () => {
      ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({})
      await listChanges('nginx', 20)
      expect(api.get).toHaveBeenCalledWith('/changes', {
        params: { service: 'nginx', limit: 20 },
      })
      await getChange('c1')
      expect(api.get).toHaveBeenCalledWith('/changes/c1')
    })
  })

  describe('Topology', () => {
    it('rebuildTopology POST params.max_docs', async () => {
      ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
      await rebuildTopology(50)
      expect(api.post).toHaveBeenCalledWith('/topology/rebuild', null, {
        params: { max_docs: 50 },
      })
    })

    it('getTopology GET 携带 node_type/relation', async () => {
      ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ nodes: [], edges: [] })
      await getTopology('Host', 'RUNS_ON')
      expect(api.get).toHaveBeenCalledWith('/topology', {
        params: { node_type: 'Host', relation: 'RUNS_ON' },
      })
    })

    it('getNodeNeighbors / getImpactAnalysis 编码 node 名并命中路径', async () => {
      ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({})
      await getNodeNeighbors('web prod', 2)
      expect(api.get).toHaveBeenCalledWith('/topology/nodes/web%20prod', { params: { depth: 2 } })
      await getImpactAnalysis('web prod')
      expect(api.get).toHaveBeenCalledWith('/topology/impact/web%20prod')
    })
  })
})