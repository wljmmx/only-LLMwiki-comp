<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NInput,
  NButton,
  NCard,
  NTag,
  NSpace,
  NSpin,
  NEmpty,
  NAlert,
  NDivider,
} from 'naive-ui'
import { queryWiki } from '@/api/wiki'
import type { WikiQueryResult } from '@/api/wiki'

const router = useRouter()

const question = ref('')
const loading = ref(false)
const asked = ref(false)
const result = ref<WikiQueryResult | null>(null)

const hasError = computed(() => !!result.value?.error)
const hasAnswer = computed(
  () => !hasError.value && !!result.value && !result.value.insufficient_knowledge
)

function handleQuery() {
  const q = question.value.trim()
  if (!q || loading.value) return
  loading.value = true
  asked.value = true
  queryWiki(q)
    .then((res) => {
      result.value = res
    })
    .catch((err) => {
      result.value = {
        question: q,
        answer: '',
        cited_slugs: [],
        recalled_pages: [],
        insufficient_knowledge: false,
        error: err?.message || '查询失败，请稍后重试',
      }
    })
    .finally(() => {
      loading.value = false
    })
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
</script>

<template>
  <div class="wiki-query-view">
    <div class="query-header">
      <h2 class="page-title">Wiki 智能问答</h2>
      <p class="page-desc">基于已编译的 Wiki 知识库回答问题，回答中引用 [[slug]] 作为来源</p>
    </div>

    <div class="search-bar">
      <n-input
        v-model:value="question"
        type="textarea"
        :rows="2"
        placeholder="输入你的问题，例如：Nginx 502 错误如何排查？"
        :disabled="loading"
        @keydown="handleKeydown"
        class="search-input"
      />
      <n-button
        type="primary"
        size="large"
        :loading="loading"
        :disabled="!question.trim()"
        @click="handleQuery"
        class="search-btn"
      >
        提问
      </n-button>
    </div>
    <div class="search-tip">按 Ctrl + Enter 快速提交</div>

    <div class="result-area">
      <!-- 加载中 -->
      <div v-if="loading" class="loading-container">
        <n-spin size="large" />
        <div class="loading-text">正在编译知识并生成回答...</div>
      </div>

      <!-- 空状态：未提问 -->
      <div v-else-if="!asked" class="empty-wrapper">
        <n-empty description="输入问题，从 Wiki 知识库中获取结构化解答">
          <template #icon>
            <span style="font-size: 48px;">💬</span>
          </template>
        </n-empty>
      </div>

      <!-- 结果区 -->
      <div v-else-if="result" class="result-content">
        <!-- 错误提示 -->
        <n-alert
          v-if="hasError"
          type="error"
          title="查询失败"
          class="result-alert"
        >
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

          <!-- 回答区域 -->
          <n-card
            v-if="hasAnswer"
            title="回答"
            class="answer-card"
            :bordered="true"
          >
            <div class="answer-text">{{ result.answer }}</div>
          </n-card>

          <!-- 引用来源 -->
          <template v-if="hasAnswer && result.cited_slugs.length > 0">
            <n-divider title-placement="left" class="section-divider">
              引用来源
            </n-divider>
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
                    <span>🔗</span>
                  </template>
                  [[{{ slug }}]]
                </n-button>
              </n-space>
            </div>
          </template>

          <!-- 召回页面 -->
          <template v-if="hasAnswer && result.recalled_pages.length > 0">
            <n-divider title-placement="left" class="section-divider">
              召回页面
            </n-divider>
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
                      {{ page.type }}
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

.query-header {
  margin-bottom: 24px;
}

.page-title {
  font-size: 24px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
  margin: 0 0 8px;
}

.page-desc {
  font-size: 14px;
  color: var(--n-text-color-2, #6b7280);
  margin: 0;
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

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  gap: 16px;
}

.loading-text {
  font-size: 14px;
  color: var(--n-text-color-2, #6b7280);
}

.empty-wrapper {
  padding: 80px 0;
  display: flex;
  justify-content: center;
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

.answer-text {
  font-size: 15px;
  line-height: 1.8;
  color: var(--n-text-color, #111827);
  white-space: pre-wrap;
  word-break: break-word;
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
  transition: transform 0.2s ease, box-shadow 0.2s ease;
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
</style>
