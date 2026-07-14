/** 系统配置管理 API（P2-2） */
import api from './index'

export interface SettingsFieldMeta {
  type: string
  label: string
  description: string
  default?: string | number
  options?: string[]
  range?: [number, number]
  sensitive?: boolean
}

export interface SettingsField {
  value: string | number | boolean
  meta: SettingsFieldMeta
}

export interface SettingsGroup {
  label: string
  items: Record<string, SettingsField>
}

export interface SettingsResponse {
  groups: Record<string, SettingsGroup>
}

export interface SettingsUpdateRequest {
  updates: Record<string, string | number | boolean>
}

export interface SettingsUpdateResponse {
  updated: string[]
  message: string
  restart_endpoint: string
}

export interface SettingsValidateResponse {
  valid: boolean
  errors: string[]
}

/** 获取当前配置 */
export function getSettings(): Promise<SettingsResponse> {
  return api.get('/settings')
}

/** 更新配置 */
export function updateSettings(data: SettingsUpdateRequest): Promise<SettingsUpdateResponse> {
  return api.put('/settings', data)
}

/** 验证配置 */
export function validateSettings(data: SettingsUpdateRequest): Promise<SettingsValidateResponse> {
  return api.post('/settings/validate', data)
}

/** 重启服务 */
export function restartService(): Promise<{ restart: boolean; message: string }> {
  return api.post('/settings/restart')
}

/** LLM 连通性测试 */
export interface LLMTestRequest {
  backend?: string
  base_url?: string
  api_key?: string
  model?: string
}

export interface LLMTestResponse {
  success: boolean
  backend: string
  model: string
  base_url: string
  latency_ms: number
  message: string
  errors?: string[]
}

export function testLLMConnection(data?: LLMTestRequest): Promise<LLMTestResponse> {
  return api.post('/settings/llm/test', data || {})
}