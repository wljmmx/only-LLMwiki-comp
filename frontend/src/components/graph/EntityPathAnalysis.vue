<script setup lang="ts">
import { ref, computed } from 'vue'
import {
  NCard,
  NInput,
  NButton,
  NSelect,
  NTag,
  NSpin,
  NEmpty,
  useMessage,
} from 'naive-ui'
import { getShortestPath, getImpactPropagation } from '@/api/graph'
import type { ShortestPathResult, ImpactPropagationResult } from '@/api/graph'

const props = defineProps<{
  entityName: string
}>()

const emit = defineEmits<{
  (e: 'node-click', nodeName: string): void
}>()

const message = useMessage()

type AnalysisMode = 'shortest-path' | 'impact-propagation'

const mode = ref<AnalysisMode>('shortest-path')
const targetEntity = ref('')
const searchDepth = ref(5)
const loading = ref(false)
const pathResult = ref<ShortestPathResult | null>(null)
const impactResult = ref<ImpactPropagationResult | null>(null)

const depthOptions = computed(() =>
  Array.from({ length: 10 }, (_, i) => ({
    label: `${i + 1} 跳`,
    value: i + 1,
  })),
)

const pathNodes = computed(() => {
  if (!pathResult.value?.found || !pathResult.value.path) return []
  return pathResult.value.path
})

const affectedEntities = computed(() => {
  if (!impactResult.value?.affected_entities) return []
  return [...impactResult.value.affected_entities].sort(
    (a, b) => a.distance - b.distance,
  )
})

const handleNodeClick = (nodeName: string) => {
  emit('node-click', nodeName)
}

const runShortestPath = async () => {
  if (!props.entityName) {
    message.warning('当前实体为空')
    return
  }
  if (!targetEntity.value.trim()) {
    message.warning('请输入目标实体名')
    return
  }
  loading.value = true
  pathResult.value = null
  impactResult.value = null
  try {
    const res = await getShortestPath(
      props.entityName,
      targetEntity.value.trim(),
      searchDepth.value,
    )
    pathResult.value = res
    if (!res.found) {
      message.info(`未找到路径（已搜索 ${res.depth_searched} 跳）`)
    }
  } catch (err: any) {
    message.error(err?.message || '路径分析失败')
  } finally {
    loading.value = false
  }
}

const runImpactPropagation = async () => {
  if (!props.entityName) {
    message.warning('当前实体为空')
    return
  }
  loading.value = true
  pathResult.value = null
  impactResult.value = null
  try {
    const res = await getImpactPropagation(props.entityName, searchDepth.value)
    impactResult.value = res
    if (res.affected_count === 0) {
      message.info('未发现下游影响实体')
    }
  } catch (err: any) {
    message.error(err?.message || '影响传播分析失败')
  } finally {
    loading.value = false
  }
}

const handleAnalyze = () => {
  if (mode.value === 'shortest-path') {
    runShortestPath()
  } else {
    runImpactPropagation()
  }
}

const getEntityTypeTag = (type: string) => {
  const colorMap: Record<string, string> = {
    Host: 'red',
    Service: 'blue',
    Component: 'green',
    Parameter: 'yellow',
    Command: 'orange',
    Procedure: 'purple',
    Incident: 'error',
    Symptom: 'warning',
    Experience: 'info',
    Concept: 'default',
    Document: 'success',
  }
  return colorMap[type] || 'default'
}
</script>

<template>
  <div class="entity-path-analysis">
    <div class="header-row">
      <span class="label">图谱路径分析</span>
    </div>
      <div class="current-entity-row">
        <span class="label">当前实体：</span>
        <NTag v-if="entityName" :bordered="false" size="small" round>
          {{ entityName }}
        </NTag>
        <span v-else class="empty-hint">未选择实体</span>
      </div>

      <div class="mode-switch">
        <NButton
          :type="mode === 'shortest-path' ? 'primary' : 'default'"
          size="small"
          @click="mode = 'shortest-path'"
        >
          最短路径
        </NButton>
        <NButton
          :type="mode === 'impact-propagation' ? 'primary' : 'default'"
          size="small"
          @click="mode = 'impact-propagation'"
        >
          影响传播
        </NButton>
      </div>

      <div v-if="mode === 'shortest-path'" class="form-row">
        <span class="field-label">目标实体</span>
        <NInput
          v-model:value="targetEntity"
          placeholder="输入目标实体名称"
          clearable
        />
      </div>

      <div class="form-row">
        <span class="field-label">搜索深度</span>
        <NSelect
          v-model:value="searchDepth"
          :options="depthOptions"
          size="small"
          style="width: 120px"
        />
      </div>

      <div class="action-row">
        <NButton
          type="primary"
          size="small"
          :loading="loading"
          @click="handleAnalyze"
        >
          {{ mode === 'shortest-path' ? '查找最短路径' : '分析影响传播' }}
        </NButton>
      </div>

      <div class="result-area">
        <NSpin v-if="loading" :size="18">
          <template #description>分析中...</template>
        </NSpin>

        <template v-else-if="mode === 'shortest-path' && pathResult">
          <NCard
            v-if="pathResult.found && pathNodes.length > 0"
            title="分析结果"
            size="small"
            :bordered="false"
          >
            <div class="path-chain">
              <template v-for="(node, idx) in pathNodes" :key="node.name + idx">
                <NTag
                  :type="getEntityTypeTag(node.type) as any"
                  :bordered="false"
                  round
                  size="small"
                  class="path-node"
                  @click="handleNodeClick(node.name)"
                >
                  {{ node.name }}
                  <span class="node-type">· {{ node.type }}</span>
                </NTag>
                <span
                  v-if="idx < pathNodes.length - 1"
                  class="path-arrow"
                >→</span>
              </template>
            </div>
            <div class="path-meta">
              <NTag size="tiny" round :bordered="false">
                路径长度: {{ pathResult.length }}
              </NTag>
              <NTag size="tiny" round :bordered="false">
                搜索深度: {{ pathResult.depth_searched }}
              </NTag>
            </div>
          </NCard>

          <NEmpty
            v-else-if="!pathResult.found"
            description="未找到连通路径"
          />

          <div v-else-if="pathResult.error" class="error-hint">
            {{ pathResult.error }}
          </div>
        </template>

        <template v-else-if="mode === 'impact-propagation' && impactResult">
          <NCard
            v-if="impactResult.affected_count > 0"
            title="影响传播结果"
            size="small"
            :bordered="false"
          >
            <div class="impact-summary">
              共发现
              <NTag :bordered="false" round size="small">
                {{ impactResult.affected_count }}
              </NTag>
              个受影响实体
            </div>
            <div class="impact-list">
              <div
                v-for="entity in affectedEntities"
                :key="entity.name"
                class="impact-item"
                @click="handleNodeClick(entity.name)"
              >
                <NTag
                  :type="getEntityTypeTag(entity.type) as any"
                  :bordered="false"
                  round
                  size="small"
                  class="impact-node"
                >
                  {{ entity.name }}
                </NTag>
                <NTag size="tiny" :bordered="false" class="distance-tag">
                  {{ entity.distance }} 跳
                </NTag>
              </div>
            </div>
          </NCard>

          <NEmpty
            v-else
            description="未发现下游影响实体"
          />

          <div v-if="impactResult.error" class="error-hint">
            {{ impactResult.error }}
          </div>
        </template>

        <NEmpty
          v-else-if="!loading && !pathResult && !impactResult"
          description="点击上方按钮开始分析"
        />
      </div>
  </div>
</template>

<style scoped>
.entity-path-analysis {
  display: flex;
  flex-direction: column;
  gap: var(--opskg-sp-3, 12px);
  padding: 4px 0;
}

.current-entity-row {
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-2, 8px);
  padding: var(--opskg-sp-2, 8px) var(--opskg-sp-3, 12px);
  background: var(--opskg-color-embedded-highlight, rgba(24, 160, 88, 0.08));
  border-radius: 6px;
  font-size: var(--opskg-fs-sm, 13px);
}

.current-entity-row .label {
  color: var(--opskg-text-3, #666);
  font-weight: 500;
}

.empty-hint {
  color: var(--opskg-text-3, #999);
  font-size: var(--opskg-fs-sm, 13px);
}

.mode-switch {
  display: flex;
  gap: var(--opskg-sp-2, 8px);
}

.form-row {
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-2, 8px);
}

.field-label {
  font-size: var(--opskg-fs-sm, 13px);
  color: var(--opskg-text-2, #555);
  min-width: 72px;
  flex-shrink: 0;
}

.action-row {
  display: flex;
  justify-content: flex-end;
}

.result-area {
  min-height: 100px;
  margin-top: var(--opskg-sp-2, 8px);
}

.path-chain {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--opskg-sp-1, 4px);
}

.path-node {
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
  user-select: none;
}

.path-node:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}

.path-node .node-type {
  opacity: 0.7;
  margin-left: 2px;
  font-size: var(--opskg-fs-xs, 11px);
}

.path-arrow {
  color: var(--opskg-text-3, #999);
  font-size: var(--opskg-fs-md, 14px);
}

.path-meta {
  display: flex;
  gap: var(--opskg-sp-2, 8px);
  margin-top: var(--opskg-sp-3, 12px);
}

.impact-summary {
  font-size: var(--opskg-fs-sm, 13px);
  color: var(--opskg-text-2, #555);
  margin-bottom: var(--opskg-sp-2, 8px);
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-1, 4px);
}

.impact-list {
  display: flex;
  flex-direction: column;
  gap: var(--opskg-sp-1, 4px);
  max-height: 240px;
  overflow-y: auto;
}

.impact-item {
  display: flex;
  align-items: center;
  gap: var(--opskg-sp-2, 8px);
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.impact-item:hover {
  background: var(--opskg-color-embedded-highlight, rgba(24, 160, 88, 0.08));
}

.distance-tag {
  margin-left: auto;
}

.error-hint {
  color: var(--opskg-color-error, #d03050);
  font-size: var(--opskg-fs-sm, 13px);
  padding: var(--opskg-sp-2, 8px);
}
</style>