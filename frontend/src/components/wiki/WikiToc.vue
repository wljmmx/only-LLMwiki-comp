<script setup lang="ts">
/**
 * P1-6: Wiki 页面目录大纲（TOC）
 *
 * 从渲染后的 DOM 中提取 h2/h3 标题，生成可点击的侧边目录。
 * 点击标题平滑滚动到对应位置。
 *
 * 用法：
 *   <WikiToc :content-el="pageContentRef" :page-key="currentPage.slug" />
 *
 * - contentEl: 渲染内容的 DOM 元素 ref
 * - pageKey: 当前页面 slug（变化时重新提取标题）
 */
import { ref, watch, nextTick, onMounted } from 'vue'

const props = defineProps<{
  /** 渲染内容的 DOM 元素（通过 ref 传入） */
  contentEl: HTMLElement | null
  /** 当前页面标识（变化时触发重新提取） */
  pageKey?: string
}>()

interface TocHeading {
  id: string
  text: string
  level: number
}

const headings = ref<TocHeading[]>([])
const activeId = ref<string>('')

/** 从 DOM 提取 h2/h3 标题，为无 id 的标题分配 id */
function extractHeadings() {
  if (!props.contentEl) {
    headings.value = []
    return
  }
  const els = props.contentEl.querySelectorAll('h2, h3')
  const result: TocHeading[] = []
  els.forEach((el, i) => {
    if (!el.id) {
      el.id = `wiki-heading-${i}`
    }
    result.push({
      id: el.id,
      text: el.textContent || '',
      level: el.tagName === 'H2' ? 2 : 3,
    })
  })
  headings.value = result
  if (result.length > 0) {
    activeId.value = result[0].id
  }
}

/** 点击标题 → 平滑滚动 */
function scrollToHeading(id: string) {
  const el = document.getElementById(id)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    activeId.value = id
  }
}

// 页面变化时重新提取
watch(
  () => props.pageKey,
  () => {
    nextTick(extractHeadings)
  },
)

watch(
  () => props.contentEl,
  () => {
    nextTick(extractHeadings)
  },
)

onMounted(() => {
  nextTick(extractHeadings)
})
</script>

<template>
  <nav v-if="headings.length >= 2" class="wiki-toc" aria-label="页面目录">
    <div class="toc-header">目录</div>
    <ul class="toc-list">
      <li
        v-for="h in headings"
        :key="h.id"
        :class="['toc-item', `toc-level-${h.level}`, { active: activeId === h.id }]"
      >
        <button type="button" class="toc-link" @click="scrollToHeading(h.id)">
          {{ h.text }}
        </button>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.wiki-toc {
  position: sticky;
  top: 16px;
  max-height: calc(100vh - 32px);
  overflow-y: auto;
  padding: 12px;
  background: var(--n-color, #fff);
  border: 1px solid var(--n-border-color, #e5e7eb);
  border-radius: 6px;
  font-size: 13px;
}

.toc-header {
  font-size: 12px;
  font-weight: 600;
  color: var(--n-text-color-3, #6b7280);
  margin-bottom: 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--n-border-color, #f0f0f0);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.toc-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.toc-item {
  margin: 0;
}

.toc-level-3 {
  padding-left: 16px;
}

.toc-link {
  display: block;
  width: 100%;
  text-align: left;
  border: none;
  background: none;
  padding: 4px 6px;
  color: var(--n-text-color-2, #6b7280);
  font-size: 13px;
  line-height: 1.5;
  cursor: pointer;
  border-radius: 4px;
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s;
}

.toc-link:hover {
  background: var(--n-color-hover, rgba(0, 0, 0, 0.04));
  color: var(--n-text-color, #111827);
}

.toc-item.active .toc-link {
  color: var(--n-primary-color, #2080f0);
  font-weight: 500;
  background: var(--n-color-target, rgba(32, 128, 240, 0.06));
}

@media (max-width: 1024px) {
  .wiki-toc {
    display: none;
  }
}
</style>
