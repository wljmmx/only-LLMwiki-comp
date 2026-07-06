<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  NCard,
  NTag,
  NSpace,
  NButton,
  NSpin,
  NEmpty,
  NInput,
  NDrawer,
  NDrawerContent,
  NDescriptions,
  NDescriptionsItem,
  NModal,
  NSelect,
  NAlert,
  NCode,
  NPopconfirm,
  useMessage,
} from 'naive-ui'
import {
  listVersions,
  getVersion,
  diffVersions,
  rollbackVersion,
  listWikiDocs,
  type VersionMeta,
  type VersionDetail,
  type WikiDocSummary,
} from '@/api/versions'
import { getAuthToken } from '@/api/index'

const message = useMessage()

const isAuthenticated = computed(() => !!getAuthToken())

// doc_key 输入与列表
const docKeyInput = ref<string>('wiki:')
const currentDocKey = ref<string>('')
const loading = ref(false)
const versions = ref<VersionMeta[]>([])

// Wiki 文档列表（用于快速选 doc_key）
const wikiDocs = ref<WikiDocSummary[]>([])
const wikiDocsLoading = ref(false)

// 详情抽屉
const detailVisible = ref(false)
const detailLoading = ref(false)
const selectedVersion = ref<VersionDetail | null>(null)

// Diff 弹窗
const diffVisible = ref(false)
const diffLoading = ref(false)
const diffV1 = ref<number | null>(null)
const diffV2 = ref<number | null>(null)
const diffResult = ref<string>('')
const diffStats = ref<{ added: number; removed: number } | null>(null)
const diffError = ref<string>('')

// 内容预览弹窗
const contentVisible = ref(false)
const contentLoading = ref(false)
const contentPreview = ref<VersionDetail | null>(null)

const versionOptions = computed(() =>
  versions.value.map((v) => ({ label: `v${v.version}`, value: v.version })),
)

async function loadVersions() {
  const key = docKeyInput.value.trim()
  if (!key) {
    message.warning('请输入 doc_key')
    return
  }
  loading.value = true
  currentDocKey.value = key
  try {
    const res = await listVersions(key)
    versions.value = res.versions || []
    if (versions.value.length === 0) {
      message.info(`未找到 ${key} 的版本记录`)
    }
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '加载版本失败')
    versions.value = []
  } finally {
    loading.value = false
  }
}

async function loadWikiDocs() {
  wikiDocsLoading.value = true
  try {
    const res = await listWikiDocs(100, 0)
    wikiDocs.value = res.documents || []
  } catch (err: any) {
    // 静默失败，仅控制台
    console.warn('加载 Wiki 文档列表失败', err)
  } finally {
    wikiDocsLoading.value = false
  }
}

function selectWikiDoc(doc: WikiDocSummary) {
  docKeyInput.value = `wiki:${doc.slug}`
  loadVersions()
}

async function openContent(version: VersionMeta) {
  contentVisible.value = true
  contentLoading.value = true
  contentPreview.value = null
  try {
    const full = await getVersion(currentDocKey.value, version.version)
    contentPreview.value = full
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '加载内容失败')
  } finally {
    contentLoading.value = false
  }
}

async function openDetail(version: VersionMeta) {
  detailVisible.value = true
  detailLoading.value = true
  selectedVersion.value = null
  try {
    const full = await getVersion(currentDocKey.value, version.version)
    selectedVersion.value = full
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

function openDiff(prefillV1?: number, prefillV2?: number) {
  diffVisible.value = true
  diffResult.value = ''
  diffStats.value = null
  diffError.value = ''
  if (prefillV1 != null && prefillV2 != null) {
    diffV1.value = prefillV1
    diffV2.value = prefillV2
    // 自动执行 diff
    doDiff()
  } else {
    diffV1.value = versions.value[versions.value.length - 1]?.version ?? null
    diffV2.value = versions.value[0]?.version ?? null
  }
}

async function doDiff() {
  if (diffV1.value == null || diffV2.value == null) {
    message.warning('请选择两个版本')
    return
  }
  if (diffV1.value === diffV2.value) {
    message.warning('请选择不同的版本')
    return
  }
  diffLoading.value = true
  diffError.value = ''
  diffResult.value = ''
  diffStats.value = null
  try {
    const res = await diffVersions(currentDocKey.value, diffV1.value, diffV2.value)
    if (res.error) {
      diffError.value = res.error
    } else {
      diffResult.value = res.diff
      diffStats.value = { added: res.added_lines, removed: res.removed_lines }
    }
  } catch (err: any) {
    diffError.value = err?.response?.data?.detail || err?.message || 'Diff 失败'
  } finally {
    diffLoading.value = false
  }
}

async function handleRollback(targetVersion: number) {
  if (!isAuthenticated.value) {
    message.error('回滚需要登录 token')
    return
  }
  try {
    const res = await rollbackVersion(currentDocKey.value, targetVersion)
    if (res.skipped) {
      message.warning(`回滚跳过: ${res.reason}`)
    } else {
      message.success(`已回滚到 v${targetVersion}（创建为新版本 v${res.version}）`)
      detailVisible.value = false
      await loadVersions()
    }
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '回滚失败')
  }
}

function formatDate(dateStr: string) {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

onMounted(() => {
  loadWikiDocs()
})
</script>

<template>
  <div class="versions-view">
    <div class="page-header">
      <h2 class="page-title">版本控制</h2>
      <p class="page-desc">
        文档版本历史、Diff 对比、回滚。基于 doc_key（如
        <code>wiki:nginx-502</code>
        ）查询版本，回滚会创建新版本不删除历史。
      </p>
    </div>

    <n-card :bordered="true" class="search-card">
      <n-space align="center" :size="12" wrap>
        <n-input
          v-model:value="docKeyInput"
          placeholder="输入 doc_key，如 wiki:nginx-502 或 nginx-doc"
          style="width: 360px"
          @keyup.enter="loadVersions"
        />
        <n-button type="primary" :loading="loading" @click="loadVersions">加载版本</n-button>
        <span class="hint">提示：点击下方 Wiki 文档可快速填入</span>
      </n-space>

      <div v-if="wikiDocsLoading" style="margin-top: 12px">
        <n-spin size="small" />
      </div>
      <div v-else-if="wikiDocs.length" style="margin-top: 12px">
        <div class="wiki-docs-label">已发布 Wiki 文档（点击查看版本历史）：</div>
        <n-space :size="6" style="margin-top: 6px">
          <n-tag
            v-for="doc in wikiDocs"
            :key="doc.slug"
            checkable
            size="small"
            :type="'info'"
            @click="selectWikiDoc(doc)"
          >
            {{ doc.slug }} · v{{ doc.version }}
          </n-tag>
        </n-space>
      </div>
    </n-card>

    <n-card v-if="currentDocKey" :bordered="true">
      <template #header>
        <n-space align="center" :size="12" wrap>
          <span>版本历史:</span>
          <code class="doc-key">{{ currentDocKey }}</code>
          <n-tag size="small">{{ versions.length }} 个版本</n-tag>
          <n-button v-if="versions.length >= 2" size="small" type="info" @click="openDiff()">
            对比版本
          </n-button>
          <n-button quaternary size="small" :loading="loading" @click="loadVersions">刷新</n-button>
        </n-space>
      </template>

      <div v-if="loading" class="loading-container">
        <n-spin size="large" />
      </div>

      <n-empty
        v-else-if="!versions.length"
        description="该 doc_key 暂无版本记录"
        style="padding: 60px 0"
      />

      <n-space v-else vertical :size="8">
        <n-card v-for="v in versions" :key="v.version" size="small" :bordered="true" hoverable>
          <div class="ver-row">
            <div class="ver-main">
              <n-space align="center" :size="8">
                <n-tag size="small" type="primary" :bordered="false">v{{ v.version }}</n-tag>
                <span class="ver-title">{{ v.title || '(未命名)' }}</span>
                <n-tag v-if="v.author" size="small" :bordered="false">作者: {{ v.author }}</n-tag>
              </n-space>
              <div v-if="v.change_summary" class="ver-summary">
                {{ v.change_summary }}
              </div>
              <div class="ver-meta">
                <span>{{ formatDate(v.created_at) }}</span>
                <span class="ver-checksum">
                  checksum:
                  <code>{{ v.checksum }}</code>
                </span>
              </div>
            </div>
            <n-space :size="6">
              <n-button size="small" quaternary @click="openContent(v)">预览</n-button>
              <n-button size="small" quaternary type="info" @click="openDetail(v)">详情</n-button>
              <n-button
                v-if="versions.length >= 2"
                size="small"
                quaternary
                type="warning"
                @click="
                  openDiff(
                    v.version,
                    versions[0].version === v.version
                      ? versions[versions.length - 1].version
                      : versions[0].version,
                  )
                "
              >
                对比最新
              </n-button>
              <n-popconfirm v-if="isAuthenticated" @positive-click="handleRollback(v.version)">
                <template #trigger>
                  <n-button size="small" quaternary type="error">回滚到此版本</n-button>
                </template>
                回滚到 v{{ v.version }}？将创建一个新版本（内容与 v{{ v.version }}
                相同），历史不会删除。
              </n-popconfirm>
            </n-space>
          </div>
        </n-card>
      </n-space>
    </n-card>

    <!-- 详情抽屉 -->
    <n-drawer v-model:show="detailVisible" :width="720" placement="right">
      <n-drawer-content title="版本详情" closable>
        <div v-if="detailLoading" class="loading-container">
          <n-spin size="large" />
        </div>
        <template v-else-if="selectedVersion">
          <n-descriptions :column="1" bordered label-placement="left" size="small">
            <n-descriptions-item label="doc_key">
              <code>{{ selectedVersion.doc_key }}</code>
            </n-descriptions-item>
            <n-descriptions-item label="版本">
              <n-tag size="small" type="primary">v{{ selectedVersion.version }}</n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="标题">
              {{ selectedVersion.title || '(未命名)' }}
            </n-descriptions-item>
            <n-descriptions-item label="作者">
              {{ selectedVersion.author || '-' }}
            </n-descriptions-item>
            <n-descriptions-item label="变更说明">
              {{ selectedVersion.change_summary || '—' }}
            </n-descriptions-item>
            <n-descriptions-item label="Checksum">
              <code>{{ selectedVersion.checksum }}</code>
            </n-descriptions-item>
            <n-descriptions-item label="创建时间">
              {{ formatDate(selectedVersion.created_at) }}
            </n-descriptions-item>
          </n-descriptions>

          <h4 style="margin-top: 20px; margin-bottom: 8px">内容</h4>
          <n-code
            :code="selectedVersion.content || ''"
            language="markdown"
            word-wrap
            style="
              font-size: 13px;
              padding: 12px;
              border-radius: 6px;
              background: var(--n-color-target, #fafafa);
              max-height: 400px;
              overflow: auto;
            "
          />

          <n-space style="margin-top: 16px" :size="8">
            <n-button
              v-if="isAuthenticated"
              size="small"
              type="error"
              @click="handleRollback(selectedVersion.version)"
            >
              回滚到此版本
            </n-button>
          </n-space>
        </template>
      </n-drawer-content>
    </n-drawer>

    <!-- 内容预览弹窗 -->
    <n-modal
      v-model:show="contentVisible"
      preset="card"
      :title="contentPreview ? `v${contentPreview.version} 内容预览` : '内容预览'"
      style="width: 800px; max-width: 95vw"
    >
      <div v-if="contentLoading" class="loading-container">
        <n-spin size="large" />
      </div>
      <template v-else-if="contentPreview">
        <n-code
          :code="contentPreview.content || ''"
          language="markdown"
          word-wrap
          style="
            font-size: 13px;
            padding: 12px;
            border-radius: 6px;
            background: var(--n-color-target, #fafafa);
            max-height: 60vh;
            overflow: auto;
          "
        />
      </template>
    </n-modal>

    <!-- Diff 弹窗 -->
    <n-modal
      v-model:show="diffVisible"
      preset="card"
      :title="`版本对比: ${currentDocKey}`"
      style="width: 900px; max-width: 95vw"
    >
      <n-space align="center" :size="12" style="margin-bottom: 12px">
        <span>从</span>
        <n-select
          v-model:value="diffV1"
          :options="versionOptions"
          size="small"
          style="width: 120px"
          placeholder="v1"
        />
        <span>到</span>
        <n-select
          v-model:value="diffV2"
          :options="versionOptions"
          size="small"
          style="width: 120px"
          placeholder="v2"
        />
        <n-button type="primary" size="small" :loading="diffLoading" @click="doDiff">
          生成 Diff
        </n-button>
      </n-space>

      <n-alert v-if="diffError" type="error" style="margin-bottom: 12px">
        {{ diffError }}
      </n-alert>

      <div v-if="diffStats" style="margin-bottom: 8px">
        <n-space :size="12">
          <n-tag size="small" type="success">+{{ diffStats.added }} 行</n-tag>
          <n-tag size="small" type="error">-{{ diffStats.removed }} 行</n-tag>
        </n-space>
      </div>

      <div v-if="diffLoading" class="loading-container">
        <n-spin size="large" />
      </div>

      <n-code
        v-else-if="diffResult"
        :code="diffResult"
        language="diff"
        word-wrap
        style="
          font-size: 13px;
          padding: 12px;
          border-radius: 6px;
          background: var(--n-color-target, #fafafa);
          max-height: 60vh;
          overflow: auto;
        "
      />
    </n-modal>
  </div>
</template>

<style scoped>
.versions-view {
  max-width: 1400px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 24px;
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

.search-card {
  margin-bottom: 16px;
}

.hint {
  font-size: 12px;
  color: var(--n-text-color-2, #6b7280);
}

.wiki-docs-label {
  font-size: 12px;
  color: var(--n-text-color-2, #6b7280);
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  gap: 16px;
}

.doc-key {
  font-size: 13px;
  background: var(--n-color-target, #f5f5f5);
  padding: 2px 6px;
  border-radius: 4px;
}

.ver-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.ver-main {
  flex: 1;
  min-width: 0;
}

.ver-title {
  font-weight: 600;
  font-size: 14px;
}

.ver-summary {
  font-size: 12px;
  color: var(--n-text-color-2, #6b7280);
  margin-top: 4px;
  font-style: italic;
}

.ver-meta {
  font-size: 11px;
  color: var(--n-text-color-3, #999);
  margin-top: 4px;
  display: flex;
  gap: 12px;
}

.ver-checksum code {
  font-size: 11px;
}
</style>
