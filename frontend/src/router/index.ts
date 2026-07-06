import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw, RouteLocationNormalized } from 'vue-router'
import AppLayout from '@/components/layout/AppLayout.vue'
import { useAuthStore } from '@/stores/auth'

/**
 * 路由 meta 类型扩展（S14-1 路由级权限守卫）
 *
 * 字段说明：
 * - title:    页面标题（用于 document.title 与侧栏显示）
 * - icon:     侧栏图标 key
 * - public:   公开路由（无需登录即可访问，如登录页/回调页）
 * - requireRole: 访问该路由所需角色列表（任一匹配即放行）；
 *                未设置表示所有已登录用户均可访问。
 *                角色层级：admin > operator > viewer
 */
declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    icon?: string
    public?: boolean
    requireRole?: Array<'admin' | 'operator' | 'viewer'>
  }
}

const routes: RouteRecordRaw[] = [
  // 登录页（不在 AppLayout 内，无侧栏）
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', public: true },
  },
  // OIDC 回调页（不在 AppLayout 内）
  {
    path: '/login/callback',
    name: 'login-callback',
    component: () => import('@/views/LoginCallbackView.vue'),
    meta: { title: '登录回调', public: true },
  },
  // 403 禁止访问页（不在 AppLayout 内，避免侧栏干扰）
  {
    path: '/forbidden',
    name: 'forbidden',
    component: () => import('@/views/ForbiddenView.vue'),
    meta: { title: '禁止访问', public: true },
  },
  {
    path: '/',
    component: AppLayout,
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '仪表盘', icon: 'dashboard' },
      },
      {
        path: 'documents',
        name: 'documents',
        component: () => import('@/views/DocumentsView.vue'),
        meta: { title: '文档管理', icon: 'documents' },
      },
      {
        path: 'search',
        name: 'search',
        component: () => import('@/views/SearchView.vue'),
        meta: { title: '知识搜索', icon: 'search' },
      },
      {
        path: 'review',
        name: 'review',
        component: () => import('@/views/ReviewView.vue'),
        meta: { title: '审查队列', icon: 'review' },
      },
      {
        path: 'wiki',
        name: 'wiki',
        component: () => import('@/views/WikiView.vue'),
        meta: { title: 'Wiki 浏览', icon: 'wiki' },
      },
      {
        path: 'wiki-query',
        name: 'wiki-query',
        component: () => import('@/views/WikiQueryView.vue'),
        meta: { title: 'Wiki Q&A', icon: 'query' },
      },
      {
        path: 'wiki-health',
        name: 'wiki-health',
        component: () => import('@/views/WikiHealthView.vue'),
        meta: { title: '健康检查', icon: 'health' },
      },
      {
        path: 'runbook',
        name: 'runbook',
        component: () => import('@/views/RunbookView.vue'),
        meta: { title: 'Runbook 工作台', icon: 'runbook' },
      },
      {
        path: 'incidents',
        name: 'incidents',
        component: () => import('@/views/IncidentsView.vue'),
        meta: { title: 'Incident 管理', icon: 'incidents' },
      },
      {
        path: 'changes',
        name: 'changes',
        component: () => import('@/views/ChangesView.vue'),
        meta: { title: '变更关联', icon: 'changes' },
      },
      {
        path: 'topology',
        name: 'topology',
        component: () => import('@/views/TopologyView.vue'),
        meta: { title: '服务拓扑', icon: 'topology' },
      },
      {
        path: 'templates',
        name: 'templates',
        component: () => import('@/views/TemplatesView.vue'),
        meta: { title: '模板管理', icon: 'templates' },
      },
      {
        path: 'versions',
        name: 'versions',
        component: () => import('@/views/VersionsView.vue'),
        meta: { title: '版本控制', icon: 'versions' },
      },
      {
        path: 'graph',
        name: 'graph',
        component: () => import('@/views/GraphView.vue'),
        meta: { title: '知识图谱', icon: 'graph' },
      },
      {
        path: 'export',
        name: 'export',
        component: () => import('@/views/ExportView.vue'),
        meta: { title: '导出中心', icon: 'export' },
      },
      {
        path: 'mcp',
        name: 'mcp',
        component: () => import('@/views/McpView.vue'),
        meta: { title: 'MCP 浏览器', icon: 'mcp' },
      },
      {
        path: 'users',
        name: 'users',
        component: () => import('@/views/UsersView.vue'),
        // 用户管理仅 admin 可访问（S14-1：路由级拦截，避免进页面后才 forbidden）
        meta: { title: '用户管理', icon: 'users', requireRole: ['admin'] },
      },
      // 404 兜底（catch-all，必须放在最后）
      {
        path: ':pathMatch(.*)*',
        name: 'not-found',
        component: () => import('@/views/NotFoundView.vue'),
        meta: { title: '页面不存在' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 已初始化标记：避免每次路由跳转都重新拉 /auth/me
let authInitialized = false

/**
 * 检查当前用户角色是否满足路由所需角色
 *
 * 角色层级（admin > operator > viewer）：
 * - 路由 requireRole: ['admin']       → 仅 admin
 * - 路由 requireRole: ['admin','operator'] → admin 或 operator
 * - 路由无 requireRole                → 所有已登录用户
 *
 * 特殊处理：
 * - dev 模式（authRequired === false）：放行（后端无认证，前端不应拦截）
 * - 用户角色未知（user 为 null）：视为无权限，交由调用方决定跳转
 */
export function hasRequiredRole(
  userRole: string | undefined,
  requireRole: Array<'admin' | 'operator' | 'viewer'> | undefined,
): boolean {
  if (!requireRole || requireRole.length === 0) {
    return true
  }
  if (!userRole) {
    return false
  }
  return requireRole.includes(userRole as 'admin' | 'operator' | 'viewer')
}

/**
 * 路由守卫主逻辑（导出以便单元测试）
 *
 * 返回值语义（同 vue-router beforeEach）：
 * - true / undefined：放行
 * - false：取消导航
 * - 路由位置对象：重定向到该路由
 */
export async function navigationGuard(to: RouteLocationNormalized): Promise<
  boolean | { name: string; query?: Record<string, string>; state?: Record<string, unknown> }
> {
  document.title = `${to.meta.title || 'OpsKG'} · LLM Wiki Console`

  // 公开路由（登录页、回调页、403 页）直接放行
  if (to.meta.public) {
    return true
  }

  // 首次访问受保护路由时检查认证状态
  if (!authInitialized) {
    authInitialized = true
    const authStore = useAuthStore()
    // fetchMe 会设置 authRequired：
    // - true: 后端要求认证（session/401）
    // - false: dev/legacy 模式放行
    // - null: 后端不可达
    await authStore.fetchMe()

    // 后端要求认证但用户未登录 → 跳登录
    if (authStore.authRequired === true && !authStore.isAuthenticated) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    // 其他情况（dev 模式 / 已登录 / 后端不可达）放行
  }

  // S14-1: 路由级权限守卫
  // 仅在后端确认要求认证（authRequired === true）时检查角色：
  // - true:  后端可达且要求认证 → 检查角色
  // - false: dev 模式放行 → 不检查
  // - null:  后端不可达 → 不检查（避免网络抖动锁死，无法验证角色）
  const authStore = useAuthStore()
  if (to.meta.requireRole && authStore.authRequired === true) {
    const userRole = authStore.user?.role
    if (!hasRequiredRole(userRole, to.meta.requireRole)) {
      // 通过 router state 传递所需角色给 ForbiddenView 显示
      return {
        name: 'forbidden',
        state: { requiredRoles: to.meta.requireRole },
      }
    }
  }

  return true
}

// 测试辅助：重置 authInitialized 标记（仅用于单元测试隔离）
export function _resetAuthInitializedForTest(): void {
  authInitialized = false
}

router.beforeEach(navigationGuard)

export default router
