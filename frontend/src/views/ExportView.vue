<script setup lang="ts">
import { ref, computed, reactive, onMounted } from 'vue'
import {
  NCard,
  NTag,
  NSpace,
  NButton,
  NInput,
  NSelect,
  NAlert,
  NDivider,
  NCode,
  NForm,
  NFormItem,
  NRadioGroup,
  NRadio,
  useMessage,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import { exportDocument, downloadBlob, exportFormatOptions, type ExportFormat } from '@/api/export'
import { listWikiDocs } from '@/api/versions'
import { getTemplate } from '@/api/templates'
import { getAuthToken } from '@/api/index'
import PageHeader from '@/components/common/PageHeader.vue'

const message = useMessage()

const isAuthenticated = computed(() => !!getAuthToken())

// 表单模型（NForm 单一数据源）：标题 / 内容 / 格式 / 选中的 Wiki 与模板 slug
const formModel = reactive({
  title: 'untitled',
  content: '# 标题\n\n在此输入 Markdown 内容...',
  format: 'markdown' as ExportFormat,
  selectedWikiSlug: null as string | null,
  selectedTemplateSlug: null as string | null,
})

// 向后兼容访问器：以 computed 代理 formModel，便于现有逻辑与测试通过 vm.title 等访问
const title = computed({
  get: () => formModel.title,
  set: (v: string) => {
    formModel.title = v
  },
})
const content = computed({
  get: () => formModel.content,
  set: (v: string) => {
    formModel.content = v
  },
})
const format = computed({
  get: () => formModel.format,
  set: (v: ExportFormat) => {
    formModel.format = v
  },
})
const selectedWikiSlug = computed({
  get: () => formModel.selectedWikiSlug,
  set: (v: string | null) => {
    formModel.selectedWikiSlug = v
  },
})
const selectedTemplateSlug = computed({
  get: () => formModel.selectedTemplateSlug,
  set: (v: string | null) => {
    formModel.selectedTemplateSlug = v
  },
})

const exporting = ref(false)

// 预设内容来源
const wikiDocs = ref<{ slug: string; title: string; version: number }[]>([])
const wikiDocsLoading = ref(false)
const templates = ref<{ slug: string; name: string }[]>([])
const templatesLoading = ref(false)

// 表单校验规则：标题（影响导出文件名）与导出内容必填
const formRef = ref<FormInst | null>(null)
const rules: FormRules = {
  title: {
    required: true,
    message: '请输入标题',
    trigger: ['blur', 'input'],
  },
  content: {
    required: true,
    message: '请输入导出内容',
    trigger: ['blur', 'input'],
  },
}

const formatDesc = computed(
  () => exportFormatOptions.find((o) => o.value === formModel.format)?.desc || '',
)

async function loadWikiDocs() {
  wikiDocsLoading.value = true
  try {
    const res = await listWikiDocs(100, 0)
    wikiDocs.value = (res.documents || []).map((d) => ({
      slug: d.slug,
      title: d.title,
      version: d.version,
    }))
  } catch (err: any) {
    console.warn('加载 Wiki 文档失败', err)
  } finally {
    wikiDocsLoading.value = false
  }
}

async function loadTemplates() {
  templatesLoading.value = true
  try {
    const { listTemplates } = await import('@/api/templates')
    const res = await listTemplates()
    templates.value = (res.templates || []).map((t) => ({
      slug: t.slug,
      name: t.name,
    }))
  } catch (err: any) {
    console.warn('加载模板失败', err)
  } finally {
    templatesLoading.value = false
  }
}

async function loadWikiContent(slug: string) {
  try {
    // 使用 versions API 拿最新版本的 content
    const { getVersion } = await import('@/api/versions')
    // 改用 wiki 列表接口拿 slug，再用版本列表拿最新版本号
    const { listVersions } = await import('@/api/versions')
    const vlist = await listVersions(`wiki:${slug}`)
    const latest = vlist.versions?.[0]
    if (!latest) {
      message.warning(`未找到 wiki:${slug} 的版本`)
      return
    }
    const full = await getVersion(`wiki:${slug}`, latest.version)
    title.value = full.title || slug
    content.value = full.content || ''
    message.success(`已加载 Wiki: ${slug} (v${latest.version})`)
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '加载 Wiki 内容失败')
  }
}

async function loadTemplateContent(slug: string) {
  try {
    const tpl = await getTemplate(slug)
    title.value = tpl.name
    content.value = tpl.content
    message.success(`已加载模板: ${tpl.name}`)
  } catch (err: any) {
    message.error(err?.response?.data?.detail || err?.message || '加载模板失败')
  }
}

async function doExport() {
  if (!isAuthenticated.value) {
    message.error('导出需要登录 token')
    return
  }
  if (!title.value.trim() || !content.value.trim()) {
    message.warning('标题和内容不能为空')
    return
  }
  exporting.value = true
  try {
    const { blob, filename } = await exportDocument({
      title: title.value,
      content: content.value,
      format: format.value,
    })
    downloadBlob(blob, filename)
    message.success(`已导出: ${filename}（${(blob.size / 1024).toFixed(2)} KB）`)
  } catch (err: any) {
    // 后端可能返回 PDF 依赖缺失的 500
    const detail = err?.response?.data?.detail || err?.message || '导出失败'
    if (format.value === 'pdf' && /wkhtmltopdf|依赖/i.test(String(detail))) {
      message.error('PDF 导出失败：服务端未安装 wkhtmltopdf')
    } else {
      message.error(String(detail))
    }
  } finally {
    exporting.value = false
  }
}

// 提交按钮：先校验表单，校验失败给出 warning；通过后再调用 doExport
async function handleExport() {
  try {
    await formRef.value?.validate()
  } catch {
    message.warning('请完善表单必填项后再提交')
    return
  }
  await doExport()
}

function handleWikiSelect(slug: string) {
  selectedWikiSlug.value = slug
  loadWikiContent(slug)
}

function handleTemplateSelect(slug: string) {
  selectedTemplateSlug.value = slug
  loadTemplateContent(slug)
}

onMounted(() => {
  loadWikiDocs()
  loadTemplates()
})
</script>

<template>
  <div class="export-view">
    <PageHeader
      title="导出中心"
      description="将 Markdown 内容导出为 Markdown / HTML / 纯文本 / PDF（PDF 需服务端安装 wkhtmltopdf）"
    />

    <n-alert v-if="!isAuthenticated" type="info" style="margin-bottom: 16px">
      导出需要登录 Token，未登录时按钮将禁用
    </n-alert>

    <n-card :bordered="true" class="preset-card">
      <template #header>
        <span>内容来源（可选）</span>
      </template>
      <n-space vertical :size="12">
        <div>
          <div class="preset-label">从 Wiki 文档加载：</div>
          <n-space v-if="wikiDocsLoading" :size="6">
            <n-tag size="small">加载中...</n-tag>
          </n-space>
          <!-- 单选场景：一次导出一个 wiki，绑定 formModel.selectedWikiSlug -->
          <NRadioGroup
            v-else-if="wikiDocs.length"
            v-model:value="formModel.selectedWikiSlug"
            @update:value="handleWikiSelect"
          >
            <n-space :size="6">
              <NRadio v-for="doc in wikiDocs" :key="doc.slug" :value="doc.slug">
                {{ doc.slug }} · v{{ doc.version }}
              </NRadio>
            </n-space>
          </NRadioGroup>
          <span v-else class="empty-hint">暂无已发布 Wiki 文档</span>
        </div>

        <n-divider style="margin: 4px 0" />

        <div>
          <div class="preset-label">从模板加载：</div>
          <n-space v-if="templatesLoading" :size="6">
            <n-tag size="small">加载中...</n-tag>
          </n-space>
          <!-- 单选场景：绑定 formModel.selectedTemplateSlug -->
          <NRadioGroup
            v-else-if="templates.length"
            v-model:value="formModel.selectedTemplateSlug"
            @update:value="handleTemplateSelect"
          >
            <n-space :size="6">
              <NRadio v-for="tpl in templates" :key="tpl.slug" :value="tpl.slug">
                {{ tpl.name }}
              </NRadio>
            </n-space>
          </NRadioGroup>
          <span v-else class="empty-hint">暂无模板</span>
        </div>
      </n-space>
    </n-card>

    <n-card :bordered="true" style="margin-top: 16px">
      <template #header>
        <span>导出内容</span>
      </template>

      <NForm ref="formRef" :model="formModel" :rules="rules" label-placement="top">
        <NFormItem label="标题" path="title">
          <n-input
            v-model:value="formModel.title"
            placeholder="文档标题（影响导出文件名）"
            style="max-width: 500px"
          />
        </NFormItem>

        <NFormItem label="格式" path="format">
          <n-select
            v-model:value="formModel.format"
            :options="exportFormatOptions.map((o) => ({ label: o.label, value: o.value }))"
            style="max-width: 280px"
          />
          <span class="format-desc">{{ formatDesc }}</span>
        </NFormItem>

        <NFormItem label="内容" path="content">
          <n-input
            v-model:value="formModel.content"
            type="textarea"
            :rows="16"
            placeholder="支持 Markdown 语法"
            style="font-family: monospace; font-size: 13px"
          />
        </NFormItem>

        <n-space align="center" :size="12" style="margin-top: 4px">
          <n-button
            type="primary"
            size="large"
            :loading="exporting"
            :disabled="!isAuthenticated"
            @click="handleExport"
          >
            导出并下载
          </n-button>
          <span class="hint">点击后浏览器将自动下载文件</span>
        </n-space>
      </NForm>
    </n-card>

    <n-card :bordered="true" style="margin-top: 16px" size="small">
      <template #header>
        <span>预览（原始 Markdown）</span>
      </template>
      <n-code
        :code="content"
        language="markdown"
        word-wrap
        style="
          font-size: 12px;
          padding: 12px;
          border-radius: 6px;
          background: var(--n-color-target, #fafafa);
          max-height: 300px;
          overflow: auto;
        "
      />
    </n-card>
  </div>
</template>

<style scoped>
.export-view {
  max-width: 1200px;
  margin: 0 auto;
}

.preset-card {
  margin-bottom: 0;
}

.preset-label {
  font-size: 13px;
  color: var(--n-text-color-2, #6b7280);
  margin-bottom: 6px;
}

.empty-hint {
  font-size: 12px;
  color: var(--n-text-color-3, #999);
  font-style: italic;
}

.format-desc {
  font-size: 12px;
  color: var(--n-text-color-3, #999);
  font-style: italic;
}

.hint {
  font-size: 12px;
  color: var(--n-text-color-2, #6b7280);
}
</style>
