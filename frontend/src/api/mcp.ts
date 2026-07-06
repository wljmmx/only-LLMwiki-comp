import api from './index'

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
  return api.get<any, { tools: McpTool[] }>('/mcp/tools')
}

/**
 * JSON-RPC 调用（POST /mcp，支持批量）
 * POST /mcp  body: JsonRpcRequest | JsonRpcRequest[]
 */
export function mcpJsonRpc(req: JsonRpcRequest | JsonRpcRequest[]) {
  return api.post<any, JsonRpcResponse | JsonRpcResponse[]>('/mcp', req)
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
  args: Record<string, any> = {},
  id: number | string = 1,
  progressToken?: any,
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
  args: Record<string, any> = {},
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

export interface SseEvent {
  type: 'progress' | 'result' | 'error' | 'done'
  data: any
}

/**
 * SSE 流式调用工具（POST /mcp/stream）
 * 通过 fetch + ReadableStream 解析 SSE 事件
 */
export async function callToolStream(
  name: string,
  args: Record<string, any>,
  onEvent: (ev: SseEvent) => void,
  progressToken?: any,
  signal?: AbortSignal,
): Promise<void> {
  const token = typeof localStorage !== 'undefined' ? localStorage.getItem('opskg_token') : null
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  if (token) headers.Authorization = `Bearer ${token}`

  const body: JsonRpcRequest = {
    jsonrpc: '2.0',
    id: Date.now(),
    method: 'tools/call',
    params: { name, arguments: args },
    _meta: progressToken != null ? { progressToken } : undefined,
  }

  const res = await fetch('/api/mcp/stream', {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  })

  if (!res.ok || !res.body) {
    throw new Error(`SSE 请求失败: ${res.status} ${res.statusText}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = 'message'

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE 帧以 \n\n 分隔
    const frames = buffer.split('\n\n')
    buffer = frames.pop() || ''

    for (const frame of frames) {
      const lines = frame.split('\n')
      const dataLines: string[] = []
      currentEvent = 'message'
      for (const line of lines) {
        if (line.startsWith('event:')) {
          currentEvent = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trim())
        }
      }
      const dataStr = dataLines.join('\n')
      let data: any = dataStr
      try {
        data = JSON.parse(dataStr)
      } catch {
        // 保留为字符串
      }

      onEvent({ type: currentEvent as SseEvent['type'], data })
    }
  }

  onEvent({ type: 'done', data: null })
}
