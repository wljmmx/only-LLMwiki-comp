import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const mockMessage = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return { ...actual, useMessage: () => mockMessage }
})

// mock @/api/mcp 模块（包括 SSE 流式调用 callToolStream）
vi.mock('@/api/mcp', () => ({
  listMcpTools: vi.fn(),
  callTool: vi.fn(),
  listResources: vi.fn(),
  readResource: vi.fn(),
  listPrompts: vi.fn(),
  getPrompt: vi.fn(),
  callToolStream: vi.fn(),
}))

// mock @/api/index 模块（提供 getAuthToken）
vi.mock('@/api/index', () => ({
  getAuthToken: () => 'fake-token',
}))

import {
  listMcpTools,
  callTool,
  listResources,
  readResource,
  listPrompts,
  getPrompt,
  callToolStream,
} from '@/api/mcp'
import McpView from '@/views/McpView.vue'
import '@/test/setup'

const sampleTool = {
  name: 'search_wiki',
  description: '搜索 wiki',
  inputSchema: {
    type: 'object' as const,
    properties: {
      query: { type: 'string', description: '关键词' },
      limit: { type: 'integer', default: 10 },
    },
    required: ['query'],
  },
  annotations: { readOnlyHint: true },
}

const sampleResource = {
  uri: 'wiki://index',
  name: 'Wiki 索引',
  description: '所有 wiki 页面',
  mimeType: 'text/markdown',
}

const samplePrompt = {
  name: 'summarize_incident',
  description: '总结故障',
  arguments: [{ name: 'incident_id', description: '故障 ID', required: true }],
}

describe('McpView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView() {
    return mount(McpView, {
      global: { plugins: [pinia] },
    })
  }

  function setupEmptyLists() {
    ;(listMcpTools as any).mockResolvedValue({ tools: [] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
  }

  it('初始状态：onMounted 触发后三个 loading true、列表空、tab=tools', () => {
    setupEmptyLists()
    const wrapper = mountView()
    const vm = wrapper.vm as any
    expect(vm.toolsLoading).toBe(true)
    expect(vm.resourcesLoading).toBe(true)
    expect(vm.promptsLoading).toBe(true)
    expect(vm.tools).toEqual([])
    expect(vm.resources).toEqual([])
    expect(vm.prompts).toEqual([])
    expect(vm.activeTab).toBe('tools')
    expect(vm.sseMode).toBe(false)
  })

  it('onMounted 调用 listMcpTools/listResources/listPrompts', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [sampleTool] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [sampleResource] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [samplePrompt] } })
    const wrapper = mountView()
    await flushPromises()
    expect(listMcpTools).toHaveBeenCalledTimes(1)
    expect(listResources).toHaveBeenCalledWith(1)
    expect(listPrompts).toHaveBeenCalledWith(1)
    const vm = wrapper.vm as any
    expect(vm.tools).toHaveLength(1)
    expect(vm.resources).toHaveLength(1)
    expect(vm.prompts).toHaveLength(1)
    expect(vm.toolsLoading).toBe(false)
    expect(vm.resourcesLoading).toBe(false)
    expect(vm.promptsLoading).toBe(false)
  })

  it('loadTools 失败时 message.error 且 toolsLoading 恢复 false', async () => {
    ;(listMcpTools as any).mockRejectedValue({
      response: { data: { detail: '工具加载失败' } },
    })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    const wrapper = mountView()
    await flushPromises()
    expect(mockMessage.error).toHaveBeenCalledWith('工具加载失败')
    const vm = wrapper.vm as any
    expect(vm.toolsLoading).toBe(false)
  })

  it('loadResources 失败时 message.error 且 resourcesLoading 恢复 false', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [] })
    ;(listResources as any).mockRejectedValue({
      response: { data: { detail: '资源加载失败' } },
    })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    const wrapper = mountView()
    await flushPromises()
    expect(mockMessage.error).toHaveBeenCalledWith('资源加载失败')
    const vm = wrapper.vm as any
    expect(vm.resourcesLoading).toBe(false)
  })

  it('loadPrompts 失败时 message.error 且 promptsLoading 恢复 false', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockRejectedValue({
      response: { data: { detail: 'prompt 加载失败' } },
    })
    const wrapper = mountView()
    await flushPromises()
    expect(mockMessage.error).toHaveBeenCalledWith('prompt 加载失败')
    const vm = wrapper.vm as any
    expect(vm.promptsLoading).toBe(false)
  })

  it('annotationColor 按 destructive/readOnly/idempotent 顺序映射', async () => {
    setupEmptyLists()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.annotationColor({ destructiveHint: true }).color).toBe('#d03050')
    expect(vm.annotationColor({ readOnlyHint: true }).color).toBe('#18a058')
    expect(vm.annotationColor({ idempotentHint: true }).color).toBe('#2080f0')
    expect(vm.annotationColor({}).color).toBe('#999')
  })

  it('annotationLabel 按优先级返回标签', async () => {
    setupEmptyLists()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    expect(vm.annotationLabel({ destructiveHint: true })).toBe('destructive')
    expect(vm.annotationLabel({ readOnlyHint: true })).toBe('readOnly')
    expect(vm.annotationLabel({ idempotentHint: true })).toBe('idempotent')
    expect(vm.annotationLabel({})).toBe('—')
  })

  it('selectTool 用默认值与必填占位预填参数', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [sampleTool] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectTool(sampleTool)
    expect(vm.selectedTool.name).toBe('search_wiki')
    const args = JSON.parse(vm.toolArgsText)
    expect(args.limit).toBe(10) // 默认值
    expect(args.query).toBe('') // 必填项占位
    expect(vm.toolResult).toBe('')
    expect(vm.toolError).toBe('')
    expect(vm.sseEvents).toEqual([])
  })

  it('doCallTool 未选工具时直接 return', async () => {
    setupEmptyLists()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.doCallTool()
    expect(callTool).not.toHaveBeenCalled()
    expect(callToolStream).not.toHaveBeenCalled()
  })

  it('doCallTool JSON 解析失败时设置 toolError 且不调用 callTool', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [sampleTool] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectTool(sampleTool)
    vm.toolArgsText = '{invalid'
    await vm.doCallTool()
    expect(callTool).not.toHaveBeenCalled()
    expect(vm.toolError).toContain('JSON 解析失败')
  })

  it('doCallTool 同步模式成功且 result.content[0].text 是 JSON 字符串时解析', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [sampleTool] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    ;(callTool as any).mockResolvedValue({
      jsonrpc: '2.0',
      id: 1,
      result: {
        content: [{ type: 'text', text: '{"hits": 3, "items": []}' }],
      },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectTool(sampleTool)
    vm.toolArgsText = '{"query":"nginx"}'
    await vm.doCallTool()
    expect(callTool).toHaveBeenCalledWith(
      'search_wiki',
      { query: 'nginx' },
      expect.any(Number),
    )
    expect(vm.toolResult).toContain('hits')
    expect(vm.toolResult).toContain('3')
    expect(vm.toolError).toBe('')
    expect(vm.toolCalling).toBe(false)
  })

  it('doCallTool 同步模式返回 error 时填充 toolError', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [sampleTool] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    ;(callTool as any).mockResolvedValue({
      jsonrpc: '2.0',
      id: 1,
      error: { code: -32600, message: 'invalid request' },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectTool(sampleTool)
    vm.toolArgsText = '{}'
    await vm.doCallTool()
    expect(vm.toolError).toContain('-32600')
    expect(vm.toolError).toContain('invalid request')
  })

  it('doCallTool 同步模式返回 isError=true 时设置 toolError', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [sampleTool] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    ;(callTool as any).mockResolvedValue({
      jsonrpc: '2.0',
      id: 1,
      result: {
        isError: true,
        content: [{ type: 'text', text: '执行失败原因' }],
      },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectTool(sampleTool)
    vm.toolArgsText = '{}'
    await vm.doCallTool()
    expect(vm.toolError).toBe('工具返回 isError=true')
  })

  it('doCallTool 同步模式抛错时填充 toolError', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [sampleTool] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    ;(callTool as any).mockRejectedValue({
      response: { data: { detail: '调用超时' } },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectTool(sampleTool)
    vm.toolArgsText = '{}'
    await vm.doCallTool()
    expect(vm.toolError).toBe('调用超时')
    expect(vm.toolCalling).toBe(false)
  })

  it('doCallTool SSE 模式通过 callToolStream 推送事件并填充 result', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [sampleTool] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    ;(callToolStream as any).mockImplementation(
      async (
        _name: string,
        _args: any,
        onEvent: (ev: any) => void,
        _progressToken?: any,
      ) => {
        onEvent({ type: 'progress', data: { progress: 50 } })
        onEvent({
          type: 'result',
          data: { result: { content: [{ type: 'text', text: '{"ok": true}' }] } },
        })
        onEvent({ type: 'done', data: null })
      },
    )
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectTool(sampleTool)
    vm.sseMode = true
    vm.toolArgsText = '{}'
    await vm.doCallTool()
    expect(callToolStream).toHaveBeenCalled()
    expect(vm.sseEvents.length).toBeGreaterThanOrEqual(2)
    expect(vm.toolResult).toContain('ok')
    expect(vm.toolResult).toContain('true')
    expect(vm.toolCalling).toBe(false)
  })

  it('doCallTool SSE 模式 error 事件填充 toolError', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [sampleTool] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    ;(callToolStream as any).mockImplementation(
      async (
        _name: string,
        _args: any,
        onEvent: (ev: any) => void,
        _progressToken?: any,
      ) => {
        onEvent({ type: 'error', data: '内部错误' })
        onEvent({ type: 'done', data: null })
      },
    )
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectTool(sampleTool)
    vm.sseMode = true
    vm.toolArgsText = '{}'
    await vm.doCallTool()
    expect(vm.toolError).toBe('内部错误')
  })

  it('doCallTool SSE 模式无事件时填充 "SSE 无事件返回"', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [sampleTool] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    ;(callToolStream as any).mockImplementation(async () => {
      // 不推送任何事件
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectTool(sampleTool)
    vm.sseMode = true
    vm.toolArgsText = '{}'
    await vm.doCallTool()
    expect(vm.toolError).toBe('SSE 无事件返回')
  })

  it('doCallTool SSE 模式抛错时填充 toolError', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [sampleTool] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    ;(callToolStream as any).mockRejectedValue(new Error('SSE 请求失败: 500'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectTool(sampleTool)
    vm.sseMode = true
    vm.toolArgsText = '{}'
    await vm.doCallTool()
    expect(vm.toolError).toContain('SSE 请求失败')
    expect(vm.toolCalling).toBe(false)
  })

  it('readRes 成功填充 resourceContent（text 字段）', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [sampleResource] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    ;(readResource as any).mockResolvedValue({
      result: { contents: [{ text: '# Wiki Index', uri: 'wiki://index' }] },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.readRes('wiki://index')
    expect(readResource).toHaveBeenCalledWith('wiki://index', expect.any(Number))
    expect(vm.selectedResourceUri).toBe('wiki://index')
    expect(vm.resourceContent).toBe('# Wiki Index')
    expect(vm.resourceLoading).toBe(false)
  })

  it('readRes 内容为空时显示 "(空)"', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    ;(readResource as any).mockResolvedValue({ result: { contents: [] } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.readRes('wiki://empty')
    expect(vm.resourceContent).toBe('(空)')
  })

  it('readRes 失败时填充错误消息到 resourceContent', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [] } })
    ;(readResource as any).mockRejectedValue({
      response: { data: { detail: '资源不存在' } },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.readRes('wiki://missing')
    expect(vm.resourceContent).toContain('读取失败')
    expect(vm.resourceContent).toContain('资源不存在')
    expect(vm.resourceLoading).toBe(false)
  })

  it('selectPrompt 用空字符串预填参数', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [samplePrompt] } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectPrompt(samplePrompt)
    expect(vm.selectedPrompt.name).toBe('summarize_incident')
    const args = JSON.parse(vm.promptArgsText)
    expect(args.incident_id).toBe('')
    expect(vm.promptResult).toBe('')
  })

  it('doGetPrompt 成功填充 promptResult', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [samplePrompt] } })
    ;(getPrompt as any).mockResolvedValue({
      result: { messages: [{ role: 'user', content: '总结' }] },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectPrompt(samplePrompt)
    vm.promptArgsText = '{"incident_id":"inc-1"}'
    await vm.doGetPrompt()
    expect(getPrompt).toHaveBeenCalledWith(
      'summarize_incident',
      { incident_id: 'inc-1' },
      expect.any(Number),
    )
    expect(vm.promptResult).toContain('messages')
    expect(vm.promptLoading).toBe(false)
  })

  it('doGetPrompt JSON 解析失败时 message.error', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [samplePrompt] } })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectPrompt(samplePrompt)
    vm.promptArgsText = '{invalid'
    await vm.doGetPrompt()
    expect(getPrompt).not.toHaveBeenCalled()
    expect(mockMessage.error).toHaveBeenCalledWith(expect.stringContaining('JSON 解析失败'))
  })

  it('doGetPrompt 返回 error 时 message.error', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [samplePrompt] } })
    ;(getPrompt as any).mockResolvedValue({
      error: { code: -32600, message: 'invalid request' },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectPrompt(samplePrompt)
    vm.promptArgsText = '{}'
    await vm.doGetPrompt()
    expect(mockMessage.error).toHaveBeenCalledWith(expect.stringContaining('-32600'))
  })

  it('doGetPrompt 抛错时 message.error', async () => {
    ;(listMcpTools as any).mockResolvedValue({ tools: [] })
    ;(listResources as any).mockResolvedValue({ result: { resources: [] } })
    ;(listPrompts as any).mockResolvedValue({ result: { prompts: [samplePrompt] } })
    ;(getPrompt as any).mockRejectedValue({
      response: { data: { detail: '渲染失败' } },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectPrompt(samplePrompt)
    vm.promptArgsText = '{}'
    await vm.doGetPrompt()
    expect(mockMessage.error).toHaveBeenCalledWith('渲染失败')
    expect(vm.promptLoading).toBe(false)
  })

  it('doGetPrompt 未选 prompt 时直接 return', async () => {
    setupEmptyLists()
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.selectedPrompt = null
    await vm.doGetPrompt()
    expect(getPrompt).not.toHaveBeenCalled()
  })
})
