import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useOnboardingStore, TOUR_STEPS } from './onboarding'

describe('stores/onboarding.ts', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('TOUR_STEPS 定义 5 个步骤', () => {
    expect(TOUR_STEPS).toHaveLength(5)
    expect(TOUR_STEPS.map((s) => s.id)).toEqual([
      'welcome',
      'sidebar',
      'documents',
      'wiki',
      'search',
    ])
  })

  it('每个 tour 步骤包含必填字段 id/title/description', () => {
    for (const step of TOUR_STEPS) {
      expect(step.id).toBeTruthy()
      expect(step.title).toBeTruthy()
      expect(step.description).toBeTruthy()
    }
  })

  it('初始 state：tour 未激活、未完成、未跳过', () => {
    const store = useOnboardingStore()
    expect(store.tourCompleted).toBe(false)
    expect(store.tourSkipped).toBe(false)
    expect(store.activeStepIndex).toBe(-1)
    expect(store.isActive).toBe(false)
    expect(store.currentStep).toBeNull()
    expect(store.progress).toBe(0)
    expect(store.isLastStep).toBe(false)
  })

  it('startTour 激活第一步并清除 skipped 标记', () => {
    const store = useOnboardingStore()
    store.tourSkipped = true
    store.startTour()
    expect(store.activeStepIndex).toBe(0)
    expect(store.isActive).toBe(true)
    expect(store.tourSkipped).toBe(false)
    expect(store.currentStep?.id).toBe('welcome')
    expect(store.isLastStep).toBe(false)
    expect(store.progress).toBe(1 / 5)
  })

  it('nextStep 推进到下一步', () => {
    const store = useOnboardingStore()
    store.startTour()
    store.nextStep()
    expect(store.activeStepIndex).toBe(1)
    expect(store.currentStep?.id).toBe('sidebar')
  })

  it('nextStep 在最后一步触发 completeTour', () => {
    const store = useOnboardingStore()
    store.startTour()
    // 推进到最后一步
    store.nextStep()
    store.nextStep()
    store.nextStep()
    store.nextStep()
    expect(store.activeStepIndex).toBe(4)
    expect(store.isLastStep).toBe(true)
    // 再次 nextStep 应完成 tour
    store.nextStep()
    expect(store.activeStepIndex).toBe(-1)
    expect(store.tourCompleted).toBe(true)
    expect(store.isActive).toBe(false)
  })

  it('prevStep 在第一步时为 no-op', () => {
    const store = useOnboardingStore()
    store.startTour()
    store.prevStep()
    expect(store.activeStepIndex).toBe(0)
  })

  it('prevStep 回退一步', () => {
    const store = useOnboardingStore()
    store.startTour()
    store.nextStep()
    store.nextStep()
    store.prevStep()
    expect(store.activeStepIndex).toBe(1)
  })

  it('completeTour 关闭 tour 并标记完成', () => {
    const store = useOnboardingStore()
    store.startTour()
    store.completeTour()
    expect(store.activeStepIndex).toBe(-1)
    expect(store.tourCompleted).toBe(true)
    expect(store.isActive).toBe(false)
  })

  it('skipTour 关闭 tour 并标记跳过（不标记完成）', () => {
    const store = useOnboardingStore()
    store.startTour()
    store.skipTour()
    expect(store.activeStepIndex).toBe(-1)
    expect(store.tourSkipped).toBe(true)
    expect(store.tourCompleted).toBe(false)
    expect(store.isActive).toBe(false)
  })

  it('resetTour 清除所有标记并关闭 tour', () => {
    const store = useOnboardingStore()
    store.startTour()
    store.completeTour()
    store.resetTour()
    expect(store.tourCompleted).toBe(false)
    expect(store.tourSkipped).toBe(false)
    expect(store.activeStepIndex).toBe(-1)
    expect(store.isActive).toBe(false)
  })

  it('autoStartIfNeeded 在未完成未跳过时自动启动', () => {
    const store = useOnboardingStore()
    store.autoStartIfNeeded()
    expect(store.isActive).toBe(true)
    expect(store.activeStepIndex).toBe(0)
  })

  it('autoStartIfNeeded 在已完成时不再启动', () => {
    const store = useOnboardingStore()
    store.tourCompleted = true
    store.autoStartIfNeeded()
    expect(store.isActive).toBe(false)
    expect(store.activeStepIndex).toBe(-1)
  })

  it('autoStartIfNeeded 在已跳过时不再启动', () => {
    const store = useOnboardingStore()
    store.tourSkipped = true
    store.autoStartIfNeeded()
    expect(store.isActive).toBe(false)
    expect(store.activeStepIndex).toBe(-1)
  })

  it('progress 在最后一步时为 100%', () => {
    const store = useOnboardingStore()
    store.startTour()
    for (let i = 0; i < TOUR_STEPS.length - 1; i++) store.nextStep()
    expect(store.activeStepIndex).toBe(TOUR_STEPS.length - 1)
    expect(store.progress).toBe(1)
  })

  it('currentStep 反映当前 activeStepIndex 指向的步骤', () => {
    const store = useOnboardingStore()
    store.startTour()
    store.nextStep()
    store.nextStep()
    expect(store.currentStep?.id).toBe('documents')
    expect(store.currentStep?.route).toBe('documents')
  })

  it('totalSteps 等于 TOUR_STEPS 长度', () => {
    const store = useOnboardingStore()
    expect(store.totalSteps).toBe(TOUR_STEPS.length)
  })
})
