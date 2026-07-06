<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  NCard,
  NTag,
  NSpace,
  NButton,
  NSpin,
  NEmpty,
  NTabs,
  NTabPane,
  NInput,
  NCode,
  NCollapse,
  NCollapseItem,
  NDescriptions,
  NDescriptionsItem,
  NAlert,
  NSelect,
  useMessage,
} from 'naive-ui'
import {
  listMcpTools,
  callTool,
  listResources,
  readResource,
  listPrompts,
  getPrompt,
  callToolStream,
  type McpTool,
  type McpResource,
  type McpPrompt,
  type SseEvent,
} from '@/api/mcp'
import { getAuthToken } from '@/api/index'

const message = useMessage()

const isAuthenticated = computed(() => !!getAuthToken())

const activeTab = ref<'tools' | 'resources' | 'prompts'>('tools')

// ────────── Tools ──────────
const tools = ref<McpTool[]>([])
const toolsLoading = ref(false)
const selectedTool = ref<McpTool | null>(null)
const toolArgsText = ref<string>('{}')
const toolCalling = ref(false)
const toolResult = ref<string>('')
const toolError = ref<string>('')

// SSE
const sseMode = ref<boolean>(false)
const sseEvents = ref<SseEvent[]>([])

// ────────── Resources ──────────
const resources = ref<McpResource[]>([])
const resourcesLoading = ref(false)
const selectedResourceUri = ref<string>('')
const resourceContent = ref<string>('')
const resourceLoading = ref(false)

// ────────── Prompts ──────────
const prompts = ref<McpPrompt[]>([])
const promptsLoading = ref(false)
const selectedPrompt = ref<McpPrompt | null>(null)
const promptArgsText = ref<string>('{}')
const promptResult = ref<string>('')
const promptLoading = ref(false)

const annotationColor = (a: any): { color: string; bg: string } => {
  if (a?.destructiveHint) return { color: '#d03050', bg: 'rgba(208,48,80,0.1)' }
  if (a?.readOnlyHint) return { color: '#18a058', bg: 'rgba(24,160,88,0.1)' }
  if (a?.idempotentHint) return { color: '#2080f0', bg: 'rgba(32,128,240,0.1)' }
  return { color: '#999', bg: 'transparent' }
}

const annotationLabel = (a: any): string => {
  if (a?.destructiveHint) return 'destructive'
  if (a?.readOnlyHint) return 'readOnly'
  if (a?.idempotentHint) return 'idempotent'
  return '—'
}

async function loadTools() {
  toolsLoading.value = true
  try {
    const res = await listMcpTools()
    tools.value = res.tools || []
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '加载工具列表失败')
  } finally {
    toolsLoading.value = false
  }
}

function selectTool(tool: McpTool) {
  selectedTool.value = tool
  // 用 default 值预填参数
  const props = tool.inputSchema?.properties || {}
  const seed: Record<string, any> = {}
  Object.entries(props).forEach(([k, v]: [string, any]) => {
    if (v?.default !== undefined) seed[k] = v.default
    else if (tool.inputSchema?.required?.includes(k)) {
      // 给必填项占位
      if (v?.type === 'string') seed[k] = ''
      else if (v?.type === 'integer' || v?.type === 'number') seed[k] = 0
      else if (v?.type === 'boolean') seed[k] = false
      else if (v?.type === 'array') seed[k] = []
      else if (v?.type === 'object') seed[k] = {}
    }
  })
  toolArgsText.value = JSON.stringify(seed, null, 2)
  toolResult.value = ''
  toolError.value = ''
  sseEvents.value = []
}

async function doCallTool() {
  if (!selectedTool.value) return
  if (!isAuthenticated.value) {
    message.error('调用工具需要登录 token')
    return
  }
  let args: Record<string, any>
  try {
    args = JSON.parse(toolArgsText.value)
  } catch (e: any) {
    toolError.value = `JSON 解析失败: ${e.message}`
    return
  }

  toolCalling.value = true
  toolResult.value = ''
  toolError.value = ''
  sseEvents.value = []

  try {
    if (sseMode.value) {
      // SSE 模式
      const progressToken = `browser-${Date.now()}`
      await callToolStream(
        selectedTool.value.name,
        args,
        (ev) => {
          sseEvents.value.push(ev)
          if (ev.type === 'result' && ev.data?.result?.content?.[0]?.text) {
            // 双层 JSON：content[0].text 是 handler 输出的 JSON 字符串
            try {
              const inner = JSON.parse(ev.data.result.content[0].text)
              toolResult.value = JSON.stringify(inner, null, 2)
            } catch {
              toolResult.value = ev.data.result.content[0].text
            }
          } else if (ev.type === 'error') {
            toolError.value = typeof ev.data === 'string' ? ev.data : JSON.stringify(ev.data)
          }
        },
        progressToken,
      )
      if (!toolResult.value && !toolError.value && sseEvents.value.length === 0) {
        toolError.value = 'SSE 无事件返回'
      }
    } else {
      // 同步模式
      const res = await callTool(selectedTool.value.name, args, Date.now())
      if (res.error) {
        toolError.value = `JSON-RPC 错误 ${res.error.code}: ${res.error.message}`
      } else if (res.result?.content?.[0]?.text) {
        try {
          const inner = JSON.parse(res.result.content[0].text)
          toolResult.value = JSON.stringify(inner, null, 2)
        } catch {
          toolResult.value = res.result.content[0].text
        }
        if (res.result?.isError) {
          toolError.value = '工具返回 isError=true'
        }
      } else {
        toolResult.value = JSON.stringify(res.result, null, 2)
      }
    }
  } catch (err: any) {
    toolError.value = err?.response?.data?.detail || err?.message || '调用失败'
  } finally {
    toolCalling.value = false
  }
}

// ────────── Resources ──────────
async function loadResources() {
  resourcesLoading.value = true
  try {
    const res = await listResources(1)
    resources.value = res.result?.resources || []
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '加载资源失败')
  } finally {
    resourcesLoading.value = false
  }
}

async function readRes(uri: string) {
  selectedResourceUri.value = uri
  resourceLoading.value = true
  resourceContent.value = ''
  try {
    const res = await readResource(uri, 2)
    const contents = res.result?.contents || []
    if (contents.length === 0) {
      resourceContent.value = '(空)'
    } else {
      const c = contents[0]
      resourceContent.value = c.text || JSON.stringify(c, null, 2)
    }
  } catch (err: any) {
    resourceContent.value = `读取失败: ${err?.response?.data?.detail || err?.message}`
  } finally {
    resourceLoading.value = false
  }
}

// ────────── Prompts ──────────
async function loadPrompts() {
  promptsLoading.value = true
  try {
    const res = await listPrompts(1)
    prompts.value = res.result?.prompts || []
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '加载 prompt 失败')
  } finally {
    promptsLoading.value = false
  }
}

function selectPrompt(p: McpPrompt) {
  selectedPrompt.value = p
  // 用空值预填参数
  const seed: Record<string, any> = {}
  ;(p.arguments || []).forEach((a) => {
    seed[a.name] = ''
  })
  promptArgsText.value = JSON.stringify(seed, null, 2)
  promptResult.value = ''
}

async function doGetPrompt() {
  if (!selectedPrompt.value) return
  if (!isAuthenticated.value) {
    message.error('渲染 prompt 需要 token')
    return
  }
  let args: Record<string, any>
  try {
    args = JSON.parse(promptArgsText.value)
  } catch (e: any) {
    message.error(`JSON 解析失败: ${e.message}`)
    return
  }
  promptLoading.value = true
  promptResult.value = ''
  try {
    const res = await getPrompt(selectedPrompt.value.name, args, 2)
    if (res.error) {
      message.error(`JSON-RPC 错误 ${res.error.code}: ${res.error.message}`)
    } else {
      promptResult.value = JSON.stringify(res.result, null, 2)
    }
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '渲染失败')
  } finally {
    promptLoading.value = false
  }
}

onMounted(() => {
  loadTools()
  loadResources()
  loadPrompts()
})
</script>

<template>
  <div class="mcp-view">
    <div class="page-header">
      <h2 class="page-title">MCP 工具浏览器</h2>
      <p class="page-desc">
        Model Context Protocol — 浏览 13 个工具 / 资源 / Prompt 模板，支持 JSON-RPC 调用与 SSE
        流式响应
      </p>
    </div>

    <n-alert v-if="!isAuthenticated" type="info" style="margin-bottom: 16px">
      工具调用 / Prompt 渲染 / 资源读取需要登录 Token；列表查询在开发模式下放行
    </n-alert>

    <n-card :bordered="true">
      <n-tabs v-model:value="activeTab" type="line" animated>
        <!-- ────────── Tools ────────── -->
        <n-tab-pane name="tools" tab="🛠️ 工具">
          <div class="two-pane">
            <div class="left-pane">
              <div class="pane-header">
                <span>工具列表 ({{ tools.length }})</span>
                <n-button quaternary size="small" :loading="toolsLoading" @click="loadTools">
                  刷新
                </n-button>
              </div>
              <div v-if="toolsLoading" class="loading-container">
                <n-spin size="medium" />
              </div>
              <n-empty v-else-if="!tools.length" description="暂无工具" />
              <div v-else class="tool-list">
                <div
                  v-for="t in tools"
                  :key="t.name"
                  class="tool-item"
                  :class="{ active: selectedTool?.name === t.name }"
                  @click="selectTool(t)"
                >
                  <div class="tool-name">
                    <code>{{ t.name }}</code>
                    <n-tag
                      v-if="t.annotations"
                      size="tiny"
                      :bordered="false"
                      :style="annotationColor(t.annotations)"
                    >
                      {{ annotationLabel(t.annotations) }}
                    </n-tag>
                  </div>
                  <div class="tool-desc">{{ t.description }}</div>
                </div>
              </div>
            </div>

            <div class="right-pane">
              <div v-if="!selectedTool" class="empty-detail">
                <n-empty description="选择左侧工具查看详情并调用" />
              </div>
              <template v-else>
                <n-descriptions :column="1" bordered size="small" label-placement="left">
                  <n-descriptions-item label="名称">
                    <code>{{ selectedTool.name }}</code>
                  </n-descriptions-item>
                  <n-descriptions-item label="描述">
                    {{ selectedTool.description }}
                  </n-descriptions-item>
                  <n-descriptions-item v-if="selectedTool.annotations" label="注解">
                    <n-space :size="4">
                      <n-tag
                        v-if="selectedTool.annotations.readOnlyHint"
                        size="small"
                        type="success"
                        :bordered="false"
                      >
                        readOnly
                      </n-tag>
                      <n-tag
                        v-if="selectedTool.annotations.destructiveHint"
                        size="small"
                        type="error"
                        :bordered="false"
                      >
                        destructive
                      </n-tag>
                      <n-tag
                        v-if="selectedTool.annotations.idempotentHint"
                        size="small"
                        type="info"
                        :bordered="false"
                      >
                        idempotent
                      </n-tag>
                    </n-space>
                  </n-descriptions-item>
                  <n-descriptions-item
                    v-if="selectedTool.inputSchema?.required?.length"
                    label="必填参数"
                  >
                    <n-space :size="4">
                      <n-tag
                        v-for="r in selectedTool.inputSchema.required"
                        :key="r"
                        size="small"
                        type="warning"
                      >
                        {{ r }}
                      </n-tag>
                    </n-space>
                  </n-descriptions-item>
                </n-descriptions>

                <n-collapse style="margin-top: 12px">
                  <n-collapse-item title="inputSchema" name="schema">
                    <n-code
                      :code="JSON.stringify(selectedTool.inputSchema, null, 2)"
                      language="json"
                      word-wrap
                      style="font-size: 12px; max-height: 240px; overflow: auto"
                    />
                  </n-collapse-item>
                </n-collapse>

                <h4 style="margin-top: 16px; margin-bottom: 6px">参数（JSON）</h4>
                <n-input
                  v-model:value="toolArgsText"
                  type="textarea"
                  :rows="8"
                  placeholder='{"key": "value"}'
                  style="font-family: monospace; font-size: 13px"
                />

                <n-space style="margin-top: 12px" align="center" :size="12">
                  <n-button
                    type="primary"
                    :loading="toolCalling"
                    :disabled="!isAuthenticated"
                    @click="doCallTool"
                  >
                    调用工具
                  </n-button>
                  <n-space align="center" :size="6">
                    <span style="font-size: 12px; color: var(--n-text-color-2)">SSE 流式</span>
                    <n-select
                      v-model:value="sseMode as any"
                      :options="[
                        { label: '关闭', value: 0 },
                        { label: '开启', value: 1 },
                      ]"
                      size="small"
                      style="width: 100px"
                      @update:value="(v: any) => (sseMode = !!v)"
                    />
                    <span v-if="sseMode" class="hint">长耗时工具推送 progress 事件</span>
                  </n-space>
                </n-space>

                <!-- SSE 事件日志 -->
                <div v-if="sseEvents.length" style="margin-top: 12px">
                  <h4 style="margin: 0 0 6px">SSE 事件 ({{ sseEvents.length }})</h4>
                  <div class="sse-log">
                    <div v-for="(ev, idx) in sseEvents" :key="idx" class="sse-line">
                      <n-tag
                        size="tiny"
                        :type="
                          ev.type === 'error'
                            ? 'error'
                            : ev.type === 'progress'
                              ? 'info'
                              : 'success'
                        "
                        :bordered="false"
                      >
                        {{ ev.type }}
                      </n-tag>
                      <span class="sse-data">
                        {{
                          typeof ev.data === 'string'
                            ? ev.data
                            : JSON.stringify(ev.data).slice(0, 200)
                        }}
                      </span>
                    </div>
                  </div>
                </div>

                <!-- 错误 -->
                <n-alert v-if="toolError" type="error" style="margin-top: 12px">
                  {{ toolError }}
                </n-alert>

                <!-- 结果 -->
                <div v-if="toolResult" style="margin-top: 12px">
                  <h4 style="margin: 0 0 6px">结果</h4>
                  <n-code
                    :code="toolResult"
                    language="json"
                    word-wrap
                    style="
                      font-size: 12px;
                      padding: 12px;
                      border-radius: 6px;
                      background: var(--n-color-target, #fafafa);
                      max-height: 400px;
                      overflow: auto;
                    "
                  />
                </div>
              </template>
            </div>
          </div>
        </n-tab-pane>

        <!-- ────────── Resources ────────── -->
        <n-tab-pane name="resources" tab="📦 资源">
          <div class="two-pane">
            <div class="left-pane">
              <div class="pane-header">
                <span>资源列表 ({{ resources.length }})</span>
                <n-button
                  quaternary
                  size="small"
                  :loading="resourcesLoading"
                  @click="loadResources"
                >
                  刷新
                </n-button>
              </div>
              <div v-if="resourcesLoading" class="loading-container">
                <n-spin size="medium" />
              </div>
              <n-empty v-else-if="!resources.length" description="暂无资源" />
              <div v-else class="tool-list">
                <div
                  v-for="r in resources"
                  :key="r.uri"
                  class="tool-item"
                  :class="{ active: selectedResourceUri === r.uri }"
                  @click="readRes(r.uri)"
                >
                  <div class="tool-name">
                    <code>{{ r.uri }}</code>
                    <n-tag v-if="r.mimeType" size="tiny" :bordered="false">{{ r.mimeType }}</n-tag>
                  </div>
                  <div class="tool-desc">{{ r.description || r.name || '—' }}</div>
                </div>
              </div>
            </div>

            <div class="right-pane">
              <div v-if="!selectedResourceUri" class="empty-detail">
                <n-empty description="选择左侧资源查看内容" />
              </div>
              <template v-else>
                <h4 style="margin: 0 0 8px">
                  资源:
                  <code>{{ selectedResourceUri }}</code>
                </h4>
                <div v-if="resourceLoading" class="loading-container">
                  <n-spin size="medium" />
                </div>
                <n-code
                  v-else
                  :code="resourceContent"
                  language="json"
                  word-wrap
                  style="
                    font-size: 12px;
                    padding: 12px;
                    border-radius: 6px;
                    background: var(--n-color-target, #fafafa);
                    max-height: 600px;
                    overflow: auto;
                  "
                />
              </template>
            </div>
          </div>
        </n-tab-pane>

        <!-- ────────── Prompts ────────── -->
        <n-tab-pane name="prompts" tab="💬 Prompt">
          <div class="two-pane">
            <div class="left-pane">
              <div class="pane-header">
                <span>Prompt 模板 ({{ prompts.length }})</span>
                <n-button quaternary size="small" :loading="promptsLoading" @click="loadPrompts">
                  刷新
                </n-button>
              </div>
              <div v-if="promptsLoading" class="loading-container">
                <n-spin size="medium" />
              </div>
              <n-empty v-else-if="!prompts.length" description="暂无 prompt" />
              <div v-else class="tool-list">
                <div
                  v-for="p in prompts"
                  :key="p.name"
                  class="tool-item"
                  :class="{ active: selectedPrompt?.name === p.name }"
                  @click="selectPrompt(p)"
                >
                  <div class="tool-name">
                    <code>{{ p.name }}</code>
                  </div>
                  <div class="tool-desc">{{ p.description || '—' }}</div>
                  <div v-if="p.arguments?.length" class="tool-args">
                    参数: {{ p.arguments.map((a) => a.name).join(', ') }}
                  </div>
                </div>
              </div>
            </div>

            <div class="right-pane">
              <div v-if="!selectedPrompt" class="empty-detail">
                <n-empty description="选择左侧 prompt 模板渲染" />
              </div>
              <template v-else>
                <n-descriptions :column="1" bordered size="small" label-placement="left">
                  <n-descriptions-item label="名称">
                    <code>{{ selectedPrompt.name }}</code>
                  </n-descriptions-item>
                  <n-descriptions-item label="描述">
                    {{ selectedPrompt.description || '—' }}
                  </n-descriptions-item>
                  <n-descriptions-item v-if="selectedPrompt.arguments?.length" label="参数">
                    <n-space :size="4">
                      <n-tag
                        v-for="a in selectedPrompt.arguments"
                        :key="a.name"
                        size="small"
                        :type="a.required ? 'warning' : 'default'"
                      >
                        {{ a.name }}{{ a.required ? '*' : '' }}
                      </n-tag>
                    </n-space>
                  </n-descriptions-item>
                </n-descriptions>

                <h4 style="margin-top: 16px; margin-bottom: 6px">参数（JSON）</h4>
                <n-input
                  v-model:value="promptArgsText"
                  type="textarea"
                  :rows="6"
                  placeholder='{"key": "value"}'
                  style="font-family: monospace; font-size: 13px"
                />

                <n-space style="margin-top: 12px" :size="12">
                  <n-button
                    type="primary"
                    :loading="promptLoading"
                    :disabled="!isAuthenticated"
                    @click="doGetPrompt"
                  >
                    渲染 Prompt
                  </n-button>
                </n-space>

                <div v-if="promptResult" style="margin-top: 12px">
                  <h4 style="margin: 0 0 6px">渲染结果</h4>
                  <n-code
                    :code="promptResult"
                    language="json"
                    word-wrap
                    style="
                      font-size: 12px;
                      padding: 12px;
                      border-radius: 6px;
                      background: var(--n-color-target, #fafafa);
                      max-height: 500px;
                      overflow: auto;
                    "
                  />
                </div>
              </template>
            </div>
          </div>
        </n-tab-pane>
      </n-tabs>
    </n-card>
  </div>
</template>

<style scoped>
.mcp-view {
  max-width: 1600px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 20px;
}

.page-title {
  font-size: 24px;
  font-weight: 600;
  margin: 0 0 8px;
}

.page-desc {
  font-size: 14px;
  color: var(--n-text-color-2, #6b7280);
  margin: 0;
}

.two-pane {
  display: grid;
  grid-template-columns: 360px 1fr;
  gap: 16px;
  min-height: 600px;
}

.left-pane,
.right-pane {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.pane-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-weight: 600;
  font-size: 14px;
}

.loading-container {
  display: flex;
  justify-content: center;
  padding: 60px 0;
}

.tool-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 600px;
  overflow-y: auto;
}

.tool-item {
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.15s;
}

.tool-item:hover {
  background: var(--n-color-target, #f5f5f5);
}

.tool-item.active {
  background: rgba(32, 128, 240, 0.08);
  border-color: rgba(32, 128, 240, 0.3);
}

.tool-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
}

.tool-name code {
  background: var(--n-color-target, #f5f5f5);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
}

.tool-desc {
  font-size: 11px;
  color: var(--n-text-color-2, #6b7280);
  margin-top: 4px;
  line-height: 1.4;
}

.tool-args {
  font-size: 11px;
  color: var(--n-text-color-3, #999);
  margin-top: 2px;
  font-style: italic;
}

.empty-detail {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  padding: 60px 0;
}

.sse-log {
  background: var(--n-color-target, #fafafa);
  border-radius: 6px;
  padding: 8px;
  max-height: 200px;
  overflow-y: auto;
  font-size: 11px;
}

.sse-line {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  padding: 2px 0;
}

.sse-data {
  flex: 1;
  color: var(--n-text-color-2, #6b7280);
  word-break: break-all;
}

.hint {
  font-size: 11px;
  color: var(--n-text-color-3, #999);
  font-style: italic;
}
</style>
