<script setup lang="ts">
import DOMPurify from 'dompurify'
import { NButton, NTag, NSpace, NCard, NEmpty } from 'naive-ui'
import { getTypeLabel } from '@/utils/format'
import LoadingState from '@/components/common/LoadingState.vue'
import type { SearchResult, SearchSuggestions } from '@/types/api'

const props = defineProps<{
  loading: boolean
  searched: boolean
  results: SearchResult[]
  total: number
  suggestions: SearchSuggestions | null
  query: string
}>()

const emit = defineEmits<{
  'result-click': [item: SearchResult]
  'search-with': [query: string]
  'go-to-documents': []
}>()

function formatScore(score: number): string {
  return (score * 100).toFixed(1) + '%'
}

function getScoreType(score: number): 'success' | 'info' | 'warning' | 'default' {
  if (score >= 0.8) return 'success'
  if (score >= 0.5) return 'info'
  if (score >= 0.3) return 'warning'
  return 'default'
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function highlightSnippet(snippet: string, q: string): string {
  if (!snippet) return ''
  const escaped = escapeHtml(snippet)
  const trimmedQuery = q.trim()
  if (!trimmedQuery) return escaped
  const terms = trimmedQuery.split(/\s+/).filter(Boolean)
  if (terms.length === 0) return escaped
  const safeTerms = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const combined = new RegExp(`(${safeTerms.join('|')})`, 'gi')
  const highlighted = escaped.replace(combined, '<mark class="search-hit">$1</mark>')
  return DOMPurify.sanitize(highlighted)
}

function handleResultClick(item: SearchResult): void {
  emit('result-click', item)
}
</script>

<template>
  <div class="search-results">
    <LoadingState v-if="loading" />

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

      <NSpace vertical :size="16" class="results-list">
        <NCard
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
            <NTag :type="getScoreType(item.score)" size="small">
              匹配度 {{ formatScore(item.score) }}
            </NTag>
          </div>
          <p class="result-snippet" v-html="highlightSnippet(item.snippet, query)"></p>
          <div class="card-footer">
            <NTag size="small" type="info">
              {{ getTypeLabel(item.type) }}
            </NTag>
          </div>
        </NCard>
      </NSpace>

      <div v-if="total === 0" class="empty-wrapper">
        <NCard v-if="suggestions" class="suggestions-card" bordered>
          <div class="suggestions-diagnosis">
            <span class="diagnosis-icon">!</span>
            <span>{{ suggestions.diagnosis }}</span>
          </div>
          <div v-if="suggestions.did_you_mean" class="suggestions-section">
            <span class="section-label">是否要找：</span>
            <NButton
              size="small"
              type="primary"
              tertiary
              @click="emit('search-with', suggestions.did_you_mean!)"
            >
              {{ suggestions.did_you_mean }}
            </NButton>
          </div>
          <div v-if="suggestions.similar_queries.length" class="suggestions-section">
            <span class="section-label">相关搜索：</span>
            <NSpace :size="8" align="center">
              <NTag
                v-for="sq in suggestions.similar_queries"
                :key="sq"
                checkable
                size="medium"
                class="similar-tag"
                @click="emit('search-with', sq)"
              >
                {{ sq }}
              </NTag>
            </NSpace>
          </div>
          <div class="suggestions-section upload-section">
            <span class="section-label">{{ suggestions.upload_hint }}</span>
            <NButton size="small" type="info" tertiary @click="emit('go-to-documents')">
              前往上传
            </NButton>
          </div>
        </NCard>
        <NEmpty v-else description="没有找到相关结果" />
      </div>
    </div>
  </div>
</template>

<style scoped>
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

.result-card:focus-visible {
  outline: 2px solid var(--opskg-color-primary);
  outline-offset: 2px;
}

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