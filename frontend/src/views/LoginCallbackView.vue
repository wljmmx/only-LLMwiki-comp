<script setup lang="ts">
/**
 * OIDC 登录回调页（P3-1 SSO）
 *
 * 后端完成 OIDC 流程后重定向到 /login/callback?token=...&redirect=...
 * 或 /login/callback?error=...&error_description=...
 *
 * 本页：
 * 1. 从 URL query 提取 token
 * 2. 调用 authStore.handleOIDCCallback(token) 保存 token + 加载用户
 * 3. 跳转到 redirect 目标（默认 /dashboard）
 * 4. 错误场景：显示错误信息并提供重试按钮
 */
import { onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { NSpin, NCard, NButton, NAlert } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const error = ref<string>('')
const errorDescription = ref<string>('')
const processing = ref(true)

onMounted(async () => {
  const token = route.query.token as string | undefined
  const redirect = (route.query.redirect as string) || '/dashboard'
  const errorCode = route.query.error as string | undefined
  const errorDesc = route.query.error_description as string | undefined

  if (errorCode) {
    error.value = errorCode
    errorDescription.value = errorDesc || 'OIDC 认证失败'
    processing.value = false
    return
  }

  if (!token) {
    error.value = 'invalid_callback'
    errorDescription.value = '回调 URL 缺少 token 参数'
    processing.value = false
    return
  }

  try {
    await authStore.handleOIDCCallback(token)
    if (authStore.isAuthenticated) {
      router.replace(redirect)
    } else {
      // token 已保存但用户信息加载失败（可能是 dev 模式）
      // 仍然跳转，让后续路由守卫处理
      router.replace(redirect)
    }
  } catch (err: any) {
    error.value = 'callback_failed'
    errorDescription.value = err.message || '处理登录回调失败'
    processing.value = false
  }
})

function backToLogin() {
  router.replace('/login')
}
</script>

<template>
  <div class="callback-container">
    <NCard class="callback-card" :bordered="false">
      <div v-if="processing" class="callback-processing">
        <NSpin size="large" />
        <p class="callback-text">正在完成登录...</p>
      </div>
      <div v-else class="callback-error">
        <NAlert type="error" :title="error" :description="errorDescription" />
        <NButton type="primary" block class="callback-action" @click="backToLogin">
          返回登录
        </NButton>
      </div>
    </NCard>
  </div>
</template>

<style scoped>
.callback-container {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.callback-card {
  width: 100%;
  max-width: 420px;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.16);
}

.callback-processing {
  text-align: center;
  padding: 40px 20px;
}

.callback-text {
  margin-top: 20px;
  font-size: 14px;
  color: var(--n-text-color-2, #374151);
}

.callback-error {
  padding: 20px;
}

.callback-action {
  margin-top: 16px;
}
</style>
