<script setup lang="ts">
import { ref, computed, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { NInput, NButton, NCard, NTag, NSpace, NAlert, NDivider } from 'naive-ui'
import { queryWikiStream } from '@/api/wiki'
import type { WikiQueryResult, ChatHistoryEntry } from '@/api/wiki'
import { renderWikiMarkdown } from '@/utils/wikiRender'
import { computeFeedbackFingerprint, getFeedback, setFeedback as persistFeedback, clearFeedback as removeFeedback, type FeedbackRating } from '@/utils/queryFeedback'
import { getTypeLabel } from '@/utils/format'
import AppIcon from '@/components/common/AppIcon.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingState from '@/components/common/LoadingState.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const router = useRouter()

const question = ref('')
const loading = ref(false)
const asked = ref(false)
const result = ref<WikiQueryResult | null>(null)
/** P1-4: 流式回答增量文本 */
const streamingAnswer = ref('')
/** 流式 abort 控制器 */
let abortController: AbortController | null = null

/** P2-13b: 多轮会话历史（已完成轮次） */
interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
  cited_slugs?: string[]
  recalled_pages?: { slug: string; title: string; type: string; score: number }[]
  insufficient_knowledge?: boolean
  error?: string | null
  /** P2-13a: 该轮的问题原文（assistant 轮用于计算反馈指纹） */
  question?: string
  /** P2-13a: 该轮的反馈指纹（assistant 轮） */
  fingerprint?: string
}
const conversation = ref<ChatTurn[]>([])
/** 会话历史滚动容器引用 */
const conversationRef = ref<HTMLElement | null>(null)

/** P2-13a: 当前轮反馈 rating（从 localStorage 读取，null=未反馈） */
const currentFeedback = ref<FeedbackRating | null>(null)

const hasError = computed(() => !!result.value?.error)
const hasAnswer = computed(
  () => !hasError.value && !!result.value && !result.value.insufficient_knowledge,
)

/** P1-4: 回答渲染为 Markdown（经 DOMPurify sanitize，安全 v-html） */
const renderedAnswer = computed(() =>
  renderWikiMarkdown(result.value?.answer || streamingAnswer.value || ''),
)

/** P2-13a: 当前轮反馈指纹 */
const currentFingerprint = computed(() => {
  if (!result.value) return ''
  return computeFeedbackFingerprint(result.value.question, result.value.cited_slugs)
})

/** P2-13a: 历史轮反馈 rating（按 fingerprint 从 localStorage 读） */
function getTurnFeedback(turn: ChatTurn): FeedbackRating | null {
  if (!turn.fingerprint) return null
  return getFeedback(turn.fingerprint)
}

/** P2-13a: 处理反馈点击 — 同值再点取消，异值切换 */
function handleFeedback(turn: ChatTurn | null, rating: FeedbackRating) {
  const fp = turn?.fingerprint || currentFingerprint.value
  if (!fp) return
  const current = turn ? getTurnFeedback(turn) : currentFeedback.value
  if (current === rating) {
    // 再点同值 → 取消
    removeFeedback(fp)
    if (turn) {
      // 历史轮：触发响应式更新
      conversation.value = [...conversation.value]
    } else {
      currentFeedback.value = null
    }
  } else {
    persistFeedback(fp, rating)
    if (turn) {
      conversation.value = [...conversation.value]
    } else {
      currentFeedback.value = rating
    }
  }
}

/** P2-13b: 把上一轮已完成的结果归档到会话历史 */
function archiveCurrentTurn() {
  if (!result.value) return
  const r = result.value
  const answerText = r.answer || streamingAnswer.value
  // 只归档有实质内容的轮次（有回答或有错误）
  if (answerText || r.error) {
    const fp = currentFingerprint.value
    conversation.value.push({ role: 'user', content: r.question })
    conversation.value.push({
      role: 'assistant',
      content: answerText,
      cited_slugs: r.cited_slugs,
      recalled_pages: r.recalled_pages,
      insufficient_knowledge: r.insufficient_knowledge,
      error: r.error,
      question: r.question,
      fingerprint: fp,
    })
  }
  result.value = null
  streamingAnswer.value = ''
  // P2-13a: 重置当前轮反馈状态
  currentFeedback.value = null
}

/** P2-13b: 构建发给后端的历史（仅 role+content，过滤错误轮次） */
function buildHistory(): ChatHistoryEntry[] {
  return conversation.value
    .filter((t) => t.role === 'user' || (t.role === 'assistant' && t.content && !t.error))
    .map((t) => ({ role: t.role, content: t.content }))
}

function handleQuery() {
  const q = question.value.trim()
  if (!q || loading.value) return
  // 把上一轮归档到会话历史（多轮上下文）
  archiveCurrentTurn()
  const history = buildHistory()

  loading.value = true
  asked.value = true
  streamingAnswer.value = ''
  result.value = {
    question: q,
    answer: '',
    cited_slugs: [],
    recalled_pages: [],
    insufficient_knowledge: false,
    error: null,
  }
  question.value = ''
  void nextTick(scrollToBottom)

  abortController = queryWikiStream(
    q,
    {
      onMeta: (data) => {
        if (result.value) {
          result.value.recalled_pages = data.recalled_pages
          result.value.cited_slugs = data.cited_slugs
          result.value.insufficient_knowledge = data.insufficient_knowledge
          // 知识不足时后端直接给完整 answer
          if (data.insufficient_knowledge && data.answer) {
            result.value.answer = data.answer
          }
        }
      },
      onDelta: (text) => {
        streamingAnswer.value += text
      },
      onDone: () => {
        // 流式结束：把累积文本写入 result.answer
        if (result.value) {
          result.value.answer = streamingAnswer.value
        }
        loading.value = false
        abortController = null
        // P2-13a: 从 localStorage 读已有反馈（同一问题可能反馈过）
        currentFeedback.value = currentFingerprint.value
          ? getFeedback(currentFingerprint.value)
          : null
        void nextTick(scrollToBottom)
      },
      onError: (message) => {
        result.value = {
          question: q,
          answer: '',
          cited_slugs: [],
          recalled_pages: result.value?.recalled_pages || [],
          insufficient_knowledge: false,
          error: message || '查询失败，请稍后重试',
        }
        loading.value = false
        abortController = null
      },
    },
    { history },
  )
}

/** P2-13b: 开启新对话（清空历史） */
function handleNewConversation() {
  if (abortController) handleStop()
  conversation.value = []
  result.value = null
  streamingAnswer.value = ''
  asked.value = false
  question.value = ''
  // P2-13a: 重置当前轮反馈
  currentFeedback.value = null
}

/** P1-4: 取消流式查询 */
function handleStop() {
  if (abortController) {
    abortController.abort()
    abortController = null
    loading.value = false
    if (result.value) {
      result.value.answer = streamingAnswer.value
    }
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault()
    handleQuery()
  }
}

function formatScore(score: number): string {
  return (score * 100).toFixed(1) + '%'
}

function getScoreType(score: number): 'success' | 'info' | 'warning' | 'default' {
  if (score >= 0.8) return 'success'
  if (score >= 0.5) return 'info'
  if (score >= 0.3) return 'warning'
  return 'default'
}

function goToWikiPage(slug: string) {
  router.push({ path: '/wiki', query: { slug } })
}

/** P2-13b: 滚动会话区到底部（新消息可见） */
function scrollToBottom() {
  const el = conversationRef.value
  if (el) el.scrollTop = el.scrollHeight
}

onUnmounted(() => {
  if (abortController) abortController.abort()
})
</script>

<template>
  <div class="wiki-query-view">
    <PageHeader
      title="Wiki 智能问答"
      description="基于已编译的 Wiki 知识库回答问题，回答中引用 [[slug]] 作为来源"
    >
      <template #extra>
        <n-button
          v-if="conversation.length > 0"
          size="small"
          quaternary
          @click="handleNewConversation"
        >
          新对话
        </n-button>
      </template>
    </PageHeader>

    <div class="search-bar">
      <n-input
        v-model:value="question"
        type="textarea"
        :rows="2"
        :placeholder="conversation.length > 0 ? '继续追问，例如：那 504 又是什么？' : '输入你的问题，例如：Nginx 502 错误如何排查？'"
        :disabled="loading"
        class="search-input"
        @keydown="handleKeydown"
      />
      <n-button
        v-if="!loading"
        type="primary"
        size="large"
        :disabled="!question.trim()"
        class="search-btn"
        @click="handleQuery"
      >
        提问
      </n-button>
      <n-button
        v-else
        size="large"
        type="error"
        ghost
        class="search-btn"
        @click="handleStop"
      >
        <template #icon>
          <AppIcon name="stop" />
        </template>
        停止
      </n-button>
    </div>
    <div class="search-tip">
      按 Ctrl + Enter 快速提交{{ loading ? '，流式生成中可随时停止' : '' }}
      <template v-if="conversation.length > 0">· 已有 {{ conversation.filter(t => t.role === 'user').length }} 轮对话上下文</template>
    </div>

    <div ref="conversationRef" class="result-area">
      <!-- P2-13b: 多轮会话历史（已完成轮次） -->
      <template v-for="(turn, idx) in conversation" :key="'turn-' + idx">
        <div v-if="turn.role === 'user'" class="chat-turn chat-turn-user">
          <div class="chat-bubble chat-bubble-user">{{ turn.content }}</div>
        </div>
        <div v-else class="chat-turn chat-turn-assistant">
          <n-card size="small" class="answer-card" :bordered="true">
            <template #header>
              <span class="answer-card-title">回答</span>
            </template>
            <n-alert v-if="turn.error" type="error" title="查询失败" class="result-alert">
              {{ turn.error }}
            </n-alert>
            <n-alert
              v-else-if="turn.insufficient_knowledge"
              type="warning"
              title="知识库不足"
              class="result-alert"
            >
              知识库不足，建议上传相关文档
            </n-alert>
            <div v-else class="markdown-rendered" v-html="renderWikiMarkdown(turn.content)"></div>
            <template v-if="turn.cited_slugs && turn.cited_slugs.length > 0">
              <n-divider title-placement="left" class="section-divider">引用来源</n-divider>
              <div class="cited-list">
                <n-space :size="8" wrap>
                  <n-button
                    v-for="slug in turn.cited_slugs"
                    :key="slug"
                    quaternary
                    size="small"
                    @click="goToWikiPage(slug)"
                  >
                    <template #icon>
                      <AppIcon name="link" />
                    </template>
                    [[{{ slug }}]]
                  </n-button>
                </n-space>
              </div>
            </template>
            <!-- P2-13a: 历史轮反馈 -->
            <div v-if="!turn.error && !turn.insufficient_knowledge" class="feedback-bar">
              <span class="feedback-label">回答有帮助吗？</span>
              <n-button
                size="tiny"
                :type="getTurnFeedback(turn) === 'up' ? 'primary' : 'default'"
                :ghost="getTurnFeedback(turn) === 'up'"
                @click="handleFeedback(turn, 'up')"
              >
                👍 有用
              </n-button>
              <n-button
                size="tiny"
                :type="getTurnFeedback(turn) === 'down' ? 'error' : 'default'"
                :ghost="getTurnFeedback(turn) === 'down'"
                @click="handleFeedback(turn, 'down')"
              >
                👎 无用
              </n-button>
            </div>
          </n-card>
        </div>
      </template>

      <!-- 加载中（流式开始尚未收到首个 delta） -->
      <LoadingState
        v-if="loading && !streamingAnswer && !hasError"
        text="正在编译知识并生成回答..."
        :min-height="200"
      />

      <!-- 空状态：未提问 -->
      <EmptyState
        v-if="!asked && !loading && conversation.length === 0"
        description="输入问题，从 Wiki 知识库中获取结构化解答"
      />

      <!-- 当前轮结果区（流式或完成态） -->
      <div v-if="result" class="result-content chat-turn-assistant">
        <!-- 错误提示 -->
        <n-alert v-if="hasError" type="error" title="查询失败" class="result-alert">
          {{ result.error }}
        </n-alert>

        <template v-else>
          <!-- 知识库不足提示 -->
          <n-alert
            v-if="result.insufficient_knowledge"
            type="warning"
            title="知识库不足"
            class="result-alert"
          >
            知识库不足，建议上传相关文档
          </n-alert>

          <!-- 回答区域（流式或完成态） -->
          <n-card v-if="hasAnswer || (loading && streamingAnswer)" class="answer-card" :bordered="true">
            <template #header>
              <span class="answer-card-title">
                回答
                <n-tag v-if="loading && streamingAnswer" size="tiny" type="info" :bordered="false">
                  生成中…
                </n-tag>
              </span>
            </template>
            <!-- P0-4 + P1-4: v-html 经 renderWikiMarkdown → DOMPurify sanitize，安全渲染 Markdown -->
            <div class="markdown-rendered" v-html="renderedAnswer"></div>
          </n-card>

          <!-- 引用来源 -->
          <template v-if="hasAnswer && result.cited_slugs.length > 0">
            <n-divider title-placement="left" class="section-divider">引用来源</n-divider>
            <div class="cited-list">
              <n-space :size="8" wrap>
                <n-button
                  v-for="slug in result.cited_slugs"
                  :key="slug"
                  quaternary
                  size="small"
                  @click="goToWikiPage(slug)"
                >
                  <template #icon>
                    <AppIcon name="link" />
                  </template>
                  [[{{ slug }}]]
                </n-button>
              </n-space>
            </div>
          </template>

          <!-- 召回页面 -->
          <template v-if="hasAnswer && result.recalled_pages.length > 0">
            <n-divider title-placement="left" class="section-divider">召回页面</n-divider>
            <n-space vertical :size="10" class="recalled-list">
              <n-card
                v-for="page in result.recalled_pages"
                :key="page.slug"
                size="small"
                hoverable
                class="recalled-card"
                @click="goToWikiPage(page.slug)"
              >
                <div class="recalled-row">
                  <div class="recalled-info">
                    <span class="recalled-title">{{ page.title }}</span>
                    <n-tag size="small" type="info" class="recalled-type">
                      {{ getTypeLabel(page.type) }}
                    </n-tag>
                  </div>
                  <n-tag :type="getScoreType(page.score)" size="small">
                    {{ formatScore(page.score) }}
                  </n-tag>
                </div>
                <div class="recalled-slug">{{ page.slug }}</div>
              </n-card>
            </n-space>
          </template>

          <!-- P2-13a: 当前轮反馈（回答完成后显示） -->
          <div v-if="hasAnswer && !loading" class="feedback-bar current-feedback">
            <span class="feedback-label">回答有帮助吗？</span>
            <n-button
              size="tiny"
              :type="currentFeedback === 'up' ? 'primary' : 'default'"
              :ghost="currentFeedback === 'up'"
              @click="handleFeedback(null, 'up')"
            >
              👍 有用
            </n-button>
            <n-button
              size="tiny"
              :type="currentFeedback === 'down' ? 'error' : 'default'"
              :ghost="currentFeedback === 'down'"
              @click="handleFeedback(null, 'down')"
            >
              👎 无用
            </n-button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wiki-query-view {
  max-width: 900px;
  margin: 0 auto;
}

.search-bar {
  display: flex;
  gap: 12px;
  align-items: stretch;
  margin-bottom: 8px;
}

.search-input {
  flex: 1;
}

.search-btn {
  min-width: 100px;
  align-self: stretch;
}

.search-tip {
  font-size: 12px;
  color: var(--n-text-color-3, #9ca3af);
  margin-bottom: 28px;
}

.result-area {
  min-height: 300px;
}

.result-content {
  width: 100%;
}

.result-alert {
  margin-bottom: 16px;
}

.answer-card {
  margin-bottom: 8px;
}

.answer-card-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

/* P1-4: Markdown 渲染样式（与全局 markdown-rendered 对齐） */
.markdown-rendered {
  font-size: 15px;
  line-height: 1.8;
  color: var(--n-text-color, #111827);
  word-break: break-word;
}

.markdown-rendered :deep(h1),
.markdown-rendered :deep(h2),
.markdown-rendered :deep(h3) {
  margin: 1em 0 0.5em;
  font-weight: 600;
}

.markdown-rendered :deep(p) {
  margin: 0.5em 0;
}

.markdown-rendered :deep(ul),
.markdown-rendered :deep(ol) {
  padding-left: 1.5em;
  margin: 0.5em 0;
}

.markdown-rendered :deep(li) {
  margin: 0.25em 0;
}

.markdown-rendered :deep(pre) {
  padding: 12px;
  background: var(--n-code-color, rgba(0, 0, 0, 0.05));
  border-radius: 6px;
  overflow-x: auto;
}

.markdown-rendered :deep(code) {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 0.9em;
}

.markdown-rendered :deep(a) {
  color: var(--n-primary-color, #2080f0);
  text-decoration: none;
}

.markdown-rendered :deep(a:hover) {
  text-decoration: underline;
}

.markdown-rendered :deep(table) {
  border-collapse: collapse;
  margin: 0.5em 0;
}

.markdown-rendered :deep(th),
.markdown-rendered :deep(td) {
  border: 1px solid var(--n-border-color, #e5e7eb);
  padding: 6px 12px;
}

.markdown-rendered :deep(blockquote) {
  margin: 0.5em 0;
  padding-left: 1em;
  border-left: 3px solid var(--n-border-color, #e5e7eb);
  color: var(--n-text-color-2, #6b7280);
}

.section-divider {
  margin-top: 24px;
  margin-bottom: 16px;
}

.section-divider :deep(.n-divider__title) {
  font-size: 14px;
  font-weight: 600;
  color: var(--n-text-color-2, #6b7280);
}

.cited-list {
  padding: 0 4px;
}

.recalled-list {
  width: 100%;
}

.recalled-card {
  cursor: pointer;
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease;
}

.recalled-card:hover {
  transform: translateY(-1px);
}

.recalled-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.recalled-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.recalled-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.recalled-type {
  flex-shrink: 0;
}

.recalled-slug {
  margin-top: 6px;
  font-size: 12px;
  color: var(--n-text-color-3, #9ca3af);
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}

/* P2-13b: 多轮会话历史样式 */
.chat-turn {
  margin-bottom: 16px;
}

.chat-turn-user {
  display: flex;
  justify-content: flex-end;
}

.chat-turn-assistant {
  width: 100%;
}

.chat-bubble {
  max-width: 75%;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
  white-space: pre-wrap;
}

.chat-bubble-user {
  background: var(--n-primary-color, #2080f0);
  color: #fff;
  border-bottom-right-radius: 4px;
}

/* P2-13a: 答案反馈条 */
.feedback-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--n-border-color, #e5e7eb);
}

.feedback-label {
  font-size: 12px;
  color: var(--n-text-color-3, #9ca3af);
  margin-right: 4px;
}

.current-feedback {
  margin-top: 20px;
}
</style>
