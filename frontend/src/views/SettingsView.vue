<template>
  <div class="settings-page">
    <div class="page-header">
      <h2>系统配置</h2>
      <n-space>
        <n-button @click="handleValidate" :loading="validating" secondary>
          验证配置
        </n-button>
        <n-button type="primary" @click="handleSave" :loading="saving">
          保存配置
        </n-button>
      </n-space>
    </div>

    <n-card v-if="loading" class="loading-card">
      <n-spin size="large" />
    </n-card>

    <n-card v-else-if="error" class="error-card">
      <n-result status="error" title="加载失败" :description="error">
        <template #footer>
          <n-button @click="loadSettings">重试</n-button>
        </template>
      </n-result>
    </n-card>

    <n-tabs v-else type="segment" animated>
      <n-tab-pane
        v-for="(group, key) in groups"
        :key="key"
        :name="key"
        :tab="group.label"
      >
        <!-- LLM 配置：活跃后端指示 + 测试按钮 -->
        <n-alert
          v-if="key === 'llm'"
          :type="llmTestResult?.success ? 'success' : llmTestResult ? 'error' : 'info'"
          :bordered="false"
          class="llm-status-bar"
        >
          <template #header>
            <div class="llm-status-header">
              <span>
                当前活跃后端：
                <strong>{{ activeBackend }}</strong>
                <template v-if="activeBackend === 'openai_compat'">
                  · {{ groups.llm?.items?.openai_compat_model?.value || '-' }}
                </template>
                <template v-else-if="activeBackend === 'ollama'">
                  · {{ groups.llm?.items?.ollama_model?.value || '-' }}
                </template>
                <template v-else-if="activeBackend === 'vllm'">
                  · {{ groups.llm?.items?.vllm_model?.value || '-' }}
                </template>
              </span>
              <n-space :size="8">
                <n-button
                  size="small"
                  :loading="llmTesting"
                  @click="handleLLMTest"
                >
                  测试连接
                </n-button>
              </n-space>
            </div>
          </template>
          <template v-if="llmTestResult" #default>
            <div class="llm-test-detail">
              <span v-if="llmTestResult.success">
                连接成功 · {{ llmTestResult.model }} · {{ llmTestResult.latency_ms }}ms
              </span>
              <span v-else>
                连接失败<template v-if="llmTestResult.errors?.length">：{{ llmTestResult.errors.join('；') }}</template>
              </span>
            </div>
          </template>
        </n-alert>

        <n-card :title="group.label" :bordered="false" size="small">
          <n-form label-placement="left" label-width="180" :show-feedback="true">
            <n-form-item
              v-for="(field, fkey) in group.items"
              :key="fkey"
              :label="field.meta.label || fkey"
              :feedback="field.meta.description"
            >
              <!-- 下拉选择 -->
              <n-select
                v-if="field.meta.type === 'select'"
                :value="String(field.value)"
                :options="(field.meta.options || []).map((o: string) => ({ label: o, value: o }))"
                @update:value="(v: string) => setFieldValue(key, fkey, v)"
                style="max-width: 400px"
              />

              <!-- 密码输入 -->
              <n-input
                v-else-if="field.meta.sensitive"
                type="password"
                show-password-on="click"
                :value="String(field.value)"
                :placeholder="'输入新值（留空不修改）'"
                @update:value="(v: string) => setFieldValue(key, fkey, v)"
                style="max-width: 400px"
              />

              <!-- 整数 -->
              <n-input-number
                v-else-if="field.meta.type === 'int'"
                :value="Number(field.value)"
                :min="field.meta.range?.[0] ?? undefined"
                :max="field.meta.range?.[1] ?? undefined"
                @update:value="(v: number | null) => { if (v !== null) setFieldValue(key, fkey, v) }"
                style="max-width: 200px"
              />

              <!-- 浮点 -->
              <n-input-number
                v-else-if="field.meta.type === 'float'"
                :value="Number(field.value)"
                :step="0.01"
                :min="field.meta.range?.[0] ?? undefined"
                :max="field.meta.range?.[1] ?? undefined"
                @update:value="(v: number | null) => { if (v !== null) setFieldValue(key, fkey, v) }"
                style="max-width: 200px"
              />

              <!-- 布尔 -->
              <n-switch
                v-else-if="field.meta.type === 'bool'"
                :value="Boolean(field.value)"
                @update:value="(v: boolean) => setFieldValue(key, fkey, v)"
              />

              <!-- 默认：文本 -->
              <n-input
                v-else
                :value="String(field.value)"
                @update:value="(v: string) => setFieldValue(key, fkey, v)"
                style="max-width: 400px"
              />
            </n-form-item>
          </n-form>
        </n-card>
      </n-tab-pane>
    </n-tabs>

    <!-- 保存确认对话框 -->
    <n-modal v-model:show="showRestartModal" preset="dialog" title="配置已保存">
      <div class="restart-hint">
        <p>配置已写入 <code>.env</code> 文件，需要重启服务才能生效。</p>
        <p>是否立即重启？</p>
      </div>
      <template #action>
        <n-space>
          <n-button @click="showRestartModal = false">稍后手动重启</n-button>
          <n-button type="primary" @click="handleRestart" :loading="restarting">
            立即重启
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  NButton, NCard, NForm, NFormItem, NInput, NInputNumber,
  NSelect, NSpace, NSpin, NSwitch, NTabPane, NTabs, NModal,
  NResult, NAlert, useMessage,
} from 'naive-ui'
import {
  getSettings, updateSettings, validateSettings, restartService,
  testLLMConnection, type SettingsGroup, type LLMTestResponse,
} from '@/api/settings'

const message = useMessage()

const loading = ref(true)
const saving = ref(false)
const validating = ref(false)
const restarting = ref(false)
const error = ref('')
const showRestartModal = ref(false)
const groups = ref<Record<string, SettingsGroup>>({})
const pendingChanges = ref<Record<string, string | number | boolean>>({})

// LLM 测试
const llmTesting = ref(false)
const llmTestResult = ref<LLMTestResponse | null>(null)

/** 当前活跃的 LLM 后端标识 */
const activeBackend = computed(() => {
  const backend = groups.value?.llm?.items?.llm_backend?.value
  return String(backend || '')
})

async function loadSettings() {
  loading.value = true
  error.value = ''
  try {
    const res = await getSettings()
    groups.value = res.groups
    pendingChanges.value = {}
  } catch (err: any) {
    error.value = err?.response?.data?.detail || err?.message || '加载配置失败'
  } finally {
    loading.value = false
  }
}

function setFieldValue(groupKey: string, fieldKey: string, value: string | number | boolean) {
  // 更新 groups 展示
  if (groups.value[groupKey]?.items[fieldKey]) {
    groups.value[groupKey].items[fieldKey].value = value
  }
  // 记录变更
  pendingChanges.value[fieldKey] = value
}

async function handleValidate() {
  if (Object.keys(pendingChanges.value).length === 0) {
    message.info('没有待验证的变更')
    return
  }
  validating.value = true
  try {
    const res = await validateSettings({ updates: pendingChanges.value })
    if (res.valid) {
      message.success('配置验证通过')
    } else {
      message.error('验证失败：' + res.errors.join('；'))
    }
  } catch (err: any) {
    message.error('验证失败：' + (err?.response?.data?.detail || err?.message))
  } finally {
    validating.value = false
  }
}

async function handleSave() {
  if (Object.keys(pendingChanges.value).length === 0) {
    message.info('没有待保存的变更')
    return
  }
  saving.value = true
  try {
    const res = await updateSettings({ updates: pendingChanges.value })
    message.success(`已保存 ${res.updated.length} 项配置变更`)
    pendingChanges.value = {}
    showRestartModal.value = true
  } catch (err: any) {
    message.error('保存失败：' + (err?.response?.data?.detail || err?.message))
  } finally {
    saving.value = false
  }
}

async function handleRestart() {
  restarting.value = true
  try {
    const res = await restartService()
    message.success(res.message)
    showRestartModal.value = false
  } catch (err: any) {
    message.error('重启失败：' + (err?.response?.data?.detail || err?.message))
  } finally {
    restarting.value = false
  }
}

/** 测试 LLM 后端连通性 */
async function handleLLMTest() {
  llmTesting.value = true
  llmTestResult.value = null
  try {
    const res = await testLLMConnection()
    llmTestResult.value = res
    if (res.success) {
      message.success(res.message)
    } else {
      message.error(res.message)
    }
  } catch (err: any) {
    llmTestResult.value = {
      success: false,
      backend: activeBackend.value,
      model: '',
      base_url: '',
      latency_ms: 0,
      message: '测试请求失败',
      errors: [err?.response?.data?.detail || err?.message || '网络错误'],
    }
    message.error('LLM 测试失败')
  } finally {
    llmTesting.value = false
  }
}

onMounted(loadSettings)
</script>

<style scoped>
.settings-page {
  padding: 16px 24px;
  max-width: 900px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
}
.loading-card, .error-card {
  margin-top: 48px;
  text-align: center;
}
.restart-hint {
  line-height: 1.8;
}
.restart-hint code {
  background: var(--n-color-target);
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 13px;
}

.llm-status-bar {
  margin-bottom: 12px;
}

.llm-status-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.llm-test-detail {
  font-size: 13px;
  margin-top: 4px;
}
</style>