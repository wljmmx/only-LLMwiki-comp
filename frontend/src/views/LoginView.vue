<script setup lang="ts">
/**
 * 登录页（P3-1 SSO）
 *
 * 支持：
 * 1. 用户名 + 密码登录（POST /auth/login）
 * 2. OIDC 提供者按钮（GET /auth/oidc/{provider}）
 * 3. dev 模式提示（后端未配置认证时所有请求放行）
 */
import { ref, onMounted, reactive } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { NCard, NForm, NFormItem, NInput, NButton, NAlert, NSpace, NDivider } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useMessage } from 'naive-ui'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const message = useMessage()

const form = reactive({
  username: '',
  password: '',
})
const formRef = ref()
const submitting = ref(false)
const errorMessage = ref('')
const redirect = ref<string>((route.query.redirect as string) || '/dashboard')

onMounted(async () => {
  // 加载 OIDC 提供者
  await authStore.loadOIDCProviders()
  // 已登录 → 跳转
  if (authStore.token) {
    const user = await authStore.fetchMe()
    if (user) {
      router.replace(redirect.value)
    }
  }
})

async function handleLogin() {
  if (!form.username || !form.password) {
    errorMessage.value = '请输入用户名和密码'
    return
  }
  errorMessage.value = ''
  submitting.value = true
  try {
    const user = await authStore.loginWithPassword(form.username, form.password)
    message.success(`欢迎，${user.display_name || user.username}`)
    router.replace(redirect.value)
  } catch (err: any) {
    const detail = err.response?.data?.detail || err.message
    errorMessage.value = typeof detail === 'string' ? detail : '登录失败'
  } finally {
    submitting.value = false
  }
}

function handleOIDC(providerName: string) {
  authStore.redirectToOIDC(providerName, redirect.value)
}
</script>

<template>
  <div class="login-container">
    <div class="login-box">
      <div class="login-header">
        <div class="logo-icon">📚</div>
        <h1 class="login-title">OpsKG</h1>
        <p class="login-subtitle">AI 驱动的运维知识管理系统</p>
      </div>

      <NCard class="login-card" :bordered="false">
        <NForm ref="formRef" @submit.prevent="handleLogin">
          <NFormItem label="用户名">
            <NInput
              v-model:value="form.username"
              placeholder="admin"
              :input-props="{ autocomplete: 'username' }"
              @keyup.enter="handleLogin"
            />
          </NFormItem>
          <NFormItem label="密码">
            <NInput
              v-model:value="form.password"
              type="password"
              show-password-on="click"
              placeholder="••••••"
              :input-props="{ autocomplete: 'current-password' }"
              @keyup.enter="handleLogin"
            />
          </NFormItem>

          <NAlert
            v-if="errorMessage"
            type="error"
            :title="errorMessage"
            class="login-error"
            closable
            @close="errorMessage = ''"
          />

          <NButton
            type="primary"
            block
            :loading="submitting"
            @click="handleLogin"
          >
            登录
          </NButton>
        </NForm>

        <NDivider v-if="authStore.oidcEnabled" class="login-divider">
          或使用 SSO 登录
        </NDivider>

        <NSpace v-if="authStore.oidcEnabled" vertical :size="12">
          <NButton
            v-for="provider in authStore.oidcProviders"
            :key="provider.name"
            block
            secondary
            @click="handleOIDC(provider.name)"
          >
            {{ provider.display_name }}
          </NButton>
        </NSpace>

        <div class="login-hint">
          <p>默认管理员：admin / admin</p>
          <p>可在后端通过 <code>OPSKG_BOOTSTRAP_ADMIN_USER</code> / <code>OPSKG_BOOTSTRAP_ADMIN_PASSWORD</code> 覆盖</p>
        </div>
      </NCard>
    </div>
  </div>
</template>

<style scoped>
.login-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.login-box {
  width: 100%;
  max-width: 420px;
}

.login-header {
  text-align: center;
  margin-bottom: 24px;
  color: #fff;
}

.logo-icon {
  font-size: 48px;
  margin-bottom: 8px;
}

.login-title {
  font-size: 32px;
  font-weight: 700;
  margin: 0;
  color: #fff;
}

.login-subtitle {
  font-size: 14px;
  margin: 8px 0 0;
  opacity: 0.85;
}

.login-card {
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.16);
}

.login-error {
  margin-bottom: 12px;
}

.login-divider {
  margin: 20px 0;
}

.login-hint {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--n-divider-color, #e5e7eb);
  font-size: 12px;
  color: var(--n-text-color-3, #6b7280);
  text-align: center;
}

.login-hint p {
  margin: 4px 0;
}

.login-hint code {
  background: var(--n-color-hover, #f3f4f6);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
}
</style>
