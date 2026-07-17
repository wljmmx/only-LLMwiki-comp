<template>
  <div class="settings-page">
    <PageHeader title="系统配置">
      <template #actions>
        <n-space>
          <n-button @click="handleValidate" :loading="validating" secondary>
            验证配置
          </n-button>
          <n-button type="primary" @click="handleSave" :loading="saving">
            保存配置
          </n-button>
        </n-space>
      </template>
    </PageHeader>

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
        <n-card :title="group.label" :bordered="false" size="small">
          <!-- LLM 配置组显示测试按钮 -->
          <div v-if="key === 'llm'" class="llm-test-bar">
            <n-button
              type="primary"
              @click="handleTestLLM"
              :loading="testingLLM"
              :disabled="testingLLM"
            >
              <template #icon>
                <n-icon><component :is="TestIcon" /></n-icon>
              </template>
              测试 LLM 连通性
            </n-button>
            <div v-if="llmTestResult" class="llm-test-result" :class="{ success: llmTestResult.success, error: !llmTestResult.success }">
              <n-icon><component :is="llmTestResult.success ? CheckIcon : AlertIcon" /></n-icon>
              <span>{{ llmTestResult.message }}</span>
              <span v-if="llmTestResult.latency_ms" class="latency">耗时 {{ llmTestResult.latency_ms }}ms</span>
            </div>
          </div>
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
import { ref, onMounted } from 'vue'
import {
  NButton, NCard, NForm, NFormItem, NInput, NInputNumber,
  NSelect, NSpace, NSpin, NSwitch, NTabPane, NTabs, NModal,
  NResult, useMessage, NIcon,
} from 'naive-ui'
import { CheckmarkDoneOutline as CheckIcon, AlertCircleOutline as AlertIcon, BuildOutline as TestIcon } from '@vicons/ionicons5'
import {
  getSettings, updateSettings, validateSettings, restartService, testLLMConnection,
  type SettingsGroup, type TestLLMConnectionResponse,
} from '@/api/settings'
import PageHeader from '@/components/common/PageHeader.vue'

const message = useMessage()

const loading = ref(true)
const saving = ref(false)
const validating = ref(false)
const restarting = ref(false)
const testingLLM = ref(false)
const error = ref('')
const showRestartModal = ref(false)
const groups = ref<Record<string, SettingsGroup>>({})
const pendingChanges = ref<Record<string, string | number | boolean>>({})
const llmTestResult = ref<TestLLMConnectionResponse | null>(null)

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

async function handleTestLLM() {
  testingLLM.value = true
  llmTestResult.value = null
  try {
    const res = await testLLMConnection()
    llmTestResult.value = res
    if (res.success) {
      message.success(res.message)
    } else {
      message.error(res.message + (res.errors ? '：' + res.errors.join('；') : ''))
    }
  } catch (err: any) {
    llmTestResult.value = {
      success: false,
      backend: '',
      model: '',
      base_url: '',
      latency_ms: 0,
      message: '测试失败：' + (err?.response?.data?.detail || err?.message),
    }
    message.error(llmTestResult.value.message)
  } finally {
    testingLLM.value = false
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
.llm-test-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--n-border-color);
}
.llm-test-result {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}
.llm-test-result.success {
  background: rgba(24, 160, 88, 0.1);
  color: var(--n-success-color);
}
.llm-test-result.error {
  background: rgba(208, 48, 80, 0.1);
  color: var(--n-error-color);
}
.llm-test-result .latency {
  margin-left: 8px;
  opacity: 0.7;
  font-size: 12px;
}
</style>