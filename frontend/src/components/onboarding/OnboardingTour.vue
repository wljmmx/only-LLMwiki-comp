<script setup lang="ts">
import { watch, ref, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NProgress, NSpace } from 'naive-ui'
import { useOnboardingStore } from '@/stores/onboarding'

const store = useOnboardingStore()
const router = useRouter()

const targetEl = ref<HTMLElement | null>(null)
const popupStyle = ref<Record<string, string>>({})
const popupRef = ref<HTMLElement | null>(null)
const previouslyFocused = ref<HTMLElement | null>(null)

/** 根据当前步骤定位浮层 */
async function locateTarget() {
  const step = store.currentStep
  if (!step) {
    popupStyle.value = {}
    return
  }

  // 跨页面导航
  if (step.route) {
    const currentRoute = router.currentRoute.value.name
    if (currentRoute !== step.route) {
      await router.push({ name: step.route })
      await nextTick()
      // 等待视图渲染
      await new Promise((r) => setTimeout(r, 300))
    }
  }

  // 居中模式（无 selector 或 placement=center）
  if (!step.selector || step.placement === 'center') {
    popupStyle.value = {
      top: '50%',
      left: '50%',
      transform: 'translate(-50%, -50%)',
    }
    targetEl.value = null
    return
  }

  // 定位到目标元素
  const el = document.querySelector(step.selector) as HTMLElement | null
  targetEl.value = el
  if (!el) {
    // 找不到元素则居中
    popupStyle.value = {
      top: '50%',
      left: '50%',
      transform: 'translate(-50%, -50%)',
    }
    return
  }

  const rect = el.getBoundingClientRect()
  const popupWidth = 380
  const popupHeight = 240
  const gap = 16
  let top = 0
  let left = 0

  switch (step.placement) {
    case 'right':
      left = rect.right + gap
      top = rect.top + rect.height / 2 - popupHeight / 2
      break
    case 'left':
      left = rect.left - popupWidth - gap
      top = rect.top + rect.height / 2 - popupHeight / 2
      break
    case 'bottom':
      left = rect.left + rect.width / 2 - popupWidth / 2
      top = rect.bottom + gap
      break
    case 'top':
      left = rect.left + rect.width / 2 - popupWidth / 2
      top = rect.top - popupHeight - gap
      break
    default:
      left = rect.right + gap
      top = rect.top
  }

  // 边界修正
  top = Math.max(16, Math.min(top, window.innerHeight - popupHeight - 16))
  left = Math.max(16, Math.min(left, window.innerWidth - popupWidth - 16))

  popupStyle.value = {
    top: `${top}px`,
    left: `${left}px`,
  }
}

/** 高亮目标元素的遮罩 inset */
const highlightStyle = ref<Record<string, string>>({})

function updateHighlight() {
  if (targetEl.value) {
    const rect = targetEl.value.getBoundingClientRect()
    highlightStyle.value = {
      'box-shadow': `0 0 0 9999px rgba(0,0,0,0.5)`,
      'border-radius': '8px',
      top: `${rect.top - 4}px`,
      left: `${rect.left - 4}px`,
      width: `${rect.width + 8}px`,
      height: `${rect.height + 8}px`,
    }
  } else {
    highlightStyle.value = {}
  }
}

// 步骤变化时重新定位 + 聚焦浮层
watch(
  () => store.activeStepIndex,
  async () => {
    if (store.isActive) {
      await locateTarget()
      await nextTick()
      updateHighlight()
      // 聚焦浮层卡片（键盘可达性）
      await nextTick()
      popupRef.value?.focus()
    }
  },
)

// 引导激活/关闭时管理焦点
watch(
  () => store.isActive,
  async (active) => {
    if (active) {
      // 捕获当前焦点元素，关闭后恢复
      previouslyFocused.value = document.activeElement as HTMLElement | null
      await nextTick()
      popupRef.value?.focus()
    } else {
      // 恢复焦点到触发元素
      await nextTick()
      previouslyFocused.value?.focus?.()
      previouslyFocused.value = null
    }
  },
)

// 窗口大小变化时重新定位
function handleResize() {
  if (store.isActive) {
    locateTarget().then(() => updateHighlight())
  }
}

/** 获取浮层内所有可聚焦元素 */
function getFocusableElements(): HTMLElement[] {
  if (!popupRef.value) return []
  return Array.from(
    popupRef.value.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((el) => !el.hasAttribute('disabled') && el.offsetParent !== null)
}

/** Esc 键跳过引导 + Tab 焦点陷阱（键盘可达性 P4-4） */
function handleKeydown(e: KeyboardEvent) {
  if (!store.isActive) return
  if (e.key === 'Escape') {
    e.preventDefault()
    handleSkip()
    return
  }
  // 焦点陷阱：Tab/Shift+Tab 循环在浮层内
  if (e.key === 'Tab') {
    const focusables = getFocusableElements()
    if (focusables.length === 0) {
      e.preventDefault()
      popupRef.value?.focus()
      return
    }
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    if (e.shiftKey) {
      if (document.activeElement === first || document.activeElement === popupRef.value) {
        e.preventDefault()
        last.focus()
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
  }
}

onMounted(() => {
  window.addEventListener('resize', handleResize)
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  window.removeEventListener('keydown', handleKeydown)
})

function handleNext() {
  store.nextStep()
}

function handlePrev() {
  store.prevStep()
}

function handleSkip() {
  store.skipTour()
}

function handleFinish() {
  store.completeTour()
}
</script>

<template>
  <template v-if="store.isActive && store.currentStep">
    <!-- 全屏点击层：始终渲染以接收点击跳过 -->
    <!-- 居中模式自带遮罩背景；spotlight 模式透明（由 spotlight 视觉层提供暗化） -->
    <div
      class="tour-overlay"
      :class="{ 'tour-overlay--spotlight': targetEl }"
      aria-hidden="true"
      @click.self="handleSkip"
    />
    <!-- spotlight 视觉层（定位步骤，pointer-events: none 让点击穿透到 overlay） -->
    <div
      v-if="targetEl"
      class="tour-spotlight"
      :style="highlightStyle"
      aria-hidden="true"
    />

    <!-- 浮层卡片（P4-4: 对话框语义 + 焦点管理） -->
    <div
      ref="popupRef"
      class="tour-popup"
      :style="popupStyle"
      role="dialog"
      aria-modal="true"
      aria-labelledby="tour-title"
      aria-describedby="tour-desc"
      tabindex="-1"
    >
      <div class="tour-header">
        <span class="tour-step-badge" aria-hidden="true">
          {{ store.activeStepIndex + 1 }} / {{ store.totalSteps }}
        </span>
        <h3 id="tour-title" class="tour-title">{{ store.currentStep.title }}</h3>
      </div>

      <div class="tour-body">
        <p id="tour-desc" class="tour-desc">{{ store.currentStep.description }}</p>
      </div>

      <NProgress
        :percentage="Math.round(store.progress * 100)"
        :height="3"
        :show-indicator="false"
        class="tour-progress"
        aria-hidden="true"
      />

      <div class="tour-footer">
        <NSpace justify="space-between" align="center">
          <NButton size="small" quaternary @click="handleSkip">
            跳过引导
          </NButton>
          <NSpace>
            <NButton
              v-if="store.activeStepIndex > 0"
              size="small"
              secondary
              @click="handlePrev"
            >
              上一步
            </NButton>
            <NButton
              v-if="!store.isLastStep"
              size="small"
              type="primary"
              @click="handleNext"
            >
              下一步
            </NButton>
            <NButton
              v-else
              size="small"
              type="primary"
              @click="handleFinish"
            >
              完成
            </NButton>
          </NSpace>
        </NSpace>
      </div>
    </div>
  </template>
</template>

<style scoped>
.tour-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 9998;
}

/* spotlight 模式：overlay 透明（暗化由 spotlight 的 box-shadow 提供），仅作点击层 */
.tour-overlay--spotlight {
  background: transparent;
}

.tour-spotlight {
  position: fixed;
  z-index: 9998;
  pointer-events: none;
  transition: all 0.3s ease;
}

.tour-popup {
  position: fixed;
  width: 380px;
  background: var(--n-color, #fff);
  border: 1px solid var(--n-border-color, #e5e7eb);
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.16);
  z-index: 9999;
  padding: 20px;
  transition: all 0.3s ease;
  outline: none;
}

.tour-popup:focus-visible {
  box-shadow: 0 0 0 3px var(--n-primary-color, #18a058), 0 8px 32px rgba(0, 0, 0, 0.16);
}

.tour-header {
  margin-bottom: 12px;
}

.tour-step-badge {
  display: inline-block;
  font-size: 12px;
  color: var(--n-text-color-3, #6b7280);
  background: var(--n-color-hover, #f3f4f6);
  padding: 2px 8px;
  border-radius: 10px;
  margin-bottom: 8px;
}

.tour-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
  color: var(--n-text-color, #111827);
}

.tour-body {
  margin-bottom: 16px;
}

.tour-desc {
  font-size: 14px;
  line-height: 1.6;
  color: var(--n-text-color-2, #374151);
  white-space: pre-line;
  margin: 0;
}

.tour-progress {
  margin-bottom: 12px;
}

.tour-footer {
  border-top: 1px solid var(--n-divider-color, #e5e7eb);
  padding-top: 12px;
}
</style>
