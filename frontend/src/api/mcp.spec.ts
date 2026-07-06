import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/index', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import api from '@/api/index'
import {
  callTool,
  listMcpTools,
  listToolsViaJsonRpc,
  type JsonRpcRequest,
  type JsonRpcResponse,
} from './mcp'

describe('api/mcp.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('JsonRpcRequest / JsonRpcResponse 类型结构符合预期', () => {
    const req: JsonRpcRequest = {
      jsonrpc: '2.0',
      id: 1,
      method: 'tools/call',
      params: { name: 't', arguments: {} },
    }
    expect(req.jsonrpc).toBe('2.0')
    expect(req.method).toBe('tools/call')

    const resp: JsonRpcResponse = {
      jsonrpc: '2.0',
      id: 1,
      result: { ok: true },
    }
    expect(resp.result).toEqual({ ok: true })

    const errResp: JsonRpcResponse = {
      jsonrpc: '2.0',
      id: null,
      error: { code: -32600, message: 'invalid request' },
    }
    expect(errResp.error?.code).toBe(-32600)
  })

  it('listMcpTools 调用 GET /mcp/tools 并返回数据', async () => {
    const expected = {
      tools: [{ name: 'foo', description: 'd', inputSchema: { type: 'object' as const } }],
    }
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(expected)

    const res = await listMcpTools()

    expect(api.get).toHaveBeenCalledWith('/mcp/tools')
    expect(res).toEqual(expected)
  })

  it('callTool 正确构造 tools/call JSON-RPC 请求', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      jsonrpc: '2.0',
      id: 5,
      result: { ok: true },
    })

    const res = await callTool('myTool', { arg1: 'v1' }, 5)

    expect(api.post).toHaveBeenCalledWith('/mcp', {
      jsonrpc: '2.0',
      id: 5,
      method: 'tools/call',
      params: { name: 'myTool', arguments: { arg1: 'v1' } },
      _meta: undefined,
    })
    expect(res.id).toBe(5)
  })

  it('callTool 传入 progressToken 时附带 _meta.progressToken', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      jsonrpc: '2.0',
      id: 1,
      result: {},
    })

    await callTool('t', {}, 1, 'token-xyz')

    expect(api.post).toHaveBeenCalledWith(
      '/mcp',
      expect.objectContaining({
        _meta: { progressToken: 'token-xyz' },
        method: 'tools/call',
      }),
    )
  })

  it('callTool 收到数组响应时取首个', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue([
      { jsonrpc: '2.0', id: 1, result: { a: 1 } },
      { jsonrpc: '2.0', id: 2, result: { b: 2 } },
    ])

    const res = await callTool('t')
    expect(res).toEqual({ jsonrpc: '2.0', id: 1, result: { a: 1 } })
  })

  it('listToolsViaJsonRpc 调用 tools/list 方法', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({
      jsonrpc: '2.0',
      id: 7,
      result: { tools: [] },
    })

    const res = await listToolsViaJsonRpc(7)

    expect(api.post).toHaveBeenCalledWith('/mcp', {
      jsonrpc: '2.0',
      id: 7,
      method: 'tools/list',
    })
    expect(res.id).toBe(7)
    expect(res.result).toEqual({ tools: [] })
  })
})
