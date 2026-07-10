<script setup lang="ts">
/**
 * Wiki 页面编辑器组件（S16-2 / S16-5 / P1-5）
 *
 * P1-5 增强：
 * - frontmatter 结构化表单（title/type/tags 独立字段，保留 sources/created_at 等未知字段）
 * - `[[wikilink]]` 自动补全浮层（查 listWikiPages 缓存）
 * - Ctrl+S 保存快捷键
 *
 * 用法：
 *   <WikiEditor
 *     :slug="currentPage.slug"
 *     :content="currentPage.content"
 *     :version="currentPage.version"
 *     :can-edit="hasLock"
 *     @saved="handleSaved"
 *     @cancel="handleCancel"
 *   />
 */
import { ref, computed, watch, onMounted } from 'vue'
import { NButton, NInput, NCard, NSpace, NText, NAlert, NSelect, NDynamicTags } from 'naive-ui'
import { updateWikiPage, listWikiPages } from '@/api/wiki'
import { renderWikiMarkdown } from '@/utils/wikiRender'
import {
  parseFrontmatter,
  serializeFrontmatter,
  buildFrontmatter,
  WIKI_PAGE_TYPES,
} from '@/utils/frontmatter'
import { useEditDraft, type EditDraft } from '@/composables/useEditDraft'
import type { WikiPageUpdateResult } from '@/types/api'

const props = defineProps<{
  slug: string
  content: string
  version?: number
  canEdit?: boolean
}>()

const emit = defineEmits<{
  (e: 'saved', result: WikiPageUpdateResult): void
  (e: 'cancel'): void
}>()

// ────────── P1-5: 结构化 frontmatter 字段 ──────────

const fmSlug = ref(props.slug)
const fmTitle = ref('')
const fmType = ref('concept')
const fmTags = ref<string[]>([])
const fmRest = ref<Record<string, unknown>>({})
const bodyText = ref('')
const hasFrontmatter = ref(true)

/** 从完整 content 解析到结构化字段 */
function syncFromContent(content: string) {
  const parsed = parseFrontmatter(content)
  fmSlug.value = parsed.slug || props.slug
  fmTitle.value = parsed.title
  fmType.value = parsed.type
  fmTags.value = parsed.tags
  fmRest.value = parsed.rest
  bodyText.value = parsed.body
  hasFrontmatter.value = parsed.hasFrontmatter
}

// 初始化
syncFromContent(props.content)

/** editingContent：从结构化字段序列化为完整内容（computed） */
const editingContent = computed(() => {
  if (!hasFrontmatter.value) {
    // 无 frontmatter 的内容直接返回 body
    return bodyText.value
  }
  return serializeFrontmatter(
    buildFrontmatter(
      { slug: fmSlug.value, title: fmTitle.value, type: fmType.value, tags: fmTags.value },
      fmRest.value,
      bodyText.value,
    ),
  )
})

// 保存/草稿状态
const changeSummary = ref('')
const saving = ref(false)
const errorMsg = ref<string | null>(null)

// S16-5：草稿恢复状态
const draft = useEditDraft(props.slug)
const draftRecovery = ref<EditDraft | null>(draft.draft.value)
const draftConflict = ref(draft.isConflictWith(props.version ?? 0))

// content prop 变化（slug 切换）→ 重新解析
watch(
  () => props.content,
  (newContent) => {
    syncFromContent(newContent)
    errorMsg.value = null
  },
)

// S16-5：编辑内容变化时持久化草稿
watch(
  editingContent,
  (newVal) => {
    if (newVal !== props.content && props.version !== undefined) {
      draft.save(newVal, props.version, changeSummary.value || undefined)
    }
  },
)

watch(changeSummary, (newVal) => {
  if (editingContent.value !== props.content && props.version !== undefined) {
    draft.save(editingContent.value, props.version, newVal || undefined)
  }
})

// 预览
const previewHtml = computed(() => {
  try {
    return renderWikiMarkdown(editingContent.value)
  } catch {
    return editingContent.value
  }
})

const isDirty = computed(() => editingContent.value !== props.content)

const canSave = computed(
  () => props.canEdit !== false && isDirty.value && !saving.value,
)

async function handleSave() {
  if (!canSave.value) return
  saving.value = true
  errorMsg.value = null
  try {
    const result = await updateWikiPage(props.slug, {
      content: editingContent.value,
      change_summary: changeSummary.value || undefined,
      expected_version: props.version,
    })
    emit('saved', result)
    changeSummary.value = ''
    draft.clear()
    draftRecovery.value = null
    draftConflict.value = false
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.message || '保存失败'
    errorMsg.value = detail
  } finally {
    saving.value = false
  }
}

/** P1-5: Ctrl+S 保存快捷键 */
function handleKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault()
    handleSave()
  }
}

function handleCancel() {
  emit('cancel')
}

// ────────── S16-5: 草稿恢复 ──────────

function handleRestoreDraft() {
  if (!draftRecovery.value) return
  syncFromContent(draftRecovery.value.content)
  if (draftRecovery.value.summary) {
    changeSummary.value = draftRecovery.value.summary
  }
  draftRecovery.value = null
  draftConflict.value = false
}

function handleDiscardDraft() {
  draft.clear()
  draftRecovery.value = null
  draftConflict.value = false
}

function formatDraftTime(ts: number): string {
  const diff = Date.now() - ts
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  const d = new Date(ts)
  return `${d.getMonth() + 1}-${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// ────────── P1-5: [[wikilink]] 自动补全 ──────────

/** 可用 wiki 页面列表（懒加载 + 缓存） */
const wikiPages = ref<{ slug: string; title: string }[]>([])
const wikiPagesLoaded = ref(false)

async function loadWikiPages() {
  if (wikiPagesLoaded.value) return
  try {
    const res = await listWikiPages()
    wikiPages.value = res.pages
      .filter((p) => p.slug !== 'index' && p.slug !== 'log')
      .map((p) => ({ slug: p.slug, title: p.title || p.slug }))
    wikiPagesLoaded.value = true
  } catch {
    // 加载失败不影响编辑
  }
}

onMounted(() => {
  loadWikiPages()
})

/** wikilink 补全状态 */
const wikilinkActive = ref(false)
const wikilinkQuery = ref('')
const wikilinkStart = ref(-1) // [[ 的起始位置
const textareaRef = ref<InstanceType<typeof NInput> | null>(null)

/** 过滤后的候选列表 */
const wikilinkOptions = computed(() => {
  const q = wikilinkQuery.value.toLowerCase()
  const all = wikiPages.value.map((p) => ({
    label: p.title,
    value: p.slug,
  }))
  if (!q) return all.slice(0, 20)
  return all
    .filter(
      (opt) =>
        opt.value.toLowerCase().includes(q) || opt.label.toLowerCase().includes(q),
    )
    .slice(0, 20)
})

/**
 * 检测 textarea 中光标前是否有未闭合的 [[
 * 如果有，激活补全模式
 */
function detectWikilink() {
  const textarea = (textareaRef.value as any)?.$el?.querySelector('textarea') as HTMLTextAreaElement | null
  if (!textarea) return
  const cursorPos = textarea.selectionStart
  const text = bodyText.value
  // 在光标前查找最近的 [[
  const before = text.substring(0, cursorPos)
  const bracketIdx = before.lastIndexOf('[[')
  if (bracketIdx === -1) {
    wikilinkActive.value = false
    return
  }
  // 检查 [[ 后是否有 ]]（已闭合则不补全）
  const afterBracket = before.substring(bracketIdx + 2)
  if (afterBracket.includes(']]')) {
    wikilinkActive.value = false
    return
  }
  // 检查 [[ 后是否有换行（跨行不补全）
  if (afterBracket.includes('\n')) {
    wikilinkActive.value = false
    return
  }
  wikilinkActive.value = true
  wikilinkQuery.value = afterBracket
  wikilinkStart.value = bracketIdx
}

/** 选中某个 slug 后插入到 textarea */
function insertWikilink(slug: string) {
  const text = bodyText.value
  const before = text.substring(0, wikilinkStart.value)
  const after = text.substring(wikilinkStart.value + 2 + wikilinkQuery.value.length)
  bodyText.value = `${before}[[${slug}]]${after}`

  wikilinkActive.value = false
  wikilinkQuery.value = ''
  wikilinkStart.value = -1

  // 恢复焦点，光标移到 ]] 后
  requestAnimationFrame(() => {
    const textarea = (textareaRef.value as any)?.$el?.querySelector('textarea') as HTMLTextAreaElement | null
    if (textarea) {
      const newPos = before.length + slug.length + 4 // [[slug]]
      textarea.focus()
      textarea.setSelectionRange(newPos, newPos)
    }
  })
}

/** 关闭 wikilink 补全 */
function closeWikilink() {
  wikilinkActive.value = false
  wikilinkQuery.value = ''
  wikilinkStart.value = -1
}

/** 处理 body textarea 的 input 事件 */
function handleBodyInput(value: string) {
  bodyText.value = value
  detectWikilink()
}

/** 处理 body textarea 的 keydown（Escape 关闭补全） */
function handleBodyKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && wikilinkActive.value) {
    e.preventDefault()
    closeWikilink()
  }
  // P1-5: Ctrl+S
  handleKeydown(e)
}

/** 处理补全列表的键盘导航（ArrowDown/ArrowUp/Enter） */
function handleWikilinkSelect(value: string) {
  insertWikilink(value)
}
</script>

<template>
  <div class="wiki-editor" @keydown="handleKeydown">
    <div class="editor-header">
      <span class="editor-title">编辑页面</span>
      <NSpace :size="8" class="editor-actions">
        <NButton size="small" :disabled="saving" @click="handleCancel">取消</NButton>
        <NButton
          size="small"
          type="primary"
          :loading="saving"
          :disabled="!canSave"
          @click="handleSave"
        >
          保存
          <span class="shortcut-hint">Ctrl+S</span>
        </NButton>
      </NSpace>
    </div>

    <NAlert v-if="errorMsg" type="error" :show-icon="true" class="editor-error" closable @close="errorMsg = null">
      {{ errorMsg }}
    </NAlert>

    <!-- S16-5：草稿恢复提示 -->
    <NAlert
      v-if="draftRecovery"
      :type="draftConflict ? 'warning' : 'info'"
      :show-icon="true"
      class="editor-draft"
    >
      <div class="draft-content">
        <div class="draft-message">
          <template v-if="draftConflict">
            检测到未保存草稿（基于版本 {{ draftRecovery.version }}），但当前页面已是版本 {{ version }}，草稿可能与服务器版本冲突。
          </template>
          <template v-else>
            检测到未保存草稿（{{ formatDraftTime(draftRecovery.savedAt) }}），是否恢复？
          </template>
        </div>
        <NSpace :size="8" class="draft-actions">
          <NButton size="small" type="primary" @click="handleRestoreDraft">恢复草稿</NButton>
          <NButton size="small" @click="handleDiscardDraft">丢弃草稿</NButton>
        </NSpace>
      </div>
    </NAlert>

    <NAlert
      v-if="canEdit === false"
      type="warning"
      :show-icon="true"
      class="editor-warning"
    >
      你未持有编辑锁，保存将被服务端拒绝。请先在协作面板申请编辑锁。
    </NAlert>

    <!-- P1-5: frontmatter 结构化表单 -->
    <div v-if="hasFrontmatter" class="frontmatter-form">
      <div class="pane-label">Frontmatter</div>
      <div class="fm-grid">
        <div class="fm-field">
          <label class="fm-label">标题</label>
          <NInput
            v-model:value="fmTitle"
            placeholder="页面标题"
            :disabled="saving"
            size="small"
          />
        </div>
        <div class="fm-field">
          <label class="fm-label">类型</label>
          <NSelect
            v-model:value="fmType"
            :options="WIKI_PAGE_TYPES.map((t) => ({ label: t, value: t }))"
            :disabled="saving"
            size="small"
          />
        </div>
        <div class="fm-field fm-field-full">
          <label class="fm-label">标签</label>
          <NDynamicTags
            v-model:value="fmTags"
            :disabled="saving"
            size="small"
            :max="20"
          />
        </div>
        <div class="fm-field fm-field-readonly">
          <label class="fm-label">Slug（只读）</label>
          <NInput :value="fmSlug" disabled size="small" />
        </div>
      </div>
    </div>

    <!-- P1-5: body 编辑 + wikilink 补全 -->
    <div class="editor-body">
      <div class="editor-pane">
        <div class="pane-label">
          正文 Markdown
          <span class="wikilink-hint">输入 <code>[[</code> 触发页面链接补全</span>
        </div>
        <div class="editor-textarea-wrapper">
          <NInput
            ref="textareaRef"
            :value="bodyText"
            type="textarea"
            :rows="20"
            :disabled="saving"
            class="editor-textarea"
            placeholder="# 标题\n\n正文内容，使用 [[slug]] 链接到其他页面..."
            @update:value="handleBodyInput"
            @keydown="handleBodyKeydown"
            @blur="closeWikilink"
          />
          <!-- P1-5: wikilink 补全浮层 -->
          <div v-if="wikilinkActive && wikilinkOptions.length > 0" class="wikilink-popover">
            <div class="wikilink-popover-header">页面链接补全</div>
            <div class="wikilink-popover-list">
              <button
                v-for="opt in wikilinkOptions"
                :key="opt.value"
                type="button"
                class="wikilink-option"
                @mousedown.prevent="handleWikilinkSelect(opt.value)"
              >
                <span class="wikilink-option-title">{{ opt.label }}</span>
                <span class="wikilink-option-slug">{{ opt.value }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
      <div class="preview-pane">
        <div class="pane-label">预览</div>
        <NCard size="small" class="preview-card">
          <div class="preview-content" v-html="previewHtml"></div>
        </NCard>
      </div>
    </div>

    <div class="editor-footer">
      <NInput
        v-model:value="changeSummary"
        placeholder="变更摘要（可选）"
        :disabled="saving"
        class="summary-input"
      />
      <NText depth="3" class="dirty-hint">
        <span v-if="isDirty">● 未保存</span>
        <span v-else>已保存</span>
      </NText>
    </div>
  </div>
</template>

<style scoped>
.wiki-editor {
  border: 1px solid var(--n-border-color, #e5e7eb);
  border-radius: 8px;
  padding: 16px;
  background: var(--n-color, #fff);
}

.editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--n-border-color, #e5e7eb);
}

.editor-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--n-text-color, #111827);
}

.editor-actions {
  flex-shrink: 0;
}

.shortcut-hint {
  font-size: 11px;
  opacity: 0.6;
  margin-left: 4px;
}

.editor-error,
.editor-warning,
.editor-draft {
  margin-bottom: 12px;
}

.editor-draft :deep(.draft-content) {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.editor-draft :deep(.draft-message) {
  font-size: 13px;
  line-height: 1.6;
}

.editor-draft :deep(.draft-actions) {
  flex-shrink: 0;
}

/* P1-5: frontmatter 结构化表单 */
.frontmatter-form {
  margin-bottom: 16px;
  padding: 12px;
  background: var(--n-color-target, rgba(0, 0, 0, 0.02));
  border-radius: 6px;
  border: 1px solid var(--n-border-color, #e5e7eb);
}

.fm-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.fm-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.fm-field-full {
  grid-column: 1 / -1;
}

.fm-field-readonly {
  grid-column: 1 / -1;
}

.fm-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--n-text-color-3, #6b7280);
}

.editor-body {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 12px;
}

.editor-pane,
.preview-pane {
  display: flex;
  flex-direction: column;
}

.pane-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--n-text-color-3, #6b7280);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.wikilink-hint {
  font-size: 11px;
  color: var(--n-text-color-3, #9ca3af);
}

.wikilink-hint code {
  background: var(--n-color-target, rgba(0, 0, 0, 0.05));
  padding: 1px 4px;
  border-radius: 3px;
  font-family: 'SFMono-Regular', Consolas, monospace;
}

.editor-textarea-wrapper {
  position: relative;
  flex: 1;
}

.editor-textarea {
  width: 100%;
  height: 100%;
}

.editor-textarea :deep(textarea) {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
  line-height: 1.6;
}

/* P1-5: wikilink 补全浮层 */
.wikilink-popover {
  position: absolute;
  z-index: 100;
  top: 4px;
  left: 4px;
  min-width: 280px;
  max-height: 240px;
  overflow-y: auto;
  background: var(--n-color, #fff);
  border: 1px solid var(--n-border-color, #e5e7eb);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.wikilink-popover-header {
  font-size: 11px;
  font-weight: 600;
  color: var(--n-text-color-3, #9ca3af);
  padding: 6px 10px 4px;
  border-bottom: 1px solid var(--n-border-color, #f0f0f0);
}

.wikilink-popover-list {
  display: flex;
  flex-direction: column;
}

.wikilink-option {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border: none;
  background: none;
  cursor: pointer;
  text-align: left;
  width: 100%;
  font-size: 13px;
}

.wikilink-option:hover {
  background: var(--n-color-hover, rgba(0, 0, 0, 0.04));
}

.wikilink-option-title {
  color: var(--n-text-color, #111827);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wikilink-option-slug {
  color: var(--n-text-color-3, #9ca3af);
  font-size: 11px;
  font-family: 'SFMono-Regular', Consolas, monospace;
  flex-shrink: 0;
}

.preview-card {
  flex: 1;
  overflow-y: auto;
  max-height: 480px;
}

.preview-content {
  font-size: 14px;
  line-height: 1.7;
  color: var(--n-text-color, #1f2937);
}

.preview-content :deep(h1) {
  font-size: 20px;
  font-weight: 600;
  margin: 16px 0 10px;
}

.preview-content :deep(h2) {
  font-size: 17px;
  font-weight: 600;
  margin: 14px 0 8px;
}

.preview-content :deep(p) {
  margin: 8px 0;
}

.preview-content :deep(code) {
  background: var(--n-color-info-weak, #eff6ff);
  padding: 2px 4px;
  border-radius: 3px;
  font-size: 12px;
}

.editor-footer {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--n-border-color, #e5e7eb);
}

.summary-input {
  flex: 1;
}

.dirty-hint {
  font-size: 12px;
  white-space: nowrap;
}

@media (max-width: 768px) {
  .editor-body {
    grid-template-columns: 1fr;
  }

  .fm-grid {
    grid-template-columns: 1fr;
  }
}
</style>
