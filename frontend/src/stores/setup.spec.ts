/**
 * Setup Wizard store 单元测试
 *
 * 覆盖：
 * - 初始状态
 * - refreshStatus / testLLM / testNeo4j / generateCommand 调用 api 并写状态
 * - dismiss / undismiss 持久化
 * - resetForm 重置表单
 * - activeLLMBaseUrl / activeLLMModel 按 backend 切换
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// mock @/api/setup
vi.mock('@/api/setup', () => ({
  getSetupStatus: vi.fn(),
  testLLM: vi.fn(),
  testNeo4j: vi.fn(),
  generateCommand: vi.fn(),
}))

import * as setupApi from '@/api/setup'
import { useSetupStore } from './setup'

describe('stores/setup.ts', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  // ────────── 初始状态 ──────────

  it('初始状态：status 为 null，dismissed 为 false', () => {
    const store = useSetupStore()
    expect(store.status).toBeNull()
    expect(store.statusLoading).toBe(false)
    expect(store.statusError).toBeNull()
    expect(store.dismissed).toBe(false)
    expect(store.ready).toBe(false)
    expect(store.missing).toEqual([])
    expect(store.commandResult).toBeNull()
    expect(store.llmResult).toBeNull()
    expect(store.neo4jResult).toBeNull()
  })

  it('默认表单字段：backend=openai_compat, mode=docker-compose, port=80, workers=2', () => {
    const store = useSetupStore()
    expect(store.form.llm_backend).toBe('openai_compat')
    expect(store.form.mode).toBe('docker-compose')
    expect(store.form.port).toBe(80)
    expect(store.form.workers).toBe(2)
    expect(store.form.openai_compat_base_url).toBe('https://api.deepseek.com/v1')
    expect(store.form.openai_compat_model).toBe('deepseek-chat')
  })

  // ────────── activeLLMBaseUrl / activeLLMModel ──────────

  it('activeLLMBaseUrl 按 backend 切换', () => {
    const store = useSetupStore()
    store.form.llm_backend = 'openai_compat'
    expect(store.activeLLMBaseUrl).toBe('https://api.deepseek.com/v1')
    store.form.llm_backend = 'ollama'
    expect(store.activeLLMBaseUrl).toBe('http://localhost:11434')
    store.form.llm_backend = 'vllm'
    expect(store.activeLLMBaseUrl).toBe('http://localhost:8000')
  })

  it('activeLLMModel 按 backend 切换', () => {
    const store = useSetupStore()
    store.form.llm_backend = 'ollama'
    expect(store.activeLLMModel).toBe('qwen2.5:7b')
    store.form.llm_backend = 'vllm'
    expect(store.activeLLMModel).toBe('Qwen2.5-14B-Instruct')
  })

  // ────────── refreshStatus ──────────

  it('refreshStatus 成功时写入 status 并清空 error', async () => {
    const fakeStatus = {
      llm_backend: 'openai_compat',
      llm_configured: true,
      llm_backend_options: ['openai_compat', 'ollama', 'vllm'],
      neo4j_uri: 'bolt://localhost:7687',
      neo4j_configured: true,
      auth_enabled: false,
      bootstrap_admin_configured: true,
      tracing_enabled: false,
      ready: true,
      missing: [],
    }
    vi.mocked(setupApi.getSetupStatus).mockResolvedValue(fakeStatus)

    const store = useSetupStore()
    await store.refreshStatus()

    expect(store.status).toEqual(fakeStatus)
    expect(store.statusLoading).toBe(false)
    expect(store.statusError).toBeNull()
    expect(store.ready).toBe(true)
    expect(store.llmConfigured).toBe(true)
    expect(store.neo4jConfigured).toBe(true)
    expect(store.authConfigured).toBe(true)
    expect(store.missing).toEqual([])
  })

  it('refreshStatus 失败时写入 error 且 status 为 null', async () => {
    vi.mocked(setupApi.getSetupStatus).mockRejectedValue(new Error('network error'))

    const store = useSetupStore()
    await store.refreshStatus()

    expect(store.status).toBeNull()
    expect(store.statusError).toBe('network error')
    expect(store.statusLoading).toBe(false)
  })

  it('missing 来自 status.missing', () => {
    const store = useSetupStore()
    store.status = {
      llm_backend: 'openai_compat',
      llm_configured: false,
      llm_backend_options: [],
      neo4j_uri: '',
      neo4j_configured: false,
      auth_enabled: false,
      bootstrap_admin_configured: false,
      tracing_enabled: false,
      ready: false,
      missing: ['llm', 'neo4j'],
    }
    expect(store.missing).toEqual(['llm', 'neo4j'])
    expect(store.ready).toBe(false)
  })

  // ────────── testLLM ──────────

  it('testLLM 用当前表单值调用 api 并写入 llmResult（成功）', async () => {
    vi.mocked(setupApi.testLLM).mockResolvedValue({
      ok: true,
      backend: 'openai_compat',
      model: 'deepseek-chat',
      latency_ms: 120,
    })

    const store = useSetupStore()
    store.form.openai_compat_api_key = 'sk-test'
    const resp = await store.testLLM()

    expect(setupApi.testLLM).toHaveBeenCalledWith({
      backend: 'openai_compat',
      base_url: 'https://api.deepseek.com/v1',
      model: 'deepseek-chat',
      api_key: 'sk-test',
    })
    expect(resp.ok).toBe(true)
    expect(store.llmResult?.ok).toBe(true)
    expect(store.llmResult?.latency_ms).toBe(120)
    expect(store.testingLLM).toBe(false)
  })

  it('testLLM 请求失败时返回 ok:false 并写入 llmResult', async () => {
    vi.mocked(setupApi.testLLM).mockRejectedValue(new Error('request failed'))

    const store = useSetupStore()
    const resp = await store.testLLM()

    expect(resp.ok).toBe(false)
    expect(resp.error).toBe('request failed')
    expect(store.llmResult?.ok).toBe(false)
  })

  it('testLLM 在 ollama 模式下不传 api_key', async () => {
    vi.mocked(setupApi.testLLM).mockResolvedValue({
      ok: true,
      backend: 'ollama',
      model: 'qwen2.5:7b',
    })

    const store = useSetupStore()
    store.form.llm_backend = 'ollama'
    await store.testLLM()

    expect(setupApi.testLLM).toHaveBeenCalledWith({
      backend: 'ollama',
      base_url: 'http://localhost:11434',
      model: 'qwen2.5:7b',
    })
  })

  // ────────── testNeo4j ──────────

  it('testNeo4j 用当前表单值调用 api 并写入 neo4jResult（成功）', async () => {
    vi.mocked(setupApi.testNeo4j).mockResolvedValue({
      ok: true,
      uri: 'bolt://localhost:7687',
      version: '5.20.0',
      latency_ms: 30,
    })

    const store = useSetupStore()
    store.form.neo4j_password = 'mypwd'
    const resp = await store.testNeo4j()

    expect(setupApi.testNeo4j).toHaveBeenCalledWith({
      uri: 'bolt://localhost:7687',
      user: 'neo4j',
      password: 'mypwd',
    })
    expect(resp.ok).toBe(true)
    expect(store.neo4jResult?.version).toBe('5.20.0')
  })

  it('testNeo4j 请求失败时返回 ok:false', async () => {
    vi.mocked(setupApi.testNeo4j).mockRejectedValue(new Error('boom'))

    const store = useSetupStore()
    const resp = await store.testNeo4j()

    expect(resp.ok).toBe(false)
    expect(resp.uri).toBe('bolt://localhost:7687')
  })

  // ────────── generateCommand ──────────

  it('generateCommand 成功时写入 commandResult', async () => {
    vi.mocked(setupApi.generateCommand).mockResolvedValue({
      command: 'docker compose up -d',
      env_file_content: 'ENV=production\n',
    })

    const store = useSetupStore()
    const resp = await store.generateCommand()

    expect(setupApi.generateCommand).toHaveBeenCalled()
    expect(resp.command).toBe('docker compose up -d')
    expect(store.commandResult?.env_file_content).toBe('ENV=production\n')
    expect(store.generating).toBe(false)
  })

  it('generateCommand 失败时抛出异常且不写入 commandResult', async () => {
    vi.mocked(setupApi.generateCommand).mockRejectedValue(new Error('500'))

    const store = useSetupStore()
    await expect(store.generateCommand()).rejects.toThrow('500')
    expect(store.commandResult).toBeNull()
  })

  // ────────── dismiss / undismiss ──────────

  it('dismiss 标记后 dismissed=true，undismiss 恢复', () => {
    const store = useSetupStore()
    expect(store.dismissed).toBe(false)
    store.dismiss()
    expect(store.dismissed).toBe(true)
    store.undismiss()
    expect(store.dismissed).toBe(false)
  })

  // ────────── resetForm ──────────

  it('resetForm 重置表单到默认值', () => {
    const store = useSetupStore()
    store.form.llm_backend = 'vllm'
    store.form.port = 8080
    store.form.openai_compat_api_key = 'sk-changed'
    store.llmResult = { ok: true, backend: 'vllm', model: 'x' }
    store.commandResult = { command: 'x', env_file_content: 'y' }

    store.resetForm()

    expect(store.form.llm_backend).toBe('openai_compat')
    expect(store.form.port).toBe(80)
    expect(store.form.openai_compat_api_key).toBe('')
    expect(store.llmResult).toBeNull()
    expect(store.commandResult).toBeNull()
  })
})
