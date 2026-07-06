<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  NCard,
  NTag,
  NSpace,
  NButton,
  NSpin,
  NEmpty,
  NSelect,
  NInput,
  NDrawer,
  NDrawerContent,
  NDescriptions,
  NDescriptionsItem,
  NModal,
  NForm,
  NFormItem,
  NAlert,
  NCode,
  useMessage,
  useDialog,
} from 'naive-ui'
import {
  listTemplates,
  getTemplate,
  createTemplate,
  updateTemplate,
  deleteTemplate,
  renderTemplate,
  type Template,
} from '@/api/templates'
import { getAuthToken } from '@/api/index'

const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const templates = ref<Template[]>([])

const categoryFilter = ref<string | null>(null)

const isAuthenticated = computed(() => !!getAuthToken())

// 详情抽屉
const detailVisible = ref(false)
const detailLoading = ref(false)
const selectedTemplate = ref<Template | null>(null)

// 编辑/创建弹窗
const editorVisible = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const editorSaving = ref(false)
const editorForm = ref<{
  slug: string
  name: string
  category: string
  description: string
  content: string
}>({
  slug: '',
  name: '',
  category: 'custom',
  description: '',
  content: '',
})
const editorOriginalSlug = ref<string>('')
const editorIsBuiltin = ref<boolean>(false)

// 渲染弹窗
const renderVisible = ref(false)
const renderLoading = ref(false)
const renderSlug = ref<string>('')
const renderVarsText = ref<string>('{}')
const renderOutput = ref<string>('')
const renderError = ref<string>('')

const categoryOptions = [
  { label: '全部', value: '' },
  { label: '运维 (ops)', value: 'ops' },
  { label: '故障 (incident)', value: 'incident' },
  { label: '配置 (config)', value: 'config' },
  { label: '通用 (general)', value: 'general' },
  { label: '自定义 (custom)', value: 'custom' },
]

const categoryColor: Record<string, string> = {
  ops: '#2080f0',
  incident: '#d03050',
  config: '#f0a020',
  general: '#18a058',
  custom: '#8a2be2',
}

function isBuiltin(t: Template): boolean {
  return !!t.is_builtin || Number(t.is_builtin) === 1
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

async function loadTemplates() {
  loading.value = true
  try {
    const res = await listTemplates(categoryFilter.value || undefined)
    templates.value = res.templates || []
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '加载模板失败')
  } finally {
    loading.value = false
  }
}

async function openDetail(tpl: Template) {
  detailVisible.value = true
  detailLoading.value = true
  selectedTemplate.value = tpl
  try {
    const full = await getTemplate(tpl.slug)
    selectedTemplate.value = full
  } catch (err: any) {
    // 退回到列表中的数据
  } finally {
    detailLoading.value = false
  }
}

function openEditor(tpl: Template | null, mode: 'create' | 'edit') {
  editorMode.value = mode
  if (mode === 'create' || !tpl) {
    editorForm.value = {
      slug: '',
      name: '',
      category: 'custom',
      description: '',
      content: '',
    }
    editorOriginalSlug.value = ''
    editorIsBuiltin.value = false
  } else {
    editorForm.value = {
      slug: tpl.slug,
      name: tpl.name,
      category: tpl.category,
      description: tpl.description || '',
      content: tpl.content,
    }
    editorOriginalSlug.value = tpl.slug
    editorIsBuiltin.value = isBuiltin(tpl)
  }
  editorVisible.value = true
}

async function saveTemplate() {
  if (!editorForm.value.slug || !editorForm.value.name || !editorForm.value.content) {
    message.warning('slug / 名称 / 内容均为必填项')
    return
  }
  editorSaving.value = true
  try {
    if (editorMode.value === 'create') {
      await createTemplate({
        slug: editorForm.value.slug,
        name: editorForm.value.name,
        content: editorForm.value.content,
        category: editorForm.value.category,
        description: editorForm.value.description,
      })
      message.success(`模板已创建: ${editorForm.value.slug}`)
    } else {
      const slug = editorOriginalSlug.value
      const builtin = editorIsBuiltin.value
      await updateTemplate(slug, {
        name: editorForm.value.name,
        category: editorForm.value.category,
        description: editorForm.value.description,
        // 内置模板不可改 content，仍然发送（后端会忽略并返回 403）；这里直接跳过
        content: builtin ? undefined : editorForm.value.content,
      })
      message.success(`模板已更新: ${slug}`)
    }
    editorVisible.value = false
    await loadTemplates()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '保存失败')
  } finally {
    editorSaving.value = false
  }
}

function handleDelete(tpl: Template) {
  dialog.warning({
    title: '删除模板',
    content: `确认删除模板 "${tpl.name}" (${tpl.slug})？此操作不可恢复。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await deleteTemplate(tpl.slug)
        message.success(`已删除: ${tpl.slug}`)
        await loadTemplates()
      } catch (err: any) {
        message.error(err?.response?.data?.detail || err?.message || '删除失败')
      }
    },
  })
}

function openRender(tpl: Template) {
  renderSlug.value = tpl.slug
  renderVarsText.value = '{\n  "title": "示例标题",\n  "description": "示例描述"\n}'
  renderOutput.value = ''
  renderError.value = ''
  renderVisible.value = true
}

async function doRender() {
  renderLoading.value = true
  renderError.value = ''
  renderOutput.value = ''
  let variables: Record<string, any>
  try {
    variables = JSON.parse(renderVarsText.value)
  } catch (e: any) {
    renderError.value = `JSON 解析失败: ${e.message}`
    renderLoading.value = false
    return
  }
  try {
    const res = await renderTemplate(renderSlug.value, variables)
    renderOutput.value = res.rendered
  } catch (err: any) {
    renderError.value = err?.response?.data?.detail || err?.message || '渲染失败'
  } finally {
    renderLoading.value = false
  }
}

onMounted(() => {
  loadTemplates()
})
</script>

<template>
  <div class="templates-view">
    <div class="page-header">
      <h2 class="page-title">模板管理</h2>
      <p class="page-desc">
        <span>内置运维 / 故障 / 配置等模板，支持 Mustache 风格变量占位渲染（</span>
        <code v-pre>{{ variable }}</code>
        <span>/</span>
        <code v-pre>{{#list}}...{{/list}}</code>
        <span>）</span>
      </p>
    </div>

    <n-card :bordered="true">
      <template #header>
        <n-space align="center" :size="12" wrap>
          <span>模板列表</span>
          <n-select
            v-model:value="categoryFilter"
            :options="categoryOptions"
            size="small"
            style="width: 180px"
            placeholder="分类筛选"
            @update:value="loadTemplates"
          />
          <n-button quaternary size="small" :loading="loading" @click="loadTemplates">
            刷新
          </n-button>
          <n-button
            size="small"
            type="primary"
            :disabled="!isAuthenticated"
            @click="openEditor(null, 'create')"
          >
            新建模板
          </n-button>
          <n-alert
            v-if="!isAuthenticated"
            type="info"
            :show-icon="false"
            style="font-size: 12px; padding: 4px 10px"
          >
            未登录，编辑/删除/创建需先登录获取 token
          </n-alert>
        </n-space>
      </template>

      <div v-if="loading" class="loading-container">
        <n-spin size="large" />
      </div>

      <n-empty v-else-if="!templates.length" description="暂无模板数据" style="padding: 60px 0" />

      <n-space v-else vertical :size="8">
        <n-card v-for="tpl in templates" :key="tpl.slug" size="small" :bordered="true" hoverable>
          <div class="tpl-row">
            <div class="tpl-main">
              <n-space align="center" :size="8">
                <code class="tpl-slug">{{ tpl.slug }}</code>
                <n-tag
                  size="small"
                  :bordered="false"
                  :style="{
                    color: categoryColor[tpl.category] || '#999',
                    background: 'transparent',
                  }"
                >
                  {{ tpl.category }}
                </n-tag>
                <n-tag v-if="isBuiltin(tpl)" size="small" type="info">内置</n-tag>
                <n-tag v-else size="small" type="success">自定义</n-tag>
              </n-space>
              <div class="tpl-name">{{ tpl.name }}</div>
              <div class="tpl-desc">{{ tpl.description || '—' }}</div>
            </div>
            <n-space :size="6">
              <n-button size="small" quaternary @click="openDetail(tpl)">查看</n-button>
              <n-button size="small" quaternary type="info" @click="openRender(tpl)">渲染</n-button>
              <n-button
                size="small"
                quaternary
                type="warning"
                :disabled="!isAuthenticated"
                @click="openEditor(tpl, 'edit')"
              >
                编辑
              </n-button>
              <n-button
                size="small"
                quaternary
                type="error"
                :disabled="!isAuthenticated || isBuiltin(tpl)"
                @click="handleDelete(tpl)"
              >
                删除
              </n-button>
            </n-space>
          </div>
        </n-card>
      </n-space>
    </n-card>

    <!-- 详情抽屉 -->
    <n-drawer v-model:show="detailVisible" :width="720" placement="right">
      <n-drawer-content title="模板详情" closable>
        <div v-if="detailLoading" class="loading-container">
          <n-spin size="large" />
        </div>
        <template v-else-if="selectedTemplate">
          <n-descriptions :column="1" bordered label-placement="left" size="small">
            <n-descriptions-item label="Slug">
              <code>{{ selectedTemplate.slug }}</code>
            </n-descriptions-item>
            <n-descriptions-item label="名称">
              {{ selectedTemplate.name }}
            </n-descriptions-item>
            <n-descriptions-item label="分类">
              <n-tag
                size="small"
                :bordered="false"
                :style="{
                  color: categoryColor[selectedTemplate.category] || '#999',
                  background: 'transparent',
                }"
              >
                {{ selectedTemplate.category }}
              </n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="类型">
              <n-tag v-if="isBuiltin(selectedTemplate)" size="small" type="info">内置</n-tag>
              <n-tag v-else size="small" type="success">自定义</n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="描述">
              {{ selectedTemplate.description || '—' }}
            </n-descriptions-item>
            <n-descriptions-item label="创建时间">
              {{ formatDate(selectedTemplate.created_at || '') }}
            </n-descriptions-item>
            <n-descriptions-item label="更新时间">
              {{ formatDate(selectedTemplate.updated_at || '') }}
            </n-descriptions-item>
          </n-descriptions>

          <h4 style="margin-top: 20px; margin-bottom: 8px">模板内容</h4>
          <n-code
            :code="selectedTemplate.content || ''"
            language="markdown"
            word-wrap
            style="
              font-size: 13px;
              padding: 12px;
              border-radius: 6px;
              background: var(--n-color-target, #fafafa);
            "
          />

          <n-space style="margin-top: 16px" :size="8">
            <n-button
              size="small"
              type="info"
              @click="
                openRender(selectedTemplate);
                detailVisible = false;
              "
            >
              渲染此模板
            </n-button>
            <n-button
              size="small"
              type="warning"
              :disabled="!isAuthenticated"
              @click="
                openEditor(selectedTemplate, 'edit');
                detailVisible = false;
              "
            >
              编辑
            </n-button>
          </n-space>
        </template>
      </n-drawer-content>
    </n-drawer>

    <!-- 编辑/创建弹窗 -->
    <n-modal
      v-model:show="editorVisible"
      preset="card"
      :title="editorMode === 'create' ? '新建模板' : `编辑模板: ${editorOriginalSlug}`"
      style="width: 800px; max-width: 95vw"
    >
      <n-form :label-width="90" label-placement="left" size="small">
        <n-form-item label="Slug" required>
          <n-input
            v-model:value="editorForm.slug"
            placeholder="kebab-case，如 my-runbook"
            :disabled="editorMode === 'edit'"
          />
        </n-form-item>
        <n-form-item label="名称" required>
          <n-input v-model:value="editorForm.name" placeholder="模板显示名" />
        </n-form-item>
        <n-form-item label="分类">
          <n-select
            v-model:value="editorForm.category"
            :options="categoryOptions.filter((o) => o.value !== '')"
            placeholder="选择分类"
          />
        </n-form-item>
        <n-form-item label="描述">
          <n-input
            v-model:value="editorForm.description"
            type="textarea"
            :rows="2"
            placeholder="一句话说明模板用途"
          />
        </n-form-item>
        <n-form-item label="内容" required>
          <div v-if="editorIsBuiltin" style="margin-bottom: 6px">
            <n-alert type="warning" :show-icon="false" style="font-size: 12px; padding: 4px 10px">
              内置模板内容不可修改，仅可调整名称/分类/描述；如需自定义内容请创建副本
            </n-alert>
          </div>
          <n-input
            v-model:value="editorForm.content"
            type="textarea"
            :rows="14"
            placeholder="模板内容，支持 {{variable}} 与 {{#list}}...{{/list}} 占位"
            :disabled="editorIsBuiltin"
            style="font-family: monospace; font-size: 13px"
          />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editorVisible = false">取消</n-button>
          <n-button type="primary" :loading="editorSaving" @click="saveTemplate">
            {{ editorMode === 'create' ? '创建' : '保存' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 渲染弹窗 -->
    <n-modal
      v-model:show="renderVisible"
      preset="card"
      :title="`渲染模板: ${renderSlug}`"
      style="width: 900px; max-width: 95vw"
    >
      <n-form :label-width="80" label-placement="left" size="small">
        <n-form-item label="变量 JSON">
          <n-input
            v-model:value="renderVarsText"
            type="textarea"
            :rows="8"
            placeholder='{"key": "value", "list": [{"a": 1}]}'
            style="font-family: monospace; font-size: 13px"
          />
        </n-form-item>
        <n-space>
          <n-button type="primary" :loading="renderLoading" @click="doRender">渲染</n-button>
        </n-space>
      </n-form>

      <n-alert v-if="renderError" type="error" style="margin-top: 12px">
        {{ renderError }}
      </n-alert>

      <div v-if="renderOutput" style="margin-top: 12px">
        <h4 style="margin: 0 0 6px">渲染结果</h4>
        <n-code
          :code="renderOutput"
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
      </div>
    </n-modal>
  </div>
</template>

<style scoped>
.templates-view {
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

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  gap: 16px;
}

.tpl-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.tpl-main {
  flex: 1;
  min-width: 0;
}

.tpl-slug {
  font-size: 13px;
  background: var(--n-color-target, #f5f5f5);
  padding: 2px 6px;
  border-radius: 4px;
}

.tpl-name {
  font-size: 15px;
  font-weight: 600;
  margin-top: 6px;
}

.tpl-desc {
  font-size: 12px;
  color: var(--n-text-color-2, #6b7280);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
