<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { searchKnowledge } from '@/api/search'
import type { SearchResponse, SearchResult, SearchSuggestions } from '@/types/api'
import SearchBar from '@/components/search/SearchBar.vue'
import SearchResults from '@/components/search/SearchResults.vue'

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

function searchWith(newQuery: string) {
  if (!newQuery.trim()) return
  query.value = newQuery.trim()
  handleSearch()
}

function goToDocuments() {
  router.push('/documents')
}

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
    <SearchBar
      v-model:query="query"
      :loading="loading"
      @search="handleSearch"
    />
    <SearchResults
      :loading="loading"
      :searched="searched"
      :results="results"
      :total="total"
      :suggestions="suggestions"
      :query="query"
      @result-click="handleResultClick"
      @search-with="searchWith"
      @go-to-documents="goToDocuments"
    />
  </div>
</template>

<style scoped>
.search-view {
  max-width: 900px;
  margin: 0 auto;
}
</style>