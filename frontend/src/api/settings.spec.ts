import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/index', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import api from '@/api/index'
import {
  getSettings,
  updateSettings,
  validateSettings,
  restartService,
  testLLMConnection,
} from './settings'

describe('api/settings.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getSettings GET /settings', async () => {
    const data = { groups: {} }
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(data)
    await getSettings()
    expect(api.get).toHaveBeenCalledWith('/settings')
  })

  it('updateSettings PUT /settings 携带 updates', async () => {
    ;(api.put as ReturnType<typeof vi.fn>).mockResolvedValue({ updated: [], message: 'ok' })
    await updateSettings({ updates: { log_level: 'DEBUG' } })
    expect(api.put).toHaveBeenCalledWith('/settings', { updates: { log_level: 'DEBUG' } })
  })

  it('validateSettings POST /settings/validate', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ valid: true, errors: [] })
    await validateSettings({ updates: {} })
    expect(api.post).toHaveBeenCalledWith('/settings/validate', { updates: {} })
  })

  it('restartService POST /settings/restart', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ restart: true, message: 'ok' })
    await restartService()
    expect(api.post).toHaveBeenCalledWith('/settings/restart')
  })

  it('testLLMConnection POST /settings/llm/test 提空 body 时传入 {}', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await testLLMConnection()
    expect(api.post).toHaveBeenCalledWith('/settings/llm/test', {})
  })

  it('testLLMConnection 传递请求体', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await testLLMConnection({ backend: 'ollama', model: 'qwen' })
    expect(api.post).toHaveBeenCalledWith('/settings/llm/test', {
      backend: 'ollama',
      model: 'qwen',
    })
  })
})