<script setup lang="ts">
import { h } from 'vue'
import {
  NDataTable,
  NButton,
  NTag,
  NSpace,
  NPopconfirm,
  NEmpty,
} from 'naive-ui'
import { formatFileSize, formatDateTime as formatDateTimeUtil } from '@/utils/format'
import type { DocumentMeta } from '@/types/api'

const props = defineProps<{
  documents: DocumentMeta[]
  loading: boolean
  total: number
  offset: number
  limit: number
  isSearching: boolean
  checkedRowKeys: string[]
  batchLoading: boolean
}>()

const emit = defineEmits<{
  'update:checkedRowKeys': [value: string[]]
  view: [row: DocumentMeta]
  delete: [row: DocumentMeta]
  compile: [row: DocumentMeta]
  'batch-delete': []
  'batch-compile': []
  'clear-selection': []
  'page-change': [page: number]
  'page-size-change': [size: number]
}>()

const statusTagType: Record<string, 'default' | 'info' | 'success' | 'warning' | 'error'> = {
  uploaded: 'default',
  parsed: 'info',
  extracted: 'warning',
  compiled: 'success',
  error: 'error',
}

const statusText: Record<string, string> = {
  uploaded: '已上传',
  parsed: '已解析',
  extracted: '已抽取',
  compiled: '已编译',
  error: '失败',
}

// P0: 文件格式图标映射
const formatIcons: Record<string, string> = {
  md: '📝',
  txt: '📄',
  docx: '📘',
  xlsx: '📊',
  pdf: '📕',
  html: '🌐',
  sql: '🗄️',
  csv: '📋',
  json: '📦',
  xml: '📰',
  yaml: '⚙️',
  yml: '⚙️',
  log: '📜',
  conf: '🔧',
}

function getFormatIcon(format: string): string {
  return formatIcons[format.toLowerCase()] || '📎'
}

const columns = [
  {
    type: 'selection' as const,
    width: 50,
  },
  {
    title: '文件名',
    key: 'filename',
    ellipsis: { tooltip: true },
  },
  {
    title: '格式',
    key: 'format',
    width: 100,
    render(row: DocumentMeta) {
      return h('span', {}, `${getFormatIcon(row.format)} ${row.format.toUpperCase()}`)
    },
  },
  {
    title: '大小',
    key: 'size',
    width: 120,
    render(row: DocumentMeta) {
      return formatFileSize(row.size)
    },
  },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render(row: DocumentMeta) {
      return h(
        NTag,
        { type: statusTagType[row.status], size: 'small' },
        { default: () => statusText[row.status] },
      )
    },
  },
  {
    title: '上传时间',
    key: 'created_at',
    width: 180,
    render(row: DocumentMeta) {
      return formatDateTimeUtil(row.created_at)
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 240,
    render(row: DocumentMeta) {
      return h(
        NSpace,
        { size: 'small' },
        {
          default: () => [
            h(
              NButton,
              { size: 'small', type: 'primary', quaternary: true, onClick: () => emit('view', row) },
              { default: () => '查看' },
            ),
            h(
              NButton,
              {
                size: 'small',
                type: 'info',
                quaternary: true,
                onClick: () => emit('compile', row),
              },
              { default: () => '编译为Wiki' },
            ),
            h(
              NPopconfirm,
              {
                onPositiveClick: () => emit('delete', row),
              },
              {
                trigger: () => h(
                  NButton,
                  { size: 'small', type: 'error', quaternary: true },
                  { default: () => '删除' },
                ),
                default: () => `确定删除文档 ${row.filename}？此操作不可撤销`,
              },
            ),
          ],
        },
      )
    },
  },
]

function onPageChange(page: number) {
  emit('page-change', page)
}

function onPageSizeChange(size: number) {
  emit('page-size-change', size)
}
</script>

<template>
  <div class="table-container">
    <div v-if="checkedRowKeys.length > 0" class="batch-toolbar">
      <NSpace align="center" size="medium">
        <span class="batch-count">已选 {{ checkedRowKeys.length }} 项</span>
        <NPopconfirm @positive-click="emit('batch-delete')">
          <template #trigger>
            <NButton type="error" :loading="batchLoading" :disabled="batchLoading">
              批量删除
            </NButton>
          </template>
          确定删除选中的 {{ checkedRowKeys.length }} 个文档？此操作不可撤销
        </NPopconfirm>
        <NButton
          type="primary"
          :loading="batchLoading"
          :disabled="batchLoading"
          @click="emit('batch-compile')"
        >
          批量编译为 Wiki
        </NButton>
        <NButton :disabled="batchLoading" @click="emit('clear-selection')">
          取消选择
        </NButton>
      </NSpace>
    </div>

    <NDataTable
      :checked-row-keys="checkedRowKeys"
      :columns="columns"
      :data="documents"
      :loading="loading"
      :row-key="(row: DocumentMeta) => row.id"
      :pagination="isSearching
        ? false
        : {
          page: offset / limit + 1,
          pageSize: limit,
          itemCount: total,
          pageSizes: [10, 20, 50],
          showSizePicker: true,
          onUpdatePage: onPageChange,
          onUpdatePageSize: onPageSizeChange,
      }"
      :bordered="false"
      size="medium"
      @update:checked-row-keys="(keys) => emit('update:checkedRowKeys', keys as string[])"
    >
      <template #empty>
        <NEmpty description="暂无文档" />
      </template>
    </NDataTable>
  </div>
</template>

<style scoped>
.table-container {
  flex: 1;
  background: var(--n-card-color, #fff);
  border-radius: 8px;
  border: 1px solid var(--n-border-color, #e5e7eb);
  padding: 16px;
  overflow: hidden;
}

.batch-toolbar {
  margin-bottom: 12px;
  padding: 10px 12px;
  background: var(--n-color-target, #f0f9ff);
  border: 1px solid var(--n-border-color, #e5e7eb);
  border-radius: 6px;
}

.batch-toolbar .batch-count {
  font-size: 13px;
  font-weight: 500;
  color: var(--n-text-color, #111827);
}
</style>