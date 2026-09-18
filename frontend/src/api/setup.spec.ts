import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/index', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import api from '@/api/index'
import {
  getSetupStatus,
  testLLM,
  testNeo4j,
  generateCommand,
} from './setup'

describe('api/setup.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getSetupStatus GET /setup/status（无需认证）', async () => {
    const data = { ready: true, missing: [] }
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(data)
    await getSetupStatus()
    expect(api.get).toHaveBeenCalledWith('/setup/status')
  })

  it('testLLM POST /setup/test-llm 携带请求体', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, backend: 'ollama' })
    await testLLM({ backend: 'ollama', model: 'qwen' })
    expect(api.post).toHaveBeenCalledWith('/setup/test-llm', {
      backend: 'ollama',
      model: 'qwen',
    })
  })

  it('testNeo4j POST /setup/test-neo4j 携带请求体', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, uri: 'bolt://x' })
    await testNeo4j({ uri: 'bolt://x', user: 'neo4j' })
    expect(api.post).toHaveBeenCalledWith('/setup/test-neo4j', {
      uri: 'bolt://x',
      user: 'neo4j',
    })
  })

  it('generateCommand POST /setup/generate-command 传入 mode', async () => {
    const data = { command: 'docker run ...', env_file_content: 'A=1' }
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue(data)
    await generateCommand({ mode: 'docker-compose' } as any)
    expect(api.post).toHaveBeenCalledWith('/setup/generate-command', {
      mode: 'docker-compose',
    })
  })
})