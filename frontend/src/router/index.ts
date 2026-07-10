import { createRouter, createWebHistory } from 'vue-router'
import type {
  RouteRecordRaw,
  RouteLocationNormalized,
  NavigationGuardReturn,
} from 'vue-router'
import AppLayout from '@/components/layout/AppLayout.vue'
import { useAuthStore } from '@/stores/auth'
import { useSetupStore } from '@/stores/setup'
import { getSetupStatus } from '@/api/setup'
import { startLoadingBar, finishLoadingBar } from '@/api/loadingBar'
import { hasRequiredRole } from '@/utils/role'

// 重新导出，保持 router/index.spec.ts 现有 import 路径不变
export { hasRequiredRole }

/**
 * 路由 meta 类型扩展（S14-1 路由级权限守卫 + P1-5 菜单单一事实源）
 *
 * 字段说明：
 * - title:    页面标题（用于 document.title 与侧栏显示）
 * - icon:     侧栏图标 key（语义名，见 @/utils/icons）
 * - public:   公开路由（无需登录即可访问，如登录页/回调页）
 * - requireRole: 访问该路由所需角色列表（任一匹配即放行）；
 *                未设置表示所有已登录用户均可访问。
 *                角色层级：admin > operator > viewer
 * - menuGroup: 侧栏分组标签（P1-5）。缺省 menuGroup 但定义 menuOrder → 顶层独立项；
 *              两者均缺省 → 不进侧栏。
 * - menuOrder: 侧栏排序（组内）。缺省 → 不进侧栏。
 */
declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    icon?: string
    public?: boolean
    requireRole?: Array<'admin' | 'operator' | 'viewer'>
    menuGroup?: string
    menuOrder?: number
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
  // 开箱配置向导（不在 AppLayout 内，无需认证）
  {
    path: '/setup',
    name: 'setup',
    component: () => import('@/views/SetupWizardView.vue'),
    meta: { title: '开箱配置向导', public: true },
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
        meta: { title: '仪表盘', icon: 'dashboard', menuOrder: 0 },
      },
      {
        path: 'documents',
        name: 'documents',
        component: () => import('@/views/DocumentsView.vue'),
        meta: { title: '文档管理', icon: 'documents', menuGroup: '知识管理', menuOrder: 0 },
      },
      {
        path: 'search',
        name: 'search',
        component: () => import('@/views/SearchView.vue'),
        meta: { title: '知识搜索', icon: 'search', menuGroup: '知识管理', menuOrder: 1 },
      },
      {
        path: 'wiki',
        name: 'wiki',
        component: () => import('@/views/WikiView.vue'),
        meta: { title: 'Wiki 浏览', icon: 'wiki', menuGroup: '知识管理', menuOrder: 2 },
      },
      {
        path: 'wiki-query',
        name: 'wiki-query',
        component: () => import('@/views/WikiQueryView.vue'),
        meta: { title: 'Wiki Q&A', icon: 'query', menuGroup: '知识管理', menuOrder: 3 },
      },
      {
        path: 'graph',
        name: 'graph',
        component: () => import('@/views/GraphView.vue'),
        meta: { title: '知识图谱', icon: 'graph', menuGroup: '知识管理', menuOrder: 4 },
      },
      {
        path: 'wiki-health',
        name: 'wiki-health',
        component: () => import('@/views/WikiHealthView.vue'),
        meta: { title: '健康检查', icon: 'health', menuGroup: '质量治理', menuOrder: 0 },
      },
      {
        path: 'review',
        name: 'review',
        component: () => import('@/views/ReviewView.vue'),
        meta: { title: '审查队列', icon: 'review', menuGroup: '质量治理', menuOrder: 1 },
      },
      {
        path: 'versions',
        name: 'versions',
        component: () => import('@/views/VersionsView.vue'),
        meta: { title: '版本控制', icon: 'versions', menuGroup: '质量治理', menuOrder: 2 },
      },
      {
        path: 'incidents',
        name: 'incidents',
        component: () => import('@/views/IncidentsView.vue'),
        meta: { title: 'Incident 管理', icon: 'incidents', menuGroup: 'AIOps', menuOrder: 0 },
      },
      {
        path: 'changes',
        name: 'changes',
        component: () => import('@/views/ChangesView.vue'),
        meta: { title: '变更关联', icon: 'changes', menuGroup: 'AIOps', menuOrder: 1 },
      },
      {
        path: 'topology',
        name: 'topology',
        component: () => import('@/views/TopologyView.vue'),
        meta: { title: '服务拓扑', icon: 'topology', menuGroup: 'AIOps', menuOrder: 2 },
      },
      {
        path: 'runbook',
        name: 'runbook',
        component: () => import('@/views/RunbookView.vue'),
        meta: { title: 'Runbook 工作台', icon: 'runbook', menuGroup: 'AIOps', menuOrder: 3 },
      },
      {
        path: 'templates',
        name: 'templates',
        component: () => import('@/views/TemplatesView.vue'),
        meta: { title: '模板管理', icon: 'templates', menuGroup: '系统工具', menuOrder: 0 },
      },
      {
        path: 'export',
        name: 'export',
        component: () => import('@/views/ExportView.vue'),
        meta: { title: '导出中心', icon: 'export', menuGroup: '系统工具', menuOrder: 1 },
      },
      {
        path: 'mcp',
        name: 'mcp',
        component: () => import('@/views/McpView.vue'),
        meta: { title: 'MCP 浏览器', icon: 'mcp', menuGroup: '系统工具', menuOrder: 2 },
      },
      {
        path: 'users',
        name: 'users',
        component: () => import('@/views/UsersView.vue'),
        // 用户管理仅 admin 可访问（S14-1：路由级拦截，避免进页面后才 forbidden）
        meta: {
          title: '用户管理',
          icon: 'users',
          requireRole: ['admin'],
          menuGroup: '系统工具',
          menuOrder: 3,
        },
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

// Setup status 检查标记：每个会话只检查一次（避免每次导航都打后端）
let setupChecked = false
// 是否检测到环境未就绪且未被用户主动跳过
let setupNeeded = false

/**
 * 路由守卫主逻辑（导出以便单元测试）
 *
 * 返回值语义（同 vue-router beforeEach）：
 * - true / undefined：放行
 * - false：取消导航
 * - 路由位置对象：重定向到该路由
 */
export async function navigationGuard(to: RouteLocationNormalized): Promise<NavigationGuardReturn> {
  document.title = `${to.meta.title || 'OpsKG'} · LLM Wiki Console`

  // 公开路由（登录页、setup 向导、回调页、403 页）直接放行
  if (to.meta.public) {
    return true
  }

  // 首次访问受保护路由时检查 setup 完成度
  // 仅当后端未就绪且用户未主动 dismiss 时跳转到 setup 向导
  if (!setupChecked) {
    setupChecked = true
    try {
      const setupStore = useSetupStore()
      const status = await getSetupStatus()
      setupStore.status = status
      // 未就绪且用户未主动跳过 → 引导到 setup
      if (!status.ready && !setupStore.dismissed) {
        setupNeeded = true
        return { name: 'setup' }
      }
    } catch {
      // 后端不可达时不阻断主流程（让后续认证守卫处理）
    }
  } else if (setupNeeded && to.name !== 'setup') {
    // 已经判定需要 setup 但用户试图访问非 public 路由 → 仍引导到 setup
    return { name: 'setup' }
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
      // state 必须为 HistoryState（值为 string | number | boolean | null | HistoryState）
      // requireRole 为 string[]，序列化为逗号分隔字符串
      return {
        name: 'forbidden',
        state: { requiredRoles: to.meta.requireRole.join(',') },
      }
    }
  }

  return true
}

// 测试辅助：重置 authInitialized 标记（仅用于单元测试隔离）
export function _resetAuthInitializedForTest(): void {
  authInitialized = false
}

// 测试辅助：重置 setup 检查标记（仅用于单元测试隔离）
export function _resetSetupCheckedForTest(): void {
  setupChecked = false
  setupNeeded = false
}

router.beforeEach(navigationGuard)

// P1-3: 路由级 loading bar（chunk 下载反馈）
// beforeEach 启动、afterEach 完成，与 axios 共用计数器，并发安全
router.beforeEach((_to, _from, next) => {
  startLoadingBar()
  next()
})
router.afterEach(() => {
  finishLoadingBar()
})
// 导航出错也要结束 loading bar，避免卡住
router.onError(() => {
  finishLoadingBar()
})

export default router
