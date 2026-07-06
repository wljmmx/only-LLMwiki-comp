import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useStorage } from '@vueuse/core'

export interface TourStep {
  id: string
  title: string
  description: string
  /** 目标页面路由名，跨页面步骤需先导航 */
  route?: string
  /** 目标元素 CSS 选择器（可选，不设则居中显示） */
  selector?: string
  /** 提示位置 */
  placement?: 'top' | 'bottom' | 'left' | 'right' | 'center'
}

export const TOUR_STEPS: TourStep[] = [
  {
    id: 'welcome',
    title: '欢迎使用 OpsKG',
    description:
      'OpsKG 是 AI 驱动的运维知识管理系统，核心范式：文档 → 知识编译 → Wiki 页面 → 智能问答。\n\n接下来用 5 步带你快速了解核心功能。',
    placement: 'center',
  },
  {
    id: 'sidebar',
    title: '侧边栏导航',
    description:
      '左侧菜单分为 5 大组：\n\n📊 仪表盘 — 系统概览\n📄 知识管理 — 文档/搜索/Wiki/图谱\n✅ 质量治理 — 健康检查/审查/版本\n🔧 AIOps — Incident/变更/拓扑/Runbook\n🛠 系统工具 — 模板/导出/MCP',
    selector: '.logo',
    placement: 'right',
  },
  {
    id: 'documents',
    title: '上传文档（第一步）',
    description:
      '进入"文档管理"页面，拖拽上传运维文档（PDF/Word/Markdown/TXT）。\n\n上传后系统自动解析并提取实体，这是知识编译的起点。',
    route: 'documents',
    selector: '.content',
    placement: 'left',
  },
  {
    id: 'wiki',
    title: 'Wiki 浏览（核心范式）',
    description:
      '进入"Wiki 浏览"页面，查看 LLM 编译生成的结构化 Wiki 页面。\n\nWiki 页面支持 [[wikilink]] 双向链接，是 OpsKG 的核心差异化能力。',
    route: 'wiki',
    selector: '.content',
    placement: 'left',
  },
  {
    id: 'search',
    title: '知识搜索与 Q&A',
    description:
      '用"知识搜索"做关键词检索，或用"Wiki Q&A"向 LLM 提问，获得基于已编译 Wiki 的回答。\n\n你也可以探索 AIOps 模块（Incident 关联、服务拓扑）和系统工具（导出、MCP）。\n\n引导到此结束，开始你的知识管理之旅吧！',
    route: 'search',
    selector: '.content',
    placement: 'left',
  },
]

export const useOnboardingStore = defineStore('onboarding', () => {
  const tourCompleted = useStorage('opskg:onboarding:completed', false)
  const tourSkipped = useStorage('opskg:onboarding:skipped', false)
  const activeStepIndex = ref(-1)
  const isActive = computed(() => activeStepIndex.value >= 0)

  const currentStep = computed<TourStep | null>(
    () => (isActive.value ? TOUR_STEPS[activeStepIndex.value] : null),
  )
  const totalSteps = TOUR_STEPS.length
  const isLastStep = computed(
    () => activeStepIndex.value === totalSteps - 1,
  )
  const progress = computed(() =>
    isActive.value ? (activeStepIndex.value + 1) / totalSteps : 0,
  )

  function startTour() {
    activeStepIndex.value = 0
    tourSkipped.value = false
  }

  function nextStep() {
    if (activeStepIndex.value < totalSteps - 1) {
      activeStepIndex.value++
    } else {
      completeTour()
    }
  }

  function prevStep() {
    if (activeStepIndex.value > 0) {
      activeStepIndex.value--
    }
  }

  function completeTour() {
    activeStepIndex.value = -1
    tourCompleted.value = true
  }

  function skipTour() {
    activeStepIndex.value = -1
    tourSkipped.value = true
  }

  function resetTour() {
    tourCompleted.value = false
    tourSkipped.value = false
    activeStepIndex.value = -1
  }

  /** 首次访问时自动启动（在 AppLayout onMounted 调用） */
  function autoStartIfNeeded() {
    if (!tourCompleted.value && !tourSkipped.value) {
      startTour()
    }
  }

  return {
    tourCompleted,
    tourSkipped,
    activeStepIndex,
    isActive,
    currentStep,
    totalSteps,
    isLastStep,
    progress,
    startTour,
    nextStep,
    prevStep,
    completeTour,
    skipTour,
    resetTour,
    autoStartIfNeeded,
  }
})
