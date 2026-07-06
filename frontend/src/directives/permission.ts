/**
 * v-permission 自定义指令（S15-4 页面级权限粒度）
 *
 * 用法：
 *   <!-- 单角色 -->
 *   <NButton v-permission="'admin'">删除用户</NButton>
 *
 *   <!-- 多角色（满足任意一个即可） -->
 *   <NButton v-permission="['admin', 'operator']">回滚版本</NButton>
 *
 * 行为：
 * - 用户角色不匹配时，元素从 DOM 中移除（不是隐藏）
 * - dev 模式（authRequired === false）不拦截，与路由守卫一致
 * - 后端不可达（authRequired === null）时也不拦截（避免误伤）
 *
 * 安全说明：前端权限仅为 UX 优化，后端 verify_token 是安全边界
 */
import type { Directive, DirectiveBinding } from 'vue'
import { useAuthStore } from '@/stores/auth'
import type { Role } from '@/composables/usePermission'

function checkPermission(binding: DirectiveBinding<Role | Role[]>): boolean {
  const authStore = useAuthStore()

  // dev 模式 / 后端不可达 → 不拦截
  if (authStore.authRequired === false || authStore.authRequired === null) {
    return true
  }

  const userRole = authStore.user?.role
  if (!userRole) {
    return false
  }

  const required = Array.isArray(binding.value) ? binding.value : [binding.value]
  return required.includes(userRole as Role)
}

export const permission: Directive<HTMLElement, Role | Role[]> = {
  mounted(el, binding) {
    if (!checkPermission(binding)) {
      el.parentNode?.removeChild(el)
    }
  },
}
