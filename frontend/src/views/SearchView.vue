<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
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
          <n-card v-for="item in results" :key="item.id" hoverable class="result-card">
            <div class="card-header">
              <span class="result-title">{{ item.title }}</span>
              <n-tag :type="getScoreType(item.score)" size="small">
                匹配度 {{ formatScore(item.score) }}
              </n-tag>
            </div>
            <p class="result-snippet">{{ item.snippet }}</p>
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
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease;
}

.result-card:hover {
  transform: translateY(-2px);
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
