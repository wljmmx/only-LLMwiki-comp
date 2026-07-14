<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NForm,
  NFormItem,
  NInput,
  NButton,
  NSwitch,
  NTag,
  NSpace,
  NSpin,
  NEmpty,
  NAlert,
  NDivider,
  NInputNumber,
  useMessage,
} from 'naive-ui'
import { generateRunbook, type RunbookGenerateResult } from '@/api/aiops'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingState from '@/components/common/LoadingState.vue'
import { renderWikiMarkdown } from '@/utils/wikiRender'

const router = useRouter()
const message = useMessage()

const symptom = ref('')
const service = ref('')
const host = ref('')
const maxDocs = ref(5)
const publish = ref(false)

const loading = ref(false)
const result = ref<RunbookGenerateResult | null>(null)
const errorMsg = ref('')

const renderedRunbook = computed(() => {
  if (!result.value?.runbook_md) return ''
  return renderWikiMarkdown(result.value.runbook_md)
})

function handleGenerate() {
  const s = symptom.value.trim()
  if (!s || loading.value) return
  loading.value = true
  errorMsg.value = ''
  result.value = null
  generateRunbook({
    symptom: s,
    service: service.value.trim(),
    host: host.value.trim(),
    max_docs: maxDocs.value,
    publish: publish.value,
  })
    .then((res) => {
      result.value = res
      if (res.wiki_published && res.wiki_slug) {
        message.success(`已发布为 Wiki: ${res.wiki_slug}`)
      } else {
        message.success('Runbook 生成成功')
      }
    })
    .catch((err) => {
      errorMsg.value = err?.response?.data?.detail || err?.message || '生成失败'
      message.error(errorMsg.value)
    })
    .finally(() => {
      loading.value = false
    })
}

function goToWiki(slug: string) {
  router.push({ path: '/wiki', query: { slug } })
}
</script>

<template>
  <div class="runbook-view">
    <PageHeader
      title="Runbook 工作台"
      description="基于知识库自动生成故障处理 Runbook，可选发布为 Wiki"
    />

    <n-card title="生成参数" :bordered="true" class="form-card">
      <n-form label-placement="top">
        <n-form-item label="故障现象（必填）">
          <n-input
            v-model:value="symptom"
            type="textarea"
            :rows="3"
            placeholder="例如：Nginx 502 Bad Gateway，上游服务无响应"
            :disabled="loading"
          />
        </n-form-item>
        <n-space :size="12">
          <n-form-item label="受影响服务" style="flex: 1; min-width: 200px">
            <n-input
              v-model:value="service"
              placeholder="可选，如 nginx / order-service"
              :disabled="loading"
            />
          </n-form-item>
          <n-form-item label="受影响主机" style="flex: 1; min-width: 200px">
            <n-input v-model:value="host" placeholder="可选，如 web-prod-01" :disabled="loading" />
          </n-form-item>
        </n-space>
        <n-space :size="12" align="center">
          <n-form-item label="检索文档数上限">
            <n-input-number v-model:value="maxDocs" :min="1" :max="20" :disabled="loading" />
          </n-form-item>
          <n-form-item label="发布为 Wiki">
            <n-switch v-model:value="publish" :disabled="loading" />
          </n-form-item>
          <n-form-item>
            <n-button
              type="primary"
              size="large"
              :loading="loading"
              :disabled="!symptom.trim()"
              @click="handleGenerate"
            >
              生成 Runbook
            </n-button>
          </n-form-item>
        </n-space>
      </n-form>
    </n-card>

    <div class="result-area">
      <LoadingState v-if="loading" />

      <div v-else-if="errorMsg" class="error-wrapper">
        <n-alert type="error" title="生成失败">{{ errorMsg }}</n-alert>
      </div>

      <div v-else-if="!result" class="empty-wrapper">
        <n-empty description="填写故障现象，点击「生成 Runbook」开始">
          <template #icon>
            <span style="font-size: 48px">🛠️</span>
          </template>
        </n-empty>
      </div>

      <template v-else>
        <n-card
          v-if="result.wiki_published && result.wiki_slug"
          class="publish-banner"
          :bordered="true"
        >
          <n-space align="center">
            <span>✅ 已发布为 Wiki：</span>
            <n-button quaternary size="small" @click="goToWiki(result.wiki_slug!)">
              [[{{ result.wiki_slug }}]]
            </n-button>
          </n-space>
        </n-card>

        <n-card
          v-if="result.sources?.length"
          title="引用来源"
          :bordered="true"
          class="sources-card"
        >
          <n-space vertical :size="8">
            <div v-for="src in result.sources" :key="src.doc_id" class="source-item">
              <n-space align="center" :size="8">
                <n-tag size="small" type="info">{{ src.doc_id.slice(0, 8) }}</n-tag>
                <span class="source-title">{{ src.title }}</span>
                <n-tag v-if="src.score != null" size="small">
                  相似度 {{ (src.score * 100).toFixed(1) }}%
                </n-tag>
              </n-space>
              <div v-if="src.snippet" class="source-snippet">{{ src.snippet }}</div>
            </div>
          </n-space>
        </n-card>

        <n-divider title-placement="left" class="section-divider">Runbook 内容</n-divider>
        <n-card :bordered="true" class="runbook-content-card">
          <div class="markdown-rendered" v-html="renderedRunbook" />
        </n-card>
      </template>
    </div>
  </div>
</template>

<style scoped>
.runbook-view {
  max-width: 1000px;
  margin: 0 auto;
}

.form-card {
  margin-bottom: 24px;
}

.result-area {
  min-height: 300px;
  margin-top: 16px;
}

.empty-wrapper {
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

.publish-banner {
  margin-bottom: 16px;
  background: var(--n-color-success, rgba(0, 200, 100, 0.08));
}

.sources-card {
  margin-bottom: 16px;
}

.source-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--n-border-color, #eee);
}

.source-item:last-child {
  border-bottom: none;
}

.source-title {
  font-weight: 600;
  font-size: 14px;
}

.source-snippet {
  margin-top: 4px;
  font-size: 12px;
  color: var(--n-text-color-3, #9ca3af);
  white-space: pre-wrap;
}

.section-divider {
  margin-top: 24px;
  margin-bottom: 16px;
}

.runbook-content-card {
  min-height: 200px;
}

.markdown-rendered {
  font-size: 14px;
  line-height: 1.8;
  color: var(--n-text-color, #111827);
}

.markdown-rendered :deep(h1),
.markdown-rendered :deep(h2),
.markdown-rendered :deep(h3) {
  margin: 16px 0 8px;
  font-weight: 600;
}

.markdown-rendered :deep(pre) {
  background: var(--n-code-color, #f5f5f5);
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
}

.markdown-rendered :deep(code) {
  background: var(--n-code-color, #f5f5f5);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
}

.markdown-rendered :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 12px 0;
}

.markdown-rendered :deep(th),
.markdown-rendered :deep(td) {
  border: 1px solid var(--n-border-color, #ddd);
  padding: 6px 12px;
  text-align: left;
}

.markdown-rendered :deep(a) {
  color: var(--n-color-primary, #2080f0);
  text-decoration: none;
}

.markdown-rendered :deep(a:hover) {
  text-decoration: underline;
}
</style>
