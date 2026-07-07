<script setup lang="ts">
/**
 * Wiki 页面编辑器组件（S16-2 / S16-5 草稿持久化与冲突恢复）
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
 *
 * 功能：
 * - 双栏布局：左侧 textarea 编辑 Markdown（含 frontmatter），右侧实时预览
 * - 变更摘要输入框（保存时传给后端）
 * - 保存按钮：调用 PUT /llm-wiki/page/{slug}
 *   - 乐观锁：传入 expected_version
 *   - 错误处理：409（锁冲突/版本冲突）展示提示
 * - 取消按钮：emit('cancel')
 *
 * S16-5 草稿持久化与冲突恢复：
 * - 编辑过程中实时把内容 + 服务器版本号持久化到 localStorage（key 按 slug 隔离）
 * - 组件挂载时检查草稿：
 *   - 草稿版本 == 当前服务器版本 → 提示"恢复未保存草稿"
 *   - 草稿版本 != 当前服务器版本 → 提示"草稿与服务器版本不一致"（冲突）
 * - 用户可"恢复"草稿或"丢弃"草稿
 * - 保存成功后自动清除草稿
 *
 * 不管理编辑锁（由 CollabPanel 负责），通过 :can-edit prop 控制保存按钮启用
 */
import { ref, computed, watch } from 'vue'
import { NButton, NInput, NCard, NSpace, NText, NAlert } from 'naive-ui'
import { updateWikiPage } from '@/api/wiki'
import { renderWikiMarkdown } from '@/utils/wikiRender'
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

// 编辑器状态
const editingContent = ref(props.content)
const changeSummary = ref('')
const saving = ref(false)
const errorMsg = ref<string | null>(null)

// S16-5：草稿恢复状态（setup 时同步初始化，确保初始渲染即包含提示）
const draft = useEditDraft(props.slug)
const draftRecovery = ref<EditDraft | null>(draft.draft.value)
const draftConflict = ref(draft.isConflictWith(props.version ?? 0))

// 内容变化时重置（slug 切换）
watch(
  () => props.content,
  (newContent) => {
    editingContent.value = newContent
    errorMsg.value = null
  },
)

// S16-5：编辑内容变化时持久化草稿（仅当有未保存改动 + 有版本号）
watch(
  editingContent,
  (newVal) => {
    if (newVal !== props.content && props.version !== undefined) {
      draft.save(newVal, props.version, changeSummary.value || undefined)
    }
  },
)

// 变更摘要变化也同步到草稿
watch(changeSummary, (newVal) => {
  if (editingContent.value !== props.content && props.version !== undefined) {
    draft.save(editingContent.value, props.version, newVal || undefined)
  }
})

// 预览（renderWikiMarkdown 已 stub 化，测试时返回原文本）
const previewHtml = computed(() => {
  try {
    return renderWikiMarkdown(editingContent.value)
  } catch {
    return editingContent.value
  }
})

// 内容是否有变化
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
    // 保存成功后重置 dirty 状态
    changeSummary.value = ''
    // S16-5：保存成功后清除草稿（避免下次挂载时误恢复已保存内容）
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

function handleCancel() {
  emit('cancel')
}

// S16-5：恢复草稿（用户点击"恢复"按钮）
function handleRestoreDraft() {
  if (!draftRecovery.value) return
  editingContent.value = draftRecovery.value.content
  if (draftRecovery.value.summary) {
    changeSummary.value = draftRecovery.value.summary
  }
  // 恢复后保留草稿（用户可能再次修改），但关闭恢复提示
  draftRecovery.value = null
  draftConflict.value = false
}

// S16-5：丢弃草稿（用户点击"丢弃"按钮）
function handleDiscardDraft() {
  draft.clear()
  draftRecovery.value = null
  draftConflict.value = false
}

// S16-5：格式化草稿保存时间（相对时间，如"3 分钟前"）
function formatDraftTime(ts: number): string {
  const diff = Date.now() - ts
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  const d = new Date(ts)
  return `${d.getMonth() + 1}-${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
</script>

<template>
  <div class="wiki-editor">
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
        </NButton>
      </NSpace>
    </div>

    <NAlert v-if="errorMsg" type="error" :show-icon="true" class="editor-error" closable @close="errorMsg = null">
      {{ errorMsg }}
    </NAlert>

    <!-- S16-5：草稿恢复提示（冲突时 type=warning，正常时 type=info） -->
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

    <div class="editor-body">
      <div class="editor-pane">
        <div class="pane-label">Markdown（含 frontmatter）</div>
        <NInput
          v-model:value="editingContent"
          type="textarea"
          :rows="20"
          :disabled="saving"
          class="editor-textarea"
          placeholder="---\nslug: ...\ntitle: ...\ntype: ...\n---\n\n# 标题\n\n正文"
        />
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
}

.editor-textarea {
  flex: 1;
}

.editor-textarea :deep(textarea) {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
  line-height: 1.6;
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
</style>
