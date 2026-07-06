import { createRouter, createWebHistory } from 'vue-router'
import AppLayout from '@/components/layout/AppLayout.vue'

const routes = [
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
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  document.title = `${to.meta.title || 'OpsKG'} · LLM Wiki Console`
})

export default router
