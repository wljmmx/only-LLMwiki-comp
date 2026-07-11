import api, { getApiBaseUrl } from './index'
import { streamSse } from '@/utils/sse'

// ────────── MCP（F13 MCP 工具浏览器） ──────────

export interface ToolAnnotations {
  readOnlyHint?: boolean
  destructiveHint?: boolean
  idempotentHint?: boolean
}

export interface McpTool {
  name: string
  description: string
  inputSchema: {
    type: 'object'
    properties?: Record<string, any>
    required?: string[]
  }
  annotations?: ToolAnnotations
}

export interface McpResource {
  uri: string
  name?: string
  description?: string
  mimeType?: string
}

export interface McpPrompt {
  name: string
  description?: string
  arguments?: { name: string; description?: string; required?: boolean }[]
}

export interface JsonRpcRequest {
  jsonrpc: '2.0'
  id: number | string
  method: string
  params?: any
  _meta?: { progressToken?: any }
}

export interface JsonRpcResponse {
  jsonrpc: '2.0'
  id: number | string | null
  result?: any
  error?: { code: number; message: string; data?: any }
}

/**
 * 便捷接口：列出所有 MCP 工具
 * GET /mcp/tools
 */
export function listMcpTools() {
  return api.get<unknown, { tools: McpTool[] }>('/mcp/tools')
}

/**
 * JSON-RPC 调用（POST /mcp，支持批量）
 * POST /mcp  body: JsonRpcRequest | JsonRpcRequest[]
 */
export function mcpJsonRpc(req: JsonRpcRequest | JsonRpcRequest[]) {
  return api.post<unknown, JsonRpcResponse | JsonRpcResponse[]>('/mcp', req)
}

/**
 * 列出工具（通过 JSON-RPC tools/list）
 */
export async function listToolsViaJsonRpc(id: number | string = 1): Promise<JsonRpcResponse> {
  const res = await mcpJsonRpc({ jsonrpc: '2.0', id, method: 'tools/list' })
  return Array.isArray(res) ? res[0] : res
}

/**
 * 调用工具（通过 JSON-RPC tools/call）
 * 返回值需注意双层 JSON：result.content[0].text 是 handler 输出的 JSON 字符串
 */
export async function callTool(
  name: string,
  args: Record<string, unknown> = {},
  id: number | string = 1,
  progressToken?: unknown,
): Promise<JsonRpcResponse> {
  const res = await mcpJsonRpc({
    jsonrpc: '2.0',
    id,
    method: 'tools/call',
    params: { name, arguments: args },
    _meta: progressToken != null ? { progressToken } : undefined,
  })
  return Array.isArray(res) ? res[0] : res
}

/**
 * 列出资源（JSON-RPC resources/list）
 */
export async function listResources(id: number | string = 1): Promise<JsonRpcResponse> {
  const res = await mcpJsonRpc({ jsonrpc: '2.0', id, method: 'resources/list' })
  return Array.isArray(res) ? res[0] : res
}

/**
 * 读取资源（JSON-RPC resources/read）
 */
export async function readResource(uri: string, id: number | string = 1): Promise<JsonRpcResponse> {
  const res = await mcpJsonRpc({
    jsonrpc: '2.0',
    id,
    method: 'resources/read',
    params: { uri },
  })
  return Array.isArray(res) ? res[0] : res
}

/**
 * 列出 prompt 模板（JSON-RPC prompts/list）
 */
export async function listPrompts(id: number | string = 1): Promise<JsonRpcResponse> {
  const res = await mcpJsonRpc({ jsonrpc: '2.0', id, method: 'prompts/list' })
  return Array.isArray(res) ? res[0] : res
}

/**
 * 渲染 prompt（JSON-RPC prompts/get）
 */
export async function getPrompt(
  name: string,
  args: Record<string, unknown> = {},
  id: number | string = 1,
): Promise<JsonRpcResponse> {
  const res = await mcpJsonRpc({
    jsonrpc: '2.0',
    id,
    method: 'prompts/get',
    params: { name, arguments: args },
  })
  return Array.isArray(res) ? res[0] : res
}

// ────────── SSE 流式调用（用于长耗时工具如 generate_runbook） ──────────

/**
 * SSE 事件。
 *
 * P4-3：`type` 放宽为 string（与 utils/sse 的 SseEvent 对齐），`data` 保留 any
 * 以便消费方（如 McpView.vue）直接访问 ev.data.result.content[0].text。
 * 与 utils/sse 的 SseEvent（data: unknown）结构兼容。
 */
export interface SseEvent {
  type: string
  data: any
}

/**
 * SSE 流式调用工具（POST /mcp/stream）
 *
 * SSE 帧解析由共享工具 streamSse 负责（P4-3 抽取）。
 * streamSse 为回调式并返回 AbortController，本函数将其适配为 Promise<void>：
 * 收到 done 事件（服务端发送或流末尾合成）时 resolve，出错时 reject。
 *
 * S14-3：token 与 baseURL 通过共享 helper 获取（不再硬编码 'opskg_token' / '/api'）。
 */
export async function callToolStream(
  name: string,
  args: Record<string, unknown>,
  onEvent: (ev: SseEvent) => void,
  progressToken?: unknown,
  signal?: AbortSignal,
): Promise<void> {
  const body: JsonRpcRequest = {
    jsonrpc: '2.0',
    id: Date.now(),
    method: 'tools/call',
    params: { name, arguments: args },
    _meta: progressToken != null ? { progressToken } : undefined,
  }

  return new Promise<void>((resolve, reject) => {
    streamSse(
      {
        url: `${getApiBaseUrl()}/mcp/stream`,
        body,
        signal,
        // 流末尾合成 done 事件，确保 Promise 总能 resolve
        emitSyntheticDone: true,
      },
      (ev) => {
        onEvent(ev)
        if (ev.type === 'done') resolve()
      },
      (message) => reject(new Error(message)),
    )
  })
}
