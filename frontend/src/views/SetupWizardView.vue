<script setup lang="ts">
/**
 * 开箱配置向导（Setup Wizard）
 *
 * 多步骤引导用户完成环境配置：
 * 1. 欢迎与配置状态概览
 * 2. LLM 后端选择 + 连通测试
 * 3. Neo4j 配置 + 连通测试
 * 4. 认证配置（可选）
 * 5. 生成可复制的 docker 部署命令 + .env 文件
 *
 * 设计要点：
 * - 不修改后端 .env 文件，仅生成可复制命令，由用户自行执行（安全）
 * - 表单字段在用户填写后即时可用测试连通（无需重启服务）
 * - 生成命令后给出"应用并重启"指引
 */
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard,
  NSteps,
  NStep,
  NButton,
  NSpace,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NSwitch,
  NInputNumber,
  NAlert,
  NTag,
  NCode,
  NDivider,
  NText,
  NSpin,
  NGrid,
  NGi,
  useMessage,
} from 'naive-ui'
import { useSetupStore } from '@/stores/setup'

const router = useRouter()
const store = useSetupStore()
const message = useMessage()

const currentStep = ref(0)
const totalSteps = 5

// ────────── 步骤元数据 ──────────

const stepStatus: 'process' = 'process'

// ────────── LLM 选项 ──────────

const llmBackendOptions = [
  { label: 'OpenAI 兼容 API（DeepSeek / 通义千问 / Moonshot 等）', value: 'openai_compat' },
  { label: 'Ollama（本地推理）', value: 'ollama' },
  { label: 'vLLM（本地高性能推理）', value: 'vllm' },
]

// ────────── 生命周期 ──────────

onMounted(async () => {
  await store.refreshStatus()
})

// ────────── 测试连通 ──────────

async function handleTestLLM() {
  const resp = await store.testLLM()
  if (resp.ok) {
    message.success(`LLM 连通成功（${resp.latency_ms ?? '-'}ms，model: ${resp.model}）`)
  } else {
    message.error(`LLM 连通失败：${resp.error || '未知错误'}`)
  }
}

async function handleTestNeo4j() {
  const resp = await store.testNeo4j()
  if (resp.ok) {
    message.success(
      `Neo4j 连通成功${resp.version ? `（v${resp.version}, ${resp.latency_ms ?? '-'}ms）` : ''}`,
    )
  } else {
    message.error(`Neo4j 连通失败：${resp.error || '未知错误'}`)
  }
}

// ────────── 生成命令 ──────────

async function handleGenerate() {
  try {
    const resp = await store.generateCommand()
    message.success('已生成部署命令，请复制执行')
    return resp
  } catch (err: any) {
    message.error(`生成失败：${err.message || '未知错误'}`)
    throw err
  }
}

// ────────── 步骤导航 ──────────

function handleNext() {
  if (currentStep.value < totalSteps - 1) {
    currentStep.value++
  }
}

function handlePrev() {
  if (currentStep.value > 0) {
    currentStep.value--
  }
}

async function handleFinish() {
  store.dismiss()
  message.success('配置向导已完成')
  router.replace('/dashboard')
}

function handleSkip() {
  store.dismiss()
  router.replace('/dashboard')
}

// 进入最后一步时自动生成命令
watch(currentStep, async (step) => {
  if (step === totalSteps - 1 && !store.commandResult) {
    await handleGenerate()
  }
})

// 切换 backend 时清除上次的 LLM 测试结果
watch(
  () => store.form.llm_backend,
  () => {
    store.llmResult = null
  },
)

// ────────── 复制辅助 ──────────

async function copyToClipboard(text: string, label = '内容') {
  try {
    await navigator.clipboard.writeText(text)
    message.success(`${label}已复制到剪贴板`)
  } catch {
    message.error('复制失败，请手动选择文本复制')
  }
}
</script>

<template>
  <div class="setup-container">
    <div class="setup-box">
      <!-- 头部 -->
      <div class="setup-header">
        <div class="logo-icon">🚀</div>
        <h1 class="setup-title">OpsKG 开箱配置向导</h1>
        <p class="setup-subtitle">
          按步骤填写参数并测试连通，最后生成可一键执行的部署命令
        </p>
      </div>

      <!-- 步骤指示器 -->
      <div class="setup-steps">
        <NSteps :current="currentStep + 1" :status="stepStatus" size="small">
          <NStep title="配置概览" description="检查当前环境" />
          <NStep title="LLM 配置" description="选择后端并测试连通" />
          <NStep title="Neo4j 配置" description="图谱数据库连通" />
          <NStep title="认证配置" description="访问控制与初始管理员" />
          <NStep title="生成命令" description="复制部署命令" />
        </NSteps>
      </div>

      <!-- 内容卡片 -->
      <NCard class="setup-card" :bordered="false">
        <NSpin :show="store.statusLoading">
          <!-- 步骤 1：配置概览 -->
          <div v-if="currentStep === 0" class="step-content">
            <h2 class="step-title">配置概览</h2>
            <p class="step-desc">
              检测到当前环境的配置状态。未配置项会在后续步骤引导你完成。
            </p>

            <NAlert v-if="store.statusError" type="warning" class="step-alert">
              无法获取后端配置状态：{{ store.statusError }}。请确保后端服务已启动。
            </NAlert>

            <NGrid v-if="store.status" :cols="1" :x-gap="12" :y-gap="12">
              <NGi>
                <div class="status-row">
                  <div class="status-info">
                    <NTag :type="store.llmConfigured ? 'success' : 'warning'" size="small">
                      {{ store.llmConfigured ? '已配置' : '未配置' }}
                    </NTag>
                    <NText strong>LLM 后端</NText>
                    <NText depth="3">（{{ store.status.llm_backend }}）</NText>
                  </div>
                  <NText depth="3" class="status-hint">
                    用于知识抽取、Wiki 编译、Q&A
                  </NText>
                </div>
              </NGi>
              <NGi>
                <div class="status-row">
                  <div class="status-info">
                    <NTag :type="store.neo4jConfigured ? 'success' : 'warning'" size="small">
                      {{ store.neo4jConfigured ? '已配置' : '未配置' }}
                    </NTag>
                    <NText strong>Neo4j</NText>
                    <NText depth="3">（{{ store.status.neo4j_uri }}）</NText>
                  </div>
                  <NText depth="3" class="status-hint">
                    知识图谱存储
                  </NText>
                </div>
              </NGi>
              <NGi>
                <div class="status-row">
                  <div class="status-info">
                    <NTag :type="store.authConfigured ? 'success' : 'warning'" size="small">
                      {{ store.authConfigured ? '已配置' : '未配置' }}
                    </NTag>
                    <NText strong>认证 / 管理员</NText>
                  </div>
                  <NText depth="3" class="status-hint">
                    API Token 或 Bootstrap Admin 至少一项
                  </NText>
                </div>
              </NGi>
            </NGrid>

            <NAlert
              v-if="store.ready"
              type="success"
              class="step-alert"
              title="环境已就绪"
            >
              所有必需配置已就位。如需重新生成部署命令，可直接跳到最后一步。
            </NAlert>
            <NAlert
              v-else-if="store.status"
              type="info"
              class="step-alert"
              :title="`待配置：${store.missing.join(', ')}`"
            >
              点击"下一步"按引导完成配置。
            </NAlert>
          </div>

          <!-- 步骤 2：LLM 配置 -->
          <div v-else-if="currentStep === 1" class="step-content">
            <h2 class="step-title">LLM 后端配置</h2>
            <p class="step-desc">
              选择一种 LLM 后端，填写连接参数后点击"测试连通"验证。
            </p>

            <NForm label-placement="top">
              <NFormItem label="LLM 后端">
                <NSelect
                  v-model:value="store.form.llm_backend"
                  :options="llmBackendOptions"
                />
              </NFormItem>

              <!-- openai_compat -->
              <template v-if="store.form.llm_backend === 'openai_compat'">
                <NFormItem label="Base URL">
                  <NInput
                    v-model:value="store.form.openai_compat_base_url"
                    placeholder="https://api.deepseek.com/v1"
                  />
                </NFormItem>
                <NFormItem label="API Key">
                  <NInput
                    v-model:value="store.form.openai_compat_api_key"
                    type="password"
                    show-password-on="click"
                    placeholder="sk-..."
                  />
                </NFormItem>
                <NFormItem label="模型名">
                  <NInput
                    v-model:value="store.form.openai_compat_model"
                    placeholder="deepseek-chat"
                  />
                </NFormItem>
              </template>

              <!-- ollama -->
              <template v-else-if="store.form.llm_backend === 'ollama'">
                <NFormItem label="Ollama Base URL">
                  <NInput
                    v-model:value="store.form.ollama_base_url"
                    placeholder="http://localhost:11434"
                  />
                </NFormItem>
                <NFormItem label="模型名">
                  <NInput
                    v-model:value="store.form.ollama_model"
                    placeholder="qwen2.5:7b"
                  />
                </NFormItem>
                <NAlert type="info" :show-icon="false" class="step-alert">
                  Ollama 无需 API Key。请先执行 <code>ollama pull {{ store.form.ollama_model }}</code> 拉取模型。
                </NAlert>
              </template>

              <!-- vllm -->
              <template v-else>
                <NFormItem label="vLLM Base URL">
                  <NInput
                    v-model:value="store.form.vllm_base_url"
                    placeholder="http://localhost:8000"
                  />
                </NFormItem>
                <NFormItem label="模型名">
                  <NInput
                    v-model:value="store.form.vllm_model"
                    placeholder="Qwen2.5-14B-Instruct"
                  />
                </NFormItem>
                <NAlert type="info" :show-icon="false" class="step-alert">
                  vLLM 启动后默认监听 8000 端口，OpenAI 兼容协议。
                </NAlert>
              </template>

              <NSpace>
                <NButton
                  :loading="store.testingLLM"
                  @click="handleTestLLM"
                >
                  测试连通
                </NButton>
              </NSpace>

              <NAlert
                v-if="store.llmResult"
                :type="store.llmResult.ok ? 'success' : 'error'"
                class="step-alert"
                :title="store.llmResult.ok ? '连通成功' : '连通失败'"
              >
                <div v-if="store.llmResult.ok">
                  后端：{{ store.llmResult.backend }} · 模型：{{ store.llmResult.model }} · 延迟：{{ store.llmResult.latency_ms }}ms
                </div>
                <div v-else>
                  {{ store.llmResult.error }}
                </div>
              </NAlert>
            </NForm>
          </div>

          <!-- 步骤 3：Neo4j 配置 -->
          <div v-else-if="currentStep === 2" class="step-content">
            <h2 class="step-title">Neo4j 配置</h2>
            <p class="step-desc">
              OpsKG 使用 Neo4j 存储知识图谱。填写连接参数后点击"测试连通"。
            </p>

            <NForm label-placement="top">
              <NFormItem label="Bolt URI">
                <NInput
                  v-model:value="store.form.neo4j_uri"
                  placeholder="bolt://localhost:7687"
                />
              </NFormItem>
              <NFormItem label="用户名">
                <NInput
                  v-model:value="store.form.neo4j_user"
                  placeholder="neo4j"
                />
              </NFormItem>
              <NFormItem label="密码">
                <NInput
                  v-model:value="store.form.neo4j_password"
                  type="password"
                  show-password-on="click"
                  placeholder="password"
                />
              </NFormItem>

              <NSpace>
                <NButton
                  :loading="store.testingNeo4j"
                  @click="handleTestNeo4j"
                >
                  测试连通
                </NButton>
              </NSpace>

              <NAlert
                v-if="store.neo4jResult"
                :type="store.neo4jResult.ok ? 'success' : 'error'"
                class="step-alert"
                :title="store.neo4jResult.ok ? '连通成功' : '连通失败'"
              >
                <div v-if="store.neo4jResult.ok">
                  URI：{{ store.neo4jResult.uri }}
                  <span v-if="store.neo4jResult.version">
                    · 版本：{{ store.neo4jResult.version }}
                  </span>
                  · 延迟：{{ store.neo4jResult.latency_ms }}ms
                </div>
                <div v-else>
                  {{ store.neo4jResult.error }}
                </div>
              </NAlert>

              <NAlert type="info" :show-icon="false" class="step-alert">
                如尚未启动 Neo4j，可用 docker：
                <code>docker run -d --name neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j:5-community</code>
              </NAlert>
            </NForm>
          </div>

          <!-- 步骤 4：认证配置 -->
          <div v-else-if="currentStep === 3" class="step-content">
            <h2 class="step-title">认证配置</h2>
            <p class="step-desc">
              可选启用 API Token 全局访问控制；同时配置 Bootstrap Admin（首次启动自动创建的管理员账号）。
            </p>

            <NForm label-placement="top">
              <NFormItem label="启用 API Token 认证">
                <NSwitch v-model:value="store.form.enable_auth" />
                <NText depth="3" style="margin-left: 12px;">
                  启用后所有 API 请求需携带 <code>Authorization: Bearer &lt;token&gt;</code>
                </NText>
              </NFormItem>

              <NFormItem v-if="store.form.enable_auth" label="API Token">
                <NInput
                  v-model:value="store.form.api_token"
                  type="password"
                  show-password-on="click"
                  placeholder="生成一个足够长的随机字符串"
                />
              </NFormItem>

              <NDivider>Bootstrap Admin</NDivider>

              <NFormItem label="管理员用户名">
                <NInput
                  v-model:value="store.form.bootstrap_admin_user"
                  placeholder="admin"
                />
              </NFormItem>
              <NFormItem label="管理员密码">
                <NInput
                  v-model:value="store.form.bootstrap_admin_password"
                  type="password"
                  show-password-on="click"
                  placeholder="admin"
                />
              </NFormItem>

              <NAlert type="info" :show-icon="false" class="step-alert">
                Bootstrap Admin 仅在用户表为空时创建；首次启动后修改密码请通过"用户管理"页面。
              </NAlert>
            </NForm>
          </div>

          <!-- 步骤 5：生成命令 -->
          <div v-else-if="currentStep === 4" class="step-content">
            <h2 class="step-title">生成部署命令</h2>
            <p class="step-desc">
              选择部署模式与端口、worker 数，生成可复制的 docker 命令与 .env 文件内容。
            </p>

            <NForm label-placement="top" inline>
              <NFormItem label="部署模式">
                <NSelect
                  v-model:value="store.form.mode"
                  :options="[
                    { label: 'docker compose（推荐，含 Neo4j）', value: 'docker-compose' },
                    { label: 'docker run（单容器，需自行启动 Neo4j）', value: 'docker-run' },
                  ]"
                  style="width: 320px;"
                />
              </NFormItem>
              <NFormItem label="宿主端口">
                <NInputNumber
                  v-model:value="store.form.port"
                  :min="1"
                  :max="65535"
                  style="width: 120px;"
                />
              </NFormItem>
              <NFormItem label="Workers">
                <NInputNumber
                  v-model:value="store.form.workers"
                  :min="1"
                  :max="32"
                  style="width: 120px;"
                />
              </NFormItem>
              <NFormItem>
                <NButton
                  :loading="store.generating"
                  type="primary"
                  @click="handleGenerate"
                >
                  重新生成
                </NButton>
              </NFormItem>
            </NForm>

            <NDivider />

            <div v-if="store.commandResult" class="result-block">
              <div class="result-header">
                <NText strong>.env 文件</NText>
                <NButton
                  size="small"
                  quaternary
                  @click="copyToClipboard(store.commandResult?.env_file_content || '', '.env 内容')"
                >
                  复制
                </NButton>
              </div>
              <NCode
                :code="store.commandResult.env_file_content"
                language="ini"
                class="result-code"
              />

              <div class="result-header" style="margin-top: 16px;">
                <NText strong>部署命令</NText>
                <NButton
                  size="small"
                  quaternary
                  @click="copyToClipboard(store.commandResult?.command || '', '命令')"
                >
                  复制
                </NButton>
              </div>
              <NCode
                :code="store.commandResult.command"
                language="bash"
                class="result-code"
              />

              <NAlert type="success" class="step-alert" title="下一步">
                <ol class="step-list">
                  <li>将 .env 内容保存到项目根目录的 <code>.env</code> 文件</li>
                  <li>在项目根目录执行复制的命令</li>
                  <li>等待服务健康（<code>docker compose logs -f opskg</code> 看到 "Application startup complete"）</li>
                  <li>访问首页 <code>http://localhost:{{ store.form.port }}</code>，使用 Bootstrap Admin 凭据登录</li>
                </ol>
              </NAlert>
            </div>
          </div>
        </NSpin>

        <!-- 底部导航 -->
        <div class="setup-footer">
          <NSpace justify="space-between" align="center">
            <NButton quaternary @click="handleSkip">
              跳过向导
            </NButton>
            <NSpace>
              <NButton
                v-if="currentStep > 0"
                secondary
                @click="handlePrev"
              >
                上一步
              </NButton>
              <NButton
                v-if="currentStep < totalSteps - 1"
                type="primary"
                @click="handleNext"
              >
                下一步
              </NButton>
              <NButton
                v-else
                type="primary"
                @click="handleFinish"
              >
                完成
              </NButton>
            </NSpace>
          </NSpace>
        </div>
      </NCard>

      <!-- 已就绪提示（完成态） -->
      <div v-if="store.ready" class="ready-banner">
        <NText type="success">✓ 环境已就绪，可直接进入控制台</NText>
        <NButton size="small" type="primary" ghost @click="router.replace('/dashboard')">
          进入控制台
        </NButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.setup-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 32px 20px;
  display: flex;
  justify-content: center;
}

.setup-box {
  width: 100%;
  max-width: 880px;
}

.setup-header {
  text-align: center;
  margin-bottom: 24px;
  color: #fff;
}

.logo-icon {
  font-size: 48px;
  margin-bottom: 8px;
}

.setup-title {
  font-size: 28px;
  font-weight: 700;
  margin: 0;
  color: #fff;
}

.setup-subtitle {
  font-size: 14px;
  margin: 8px 0 0;
  opacity: 0.85;
}

.setup-steps {
  background: var(--n-color, #fff);
  border-radius: 12px 12px 0 0;
  padding: 20px 24px 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}

.setup-card {
  border-radius: 0 0 12px 12px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
  min-height: 460px;
  display: flex;
  flex-direction: column;
}

.setup-card :deep(.n-card__content) {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.step-content {
  flex: 1;
  padding: 8px 4px;
}

.step-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 8px;
  color: var(--n-text-color, #111827);
}

.step-desc {
  font-size: 13px;
  color: var(--n-text-color-3, #6b7280);
  margin: 0 0 16px;
}

.step-alert {
  margin-top: 12px;
}

.step-list {
  margin: 8px 0 0;
  padding-left: 20px;
  line-height: 1.8;
}

.status-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  background: var(--n-color-hover, #f9fafb);
  border-radius: 8px;
  border: 1px solid var(--n-divider-color, #e5e7eb);
}

.status-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-hint {
  font-size: 12px;
}

.result-block {
  margin-top: 8px;
}

.result-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.result-code {
  border-radius: 8px;
  font-size: 12px;
  max-height: 320px;
  overflow: auto;
}

.setup-footer {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid var(--n-divider-color, #e5e7eb);
}

.ready-banner {
  margin-top: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}

code {
  background: var(--n-color-hover, #f3f4f6);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
}
</style>
