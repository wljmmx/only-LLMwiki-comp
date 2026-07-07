<script setup lang="ts">
/**
 * 403 禁止访问页（S14-1 路由级权限守卫）
 *
 * 触发场景：
 * - 路由 meta.requireRole 与当前用户 role 不匹配时由 router 守卫重定向到此
 * - 例：viewer/operator 访问 /users（requireRole: ['admin']）
 *
 * 设计：
 * - 显示当前角色与所需角色，便于用户理解为何被拦截
 * - 提供「返回首页」「切换账号」操作
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NButton, NSpace, NAlert, NTag } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const currentRole = computed(() => authStore.user?.role || '未登录')
const displayName = computed(() => authStore.displayName)

// 从 router state 读取所需角色（由守卫通过 router.replace({ name: 'forbidden', state: {...} }) 传入）
// 兼容降级：state 不可用时显示通用提示
// 注：state 值必须为 HistoryStateValue（string | number | boolean | null），
// 守卫将 string[] 序列化为逗号分隔字符串，此处解析还原
const requiredRoles = computed<string[]>(() => {
  const state = window.history.state as { requiredRoles?: unknown } | null
  const raw = state?.requiredRoles
  if (typeof raw === 'string' && raw.length > 0) {
    return raw.split(',')
  }
  if (Array.isArray(raw)) {
    return raw as string[]
  }
  return []
})

const requiredRolesText = computed(() =>
  requiredRoles.value.length > 0 ? requiredRoles.value.join(' / ') : 'admin',
)

function goHome() {
  router.replace('/dashboard')
}

function switchAccount() {
  authStore.logout().finally(() => {
    router.replace({ name: 'login' })
  })
}
</script>

<template>
  <div class="forbidden-page">
    <NCard :bordered="true" class="forbidden-card">
      <template #header>
        <NSpace align="center" :size="8">
          <span class="forbidden-icon">🚫</span>
          <span>禁止访问（403）</span>
        </NSpace>
      </template>

      <NSpace vertical :size="16">
        <NAlert type="warning" :show-icon="true">
          <template #header>权限不足</template>
          当前账号
          <strong>{{ displayName }}</strong>
          （角色：
          <NTag size="small" :type="currentRole === 'admin' ? 'success' : 'default'">
            {{ currentRole }}
          </NTag>
          ）无权访问该页面。该页面需要角色：
          <NTag size="small" type="error">{{ requiredRolesText }}</NTag>
        </NAlert>

        <div class="hint">
          如需提升权限，请联系系统管理员。若怀疑角色配置错误，可尝试切换账号后重新访问。
        </div>

        <NSpace :size="12">
          <NButton type="primary" @click="goHome">返回首页</NButton>
          <NButton quaternary @click="switchAccount">切换账号</NButton>
        </NSpace>
      </NSpace>
    </NCard>
  </div>
</template>

<style scoped>
.forbidden-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 400px;
  padding: 24px;
}

.forbidden-card {
  max-width: 560px;
  width: 100%;
}

.forbidden-icon {
  font-size: 18px;
}

.hint {
  font-size: 13px;
  color: var(--n-text-color-2, #6b7280);
  line-height: 1.6;
}
</style>
