import { createRouter, createWebHistory } from 'vue-router'
import AppLayout from '@/components/layout/AppLayout.vue'
import { useAuthStore } from '@/stores/auth'

const routes = [
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
        meta: { title: '用户管理', icon: 'users' },
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

router.beforeEach(async (to) => {
  document.title = `${to.meta.title || 'OpsKG'} · LLM Wiki Console`

  // 公开路由（登录页、回调页）直接放行
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

  return true
})

export default router
