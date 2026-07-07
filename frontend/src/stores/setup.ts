/**
 * Setup Wizard 状态管理（开箱配置引导）
 *
 * 职责：
 * - 持有当前环境配置完成度（setup status）
 * - 持有向导表单输入（LLM/Neo4j/认证/部署参数）
 * - 提供 test-llm / test-neo4j / generate-command 操作
 * - 持久化向导完成状态到 localStorage，避免重复打扰已配置好的用户
 *
 * 设计：
 * - status 为只读快照，每次进入向导或主动 refresh 时拉取
 * - form 表单字段可被用户编辑，未保存前不影响后端配置
 * - completedOnce 标记用户是否已主动关闭过向导（用于自动跳过）
 */
import { defineStore } from 'pinia'
import { ref, computed, reactive } from 'vue'
import { useStorage } from '@vueuse/core'
import * as setupApi from '@/api/setup'
import type {
  SetupStatus,
  TestLLMRequest,
  TestLLMResponse,
  TestNeo4jRequest,
  TestNeo4jResponse,
  GenerateCommandRequest,
  GenerateCommandResponse,
  LLMDriver,
} from '@/api/setup'

/** 默认表单值（用户可改） */
function defaultForm() {
  return {
    mode: 'docker-compose' as 'docker-run' | 'docker-compose',
    llm_backend: 'openai_compat' as LLMDriver,

    // LLM - openai_compat
    openai_compat_base_url: 'https://api.deepseek.com/v1',
    openai_compat_api_key: '',
    openai_compat_model: 'deepseek-chat',

    // LLM - ollama
    ollama_base_url: 'http://localhost:11434',
    ollama_model: 'qwen2.5:7b',

    // LLM - vllm
    vllm_base_url: 'http://localhost:8000',
    vllm_model: 'Qwen2.5-14B-Instruct',

    // Neo4j
    neo4j_uri: 'bolt://localhost:7687',
    neo4j_user: 'neo4j',
    neo4j_password: 'password',

    // 认证
    enable_auth: false,
    api_token: '',
    bootstrap_admin_user: 'admin',
    bootstrap_admin_password: 'admin',

    // 部署参数
    port: 80,
    workers: 2,
  }
}

export const useSetupStore = defineStore('setup', () => {
  // ── 状态 ──
  const status = ref<SetupStatus | null>(null)
  const statusLoading = ref(false)
  const statusError = ref<string | null>(null)

  const form = reactive(defaultForm())

  const testingLLM = ref(false)
  const llmResult = ref<TestLLMResponse | null>(null)

  const testingNeo4j = ref(false)
  const neo4jResult = ref<TestNeo4jResponse | null>(null)

  const generating = ref(false)
  const commandResult = ref<GenerateCommandResponse | null>(null)

  /** 用户是否主动关闭过向导（持久化，避免重复打扰） */
  const dismissed = useStorage('opskg:setup:dismissed', false)

  // ── 计算属性 ──

  /** LLM 是否已配置（来自后端 status） */
  const llmConfigured = computed(() => status.value?.llm_configured ?? false)
  /** Neo4j 是否已配置 */
  const neo4jConfigured = computed(() => status.value?.neo4j_configured ?? false)
  /** 认证是否已配置 */
  const authConfigured = computed(
    () => status.value?.auth_enabled || status.value?.bootstrap_admin_configured || false,
  )
  /** 整体是否就绪 */
  const ready = computed(() => status.value?.ready ?? false)
  /** 缺失项列表 */
  const missing = computed<string[]>(() => status.value?.missing ?? [])

  /** LLM 表单中按 backend 切换后展示的 base_url */
  const activeLLMBaseUrl = computed(() => {
    if (form.llm_backend === 'openai_compat') return form.openai_compat_base_url
    if (form.llm_backend === 'ollama') return form.ollama_base_url
    return form.vllm_base_url
  })
  /** LLM 表单中按 backend 切换后展示的 model */
  const activeLLMModel = computed(() => {
    if (form.llm_backend === 'openai_compat') return form.openai_compat_model
    if (form.llm_backend === 'ollama') return form.ollama_model
    return form.vllm_model
  })

  // ── actions ──

  /** 拉取后端配置完成度 */
  async function refreshStatus(): Promise<void> {
    statusLoading.value = true
    statusError.value = null
    try {
      status.value = await setupApi.getSetupStatus()
    } catch (err: any) {
      statusError.value = err.message || '无法获取配置状态'
      status.value = null
    } finally {
      statusLoading.value = false
    }
  }

  /** 测试 LLM 连通（用当前 form 中的值） */
  async function testLLM(): Promise<TestLLMResponse> {
    testingLLM.value = true
    llmResult.value = null
    try {
      const req: TestLLMRequest = {
        backend: form.llm_backend,
        base_url: activeLLMBaseUrl.value,
        model: activeLLMModel.value,
      }
      if (form.llm_backend === 'openai_compat') {
        req.api_key = form.openai_compat_api_key
      }
      const resp = await setupApi.testLLM(req)
      llmResult.value = resp
      return resp
    } catch (err: any) {
      const resp: TestLLMResponse = {
        ok: false,
        backend: form.llm_backend,
        model: activeLLMModel.value,
        error: err.message || '请求失败',
      }
      llmResult.value = resp
      return resp
    } finally {
      testingLLM.value = false
    }
  }

  /** 测试 Neo4j 连通（用当前 form 中的值） */
  async function testNeo4j(): Promise<TestNeo4jResponse> {
    testingNeo4j.value = true
    neo4jResult.value = null
    try {
      const req: TestNeo4jRequest = {
        uri: form.neo4j_uri,
        user: form.neo4j_user,
        password: form.neo4j_password,
      }
      const resp = await setupApi.testNeo4j(req)
      neo4jResult.value = resp
      return resp
    } catch (err: any) {
      const resp: TestNeo4jResponse = {
        ok: false,
        uri: form.neo4j_uri,
        error: err.message || '请求失败',
      }
      neo4jResult.value = resp
      return resp
    } finally {
      testingNeo4j.value = false
    }
  }

  /** 生成 docker 启动命令 + .env 文件内容 */
  async function generateCommand(): Promise<GenerateCommandResponse> {
    generating.value = true
    commandResult.value = null
    try {
      const req: GenerateCommandRequest = {
        mode: form.mode,
        llm_backend: form.llm_backend,
        openai_compat_base_url: form.openai_compat_base_url,
        openai_compat_api_key: form.openai_compat_api_key,
        openai_compat_model: form.openai_compat_model,
        ollama_base_url: form.ollama_base_url,
        ollama_model: form.ollama_model,
        vllm_base_url: form.vllm_base_url,
        vllm_model: form.vllm_model,
        neo4j_password: form.neo4j_password,
        enable_auth: form.enable_auth,
        api_token: form.api_token,
        bootstrap_admin_user: form.bootstrap_admin_user,
        bootstrap_admin_password: form.bootstrap_admin_password,
        port: form.port,
        workers: form.workers,
      }
      const resp = await setupApi.generateCommand(req)
      commandResult.value = resp
      return resp
    } catch (err: any) {
      throw err
    } finally {
      generating.value = false
    }
  }

  /** 用户主动关闭向导（持久化，避免重复打扰） */
  function dismiss() {
    dismissed.value = true
  }

  /** 重新打开向导 */
  function undismiss() {
    dismissed.value = false
  }

  /** 重置表单到默认值 */
  function resetForm() {
    Object.assign(form, defaultForm())
    llmResult.value = null
    neo4jResult.value = null
    commandResult.value = null
  }

  return {
    // state
    status,
    statusLoading,
    statusError,
    form,
    testingLLM,
    llmResult,
    testingNeo4j,
    neo4jResult,
    generating,
    commandResult,
    dismissed,
    // computed
    llmConfigured,
    neo4jConfigured,
    authConfigured,
    ready,
    missing,
    activeLLMBaseUrl,
    activeLLMModel,
    // actions
    refreshStatus,
    testLLM,
    testNeo4j,
    generateCommand,
    dismiss,
    undismiss,
    resetForm,
  }
})
