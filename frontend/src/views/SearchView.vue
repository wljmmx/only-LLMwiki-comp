<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import DOMPurify from 'dompurify'
import { searchKnowledge } from '@/api/search'
import type { SearchResponse, SearchResult, SearchSuggestions } from '@/types/api'

const router = useRouter()
const query = ref('')
const loading = ref(false)
const searched = ref(false)
const results = ref<SearchResult[]>([])
const total = ref(0)
const suggestions = ref<SearchSuggestions | null>(null)

function handleSearch() {
  if (!query.value.trim()) return
  loading.value = true
  searched.value = true
  suggestions.value = null
  searchKnowledge(query.value.trim())
    .then((res: SearchResponse) => {
      results.value = res.results
      total.value = res.count
      suggestions.value = res.suggestions ?? null
    })
    .catch(() => {
      results.value = []
      total.value = 0
      suggestions.value = null
    })
    .finally(() => {
      loading.value = false
    })
}

// P2-1.6：用建议查询触发新搜索
function searchWith(newQuery: string) {
  if (!newQuery.trim()) return
  query.value = newQuery.trim()
  handleSearch()
}

function goToDocuments() {
  router.push('/documents')
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    handleSearch()
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

// P1-15：转义 HTML 实体，防止 snippet 中的 HTML 被当作标签解析（XSS 防护第一道）
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/**
 * P1-15：在 snippet 中用 <mark> 标签包裹匹配 query 的关键词
 *
 * XSS 防护双重保障：
 * 1. 先 escape HTML 实体 → snippet 退化为纯文本，无法注入标签
 * 2. 插入 mark 后再用 DOMPurify 清洗 → 兜底移除任何残留危险内容
 *
 * 单次合并替换：把所有 term 合成一个正则一次匹配，避免重复替换时
 * 误伤已插入的 <mark class="search-hit"> 标签属性。
 */
function highlightSnippet(snippet: string, q: string): string {
  if (!snippet) return ''
  const escaped = escapeHtml(snippet)
  const trimmedQuery = q.trim()
  if (!trimmedQuery) return escaped
  // 按空格分词，过滤空 term
  const terms = trimmedQuery.split(/\s+/).filter(Boolean)
  if (terms.length === 0) return escaped
  // 转义每个 term 的正则元字符，避免破坏正则
  const safeTerms = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  // 合并为单个正则，大小写不敏感，一次替换所有匹配
  const combined = new RegExp(`(${safeTerms.join('|')})`, 'gi')
  const highlighted = escaped.replace(combined, '<mark class="search-hit">$1</mark>')
  // DOMPurify 兜底清洗（XSS 防护第二道），mark 与 class 默认放行
  return DOMPurify.sanitize(highlighted)
}

/**
 * P1-15：点击搜索结果卡片跳转
 * - 有 doc_id → 跳转文档管理并定位该文档
 * - 有 slug（wiki 类型）→ 跳转 Wiki 浏览并选中该页面
 * - 兜底 → 跳转文档管理
 */
function handleResultClick(item: SearchResult): void {
  if (item.doc_id) {
    router.push({ path: '/documents', query: { doc_id: item.doc_id } })
    return
  }
  if (item.slug) {
    router.push({ path: '/wiki', query: { slug: item.slug } })
    return
  }
  router.push('/documents')
}
</script>

<template>
  <div class="search-view">
    <div class="search-bar">
      <n-input
        v-model:value="query"
        size="large"
        placeholder="输入关键词搜索知识库..."
        clearable
        class="search-input"
        @keydown="handleKeydown"
      >
        <template #prefix>
          <span style="font-size: 18px">🔍</span>
        </template>
      </n-input>
      <n-button
        type="primary"
        size="large"
        :loading="loading"
        class="search-btn"
        @click="handleSearch"
      >
        搜索
      </n-button>
    </div>

    <div class="search-results">
      <div v-if="loading" class="loading-container">
        <n-spin size="large" />
      </div>

      <div v-else-if="!searched" class="empty-state">
        <div class="empty-icon">📚</div>
        <div class="empty-title">知识搜索</div>
        <div class="empty-desc">输入关键词，在知识库中快速查找相关文档和实体</div>
      </div>

      <div v-else>
        <div v-if="total > 0" class="results-header">
          <span class="results-count">
            找到
            <strong>{{ total }}</strong>
            条结果
          </span>
        </div>

        <n-space vertical :size="16" class="results-list">
          <n-card
            v-for="item in results"
            :key="item.id"
            hoverable
            class="result-card"
            tabindex="0"
            role="button"
            :aria-label="`打开搜索结果：${item.title}`"
            @click="handleResultClick(item)"
            @keydown.enter="handleResultClick(item)"
          >
            <div class="card-header">
              <span class="result-title">{{ item.title }}</span>
              <n-tag :type="getScoreType(item.score)" size="small">
                匹配度 {{ formatScore(item.score) }}
              </n-tag>
            </div>
            <p class="result-snippet" v-html="highlightSnippet(item.snippet, query)"></p>
            <div class="card-footer">
              <n-tag size="small" type="info">
                {{ item.type }}
              </n-tag>
            </div>
          </n-card>
        </n-space>

        <div v-if="total === 0" class="empty-wrapper">
          <n-card v-if="suggestions" class="suggestions-card" bordered>
            <div class="suggestions-diagnosis">
              <span class="diagnosis-icon">!</span>
              <span>{{ suggestions.diagnosis }}</span>
            </div>
            <div v-if="suggestions.did_you_mean" class="suggestions-section">
              <span class="section-label">是否要找：</span>
              <n-button
                size="small"
                type="primary"
                tertiary
                @click="searchWith(suggestions.did_you_mean!)"
              >
                {{ suggestions.did_you_mean }}
              </n-button>
            </div>
            <div v-if="suggestions.similar_queries.length" class="suggestions-section">
              <span class="section-label">相关搜索：</span>
              <n-space :size="8" align="center">
                <n-tag
                  v-for="sq in suggestions.similar_queries"
                  :key="sq"
                  checkable
                  size="medium"
                  class="similar-tag"
                  @click="searchWith(sq)"
                >
                  {{ sq }}
                </n-tag>
              </n-space>
            </div>
            <div class="suggestions-section upload-section">
              <span class="section-label">{{ suggestions.upload_hint }}</span>
              <n-button size="small" type="info" tertiary @click="goToDocuments">
                前往上传
              </n-button>
            </div>
          </n-card>
          <n-empty v-else description="没有找到相关结果" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-view {
  max-width: 900px;
  margin: 0 auto;
}

.search-bar {
  display: flex;
  gap: 12px;
  margin-bottom: 32px;
  align-items: center;
}

.search-input {
  flex: 1;
}

.search-btn {
  min-width: 100px;
}

.loading-container {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 80px 0;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 20px;
  text-align: center;
}

.empty-icon {
  font-size: 64px;
  margin-bottom: 20px;
}

.empty-title {
  font-size: 24px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
  margin-bottom: 8px;
}

.empty-desc {
  font-size: 14px;
  color: var(--n-text-color-2, #6b7280);
}

.results-header {
  margin-bottom: 20px;
  font-size: 14px;
  color: var(--n-text-color-2, #6b7280);
}

.results-count strong {
  color: var(--n-text-color, #111827);
  font-weight: 600;
}

.results-list {
  width: 100%;
}

.result-card {
  cursor: pointer;
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease;
}

.result-card:hover {
  transform: translateY(-2px);
}

/* P1-15：键盘聚焦可见，辅助键盘导航 */
.result-card:focus-visible {
  outline: 2px solid var(--opskg-color-primary);
  outline-offset: 2px;
}

/* P1-15：关键词高亮 —— v-html 内容需用 :deep() 穿透 scoped 限制 */
.result-snippet :deep(.search-hit) {
  background: var(--opskg-color-warning);
  padding: 0 2px;
  border-radius: 2px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}

.result-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.result-snippet {
  font-size: 14px;
  color: var(--n-text-color-2, #6b7280);
  line-height: 1.6;
  margin: 0 0 16px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-footer {
  display: flex;
  align-items: center;
}

.empty-wrapper {
  padding: 60px 0;
}

.suggestions-card {
  max-width: 600px;
  margin: 0 auto;
}

.suggestions-diagnosis {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 14px;
  color: var(--n-text-color, #111827);
  margin-bottom: 20px;
  line-height: 1.6;
}

.diagnosis-icon {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--n-warning-color, #f0a020);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
}

.suggestions-section {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
  font-size: 13px;
}

.suggestions-section:last-child {
  margin-bottom: 0;
}

.section-label {
  color: var(--n-text-color-2, #6b7280);
}

.similar-tag {
  cursor: pointer;
}

.upload-section {
  padding-top: 12px;
  border-top: 1px solid var(--n-divider-color, #e5e7eb);
}
</style>
