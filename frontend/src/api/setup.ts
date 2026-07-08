/**
 * Setup Wizard API 封装（开箱配置引导）
 *
 * 端点：
 * - GET  /setup/status           配置完成度检查（不暴露敏感值）
 * - POST /setup/test-llm         测试 LLM 连通
 * - POST /setup/test-neo4j       测试 Neo4j 连通
 * - POST /setup/generate-command 生成可复制的 docker 启动命令 + .env 文件内容
 *
 * 所有端点均无需认证（setup wizard 在认证配置前需要可用）。
 */
import api from './index'

export type LLMDriver = 'openai_compat' | 'ollama' | 'vllm'

export interface SetupStatus {
  llm_backend: string
  llm_configured: boolean
  llm_backend_options: string[]
  neo4j_uri: string
  neo4j_configured: boolean
  auth_enabled: boolean
  bootstrap_admin_configured: boolean
  tracing_enabled: boolean
  ready: boolean
  missing: string[]
}

export interface TestLLMRequest {
  backend?: LLMDriver
  base_url?: string
  api_key?: string
  model?: string
}

export interface TestLLMResponse {
  ok: boolean
  backend: string
  model: string
  latency_ms?: number | null
  error?: string | null
}

export interface TestNeo4jRequest {
  uri?: string
  user?: string
  password?: string
}

export interface TestNeo4jResponse {
  ok: boolean
  uri: string
  version?: string | null
  latency_ms?: number | null
  error?: string | null
}

export interface GenerateCommandRequest {
  mode: 'docker-run' | 'docker-compose'
  llm_backend: LLMDriver
  openai_compat_base_url: string
  openai_compat_api_key: string
  openai_compat_model: string
  ollama_base_url: string
  ollama_model: string
  vllm_base_url: string
  vllm_model: string
  neo4j_password: string
  enable_auth: boolean
  api_token: string
  bootstrap_admin_user: string
  bootstrap_admin_password: string
  port: number
  workers: number
}

export interface GenerateCommandResponse {
  command: string
  env_file_content: string
}

/** 配置完成度检查（只读，不暴露敏感值） */
export async function getSetupStatus(): Promise<SetupStatus> {
  return await api.get('/setup/status')
}

/** 测试 LLM 连通 */
export async function testLLM(req: TestLLMRequest): Promise<TestLLMResponse> {
  return await api.post('/setup/test-llm', req)
}

/** 测试 Neo4j 连通 */
export async function testNeo4j(req: TestNeo4jRequest): Promise<TestNeo4jResponse> {
  return await api.post('/setup/test-neo4j', req)
}

/** 生成 docker 启动命令 + .env 文件内容 */
export async function generateCommand(
  req: GenerateCommandRequest,
): Promise<GenerateCommandResponse> {
  return await api.post('/setup/generate-command', req)
}
