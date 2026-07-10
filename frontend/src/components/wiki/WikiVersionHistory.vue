<script setup lang="ts">
/**
 * P1-11: Wiki 页面版本历史抽屉
 *
 * 展示某 wiki 页面的所有历史版本，支持：
 * - 查看版本列表（版本号/作者/时间/变更摘要）
 * - 对比任意两个版本（unified diff）
 * - 回滚到指定版本（非破坏式，创建新版本）
 *
 * 后端 API 复用通用版本控制 /versions/{doc_key}，doc_key = `wiki:${slug}`。
 */
import { ref, watch, computed, h } from 'vue'
import {
  NDrawer,
  NDrawerContent,
  NDataTable,
  NButton,
  NSpace,
  NSpin,
  NTag,
  NPopconfirm,
  NCode,
  NEmpty,
  NRadioGroup,
  NRadioButton,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  listVersions,
  diffVersions,
  rollbackVersion,
  type VersionMeta,
  type DiffResponse,
} from '@/api/versions'
import { formatDate } from '@/utils/format'

interface Props {
  show: boolean
  slug: string
}
const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:show', v: boolean): void
  (e: 'rollback'): void
}>()

const message = useMessage()

const loading = ref(false)
const versions = ref<VersionMeta[]>([])

// diff 状态
const diffLoading = ref(false)
const diffResult = ref<DiffResponse | null>(null)
const selectedVersions = ref<[number | null, number | null]>([null, null])

// 回滚状态
const rollingBack = ref(false)

const docKey = computed(() => `wiki:${props.slug}`)

/** 是否已选两个版本可对比 */
const canDiff = computed(
  () =>
    selectedVersions.value[0] !== null &&
    selectedVersions.value[1] !== null &&
    selectedVersions.value[0] !== selectedVersions.value[1],
)

const columns = computed<DataTableColumns<VersionMeta>>(() => [
  { title: '版本', key: 'version', width: 70 },
  {
    title: '选择对比',
    key: 'select',
    width: 120,
    render(row) {
      return h(
        NRadioGroup,
        {
          value: selectedVersions.value[0] === row.version
            ? 'v1'
            : selectedVersions.value[1] === row.version
              ? 'v2'
              : null,
          'onUpdate:value': (val: string | null) => {
            if (val === 'v1') selectedVersions.value[0] = row.version
            else if (val === 'v2') selectedVersions.value[1] = row.version
            else {
              // 取消选择
              if (selectedVersions.value[0] === row.version) selectedVersions.value[0] = null
              if (selectedVersions.value[1] === row.version) selectedVersions.value[1] = null
            }
            diffResult.value = null
          },
          size: 'small',
        },
        {
          default: () => [h(NRadioButton, { value: 'v1', label: 'A' }), h(NRadioButton, { value: 'v2', label: 'B' })],
        },
      )
    },
  },
  { title: '作者', key: 'author', width: 100, ellipsis: { tooltip: true } },
  {
    title: '变更摘要',
    key: 'change_summary',
    ellipsis: { tooltip: true },
    render(row) {
      return row.change_summary || '—'
    },
  },
  {
    title: '时间',
    key: 'created_at',
    width: 150,
    render(row) {
      return formatDate(row.created_at)
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    fixed: 'right',
    render(row) {
      // 最新版本不可回滚到自己
      const isLatest = row.version === versions.value[0]?.version
      if (isLatest) {
        return h(NTag, { size: 'small', type: 'info' }, { default: () => '当前' })
      }
      return h(
        NPopconfirm,
        {
          onPositiveClick: () => handleRollback(row.version),
        },
        {
          default: () => `确认回滚到版本 ${row.version}？（将创建新版本，不删除历史）`,
          trigger: () =>
            h(
              NButton,
              { size: 'small', quaternary: true, type: 'warning', loading: rollingBack.value },
              { default: () => '回滚' },
            ),
        },
      )
    },
  },
])

/** 加载版本列表 */
async function loadVersions() {
  if (!props.slug) return
  loading.value = true
  diffResult.value = null
  selectedVersions.value = [null, null]
  try {
    const res = await listVersions(docKey.value)
    // 后端返回按 version DESC，最新在前
    versions.value = res.versions
  } catch (e: any) {
    message.error('加载版本历史失败')
  } finally {
    loading.value = false
  }
}

/** 对比选中的两个版本 */
async function handleDiff() {
  const [v1, v2] = selectedVersions.value
  if (v1 === null || v2 === null || v1 === v2) return
  diffLoading.value = true
  diffResult.value = null
  try {
    // 确保 v1 < v2（旧 → 新），diff 更直观
    const [older, newer] = v1 < v2 ? [v1, v2] : [v2, v1]
    const res = await diffVersions(docKey.value, older, newer)
    if (res.error) {
      message.warning(res.error)
    } else {
      diffResult.value = res
    }
  } catch {
    message.error('版本对比失败')
  } finally {
    diffLoading.value = false
  }
}

/** 回滚到指定版本 */
async function handleRollback(targetVersion: number) {
  rollingBack.value = true
  try {
    await rollbackVersion(docKey.value, targetVersion)
    message.success(`已回滚到版本 ${targetVersion}（已创建新版本）`)
    emit('rollback')
    // 重新加载版本列表（回滚后会有新版本）
    await loadVersions()
  } catch {
    message.error('回滚失败')
  } finally {
    rollingBack.value = false
  }
}

// show 打开或 slug 变化时加载
watch(
  () => [props.show, props.slug],
  ([show]) => {
    if (show) loadVersions()
  },
  { immediate: true },
)

function handleClose() {
  emit('update:show', false)
}
</script>

<template>
  <NDrawer
    :show="show"
    :width="'min(720px, 90vw)'"
    placement="right"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <NDrawerContent title="版本历史" closable>
      <NSpin :show="loading">
        <NEmpty v-if="!loading && versions.length === 0" description="暂无版本历史" />
        <template v-else>
          <NDataTable
            :columns="columns"
            :data="versions"
            :row-key="(row: VersionMeta) => row.version"
            :max-height="320"
            size="small"
            :scroll-x="600"
          />

          <!-- 对比操作栏 -->
          <NSpace align="center" style="margin-top: 12px" v-if="canDiff">
            <NButton type="primary" size="small" :loading="diffLoading" @click="handleDiff">
              对比版本 A{{ selectedVersions[0] }} ↔ B{{ selectedVersions[1] }}
            </NButton>
            <span class="diff-hint">选择两个不同版本进行对比</span>
          </NSpace>

          <!-- Diff 结果 -->
          <div v-if="diffLoading" class="diff-section">
            <NSpin size="small" />
          </div>
          <div v-else-if="diffResult" class="diff-section">
            <NSpace align="center" style="margin-bottom: 8px">
              <NTag type="success" size="small">+{{ diffResult.added_lines }} 行</NTag>
              <NTag type="error" size="small">-{{ diffResult.removed_lines }} 行</NTag>
            </NSpace>
            <NCode :code="diffResult.diff" language="diff" word-wrap class="diff-code" />
          </div>
        </template>
      </NSpin>

      <template #footer>
        <NSpace>
          <NButton @click="handleClose">关闭</NButton>
        </NSpace>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.diff-section {
  margin-top: 16px;
}

.diff-hint {
  font-size: 12px;
  color: var(--opskg-text-3, #9ca3af);
}

.diff-code {
  max-height: 400px;
  overflow: auto;
  font-size: 12px;
  padding: 12px;
  border-radius: var(--opskg-radius-md, 8px);
}
</style>
