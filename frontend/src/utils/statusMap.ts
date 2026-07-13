/**
 * 状态/类型 → 颜色/标签类型集中映射（P1-19）
 *
 * 将散落在 GraphView/TopologyView/ChangesView 中的硬编码节点色与
 * 严重度/变更类型映射集中管理。颜色值引用 CSS 变量，由 style.css
 * 在 :root 与 [data-theme='dark'] 下分别定义浅/深双套，实现深色
 * 模式自动适配。
 */

/** Naive UI NTag 类型联合 */
export type NTagType = 'default' | 'info' | 'success' | 'warning' | 'error' | 'primary'

/**
 * 节点类型 → CSS 变量引用（11 种实体色）
 * 从 GraphView.vue 迁移，颜色值改为引用 style.css 中定义的 CSS 变量，
 * 浅/深双套由 :root 与 [data-theme='dark'] 分别提供。
 */
export const nodeTypeColor: Record<string, string> = {
  Host: 'var(--opskg-node-host)',
  Service: 'var(--opskg-node-service)',
  Component: 'var(--opskg-node-component)',
  Parameter: 'var(--opskg-node-parameter)',
  Command: 'var(--opskg-node-command)',
  Procedure: 'var(--opskg-node-procedure)',
  Incident: 'var(--opskg-node-incident)',
  Symptom: 'var(--opskg-node-symptom)',
  Experience: 'var(--opskg-node-experience)',
  Concept: 'var(--opskg-node-concept)',
  Document: 'var(--opskg-node-document)',
}

/**
 * 严重度 → NTag 类型（迁移自 ChangesView）
 * 覆盖：normal/info/low/warning/high/critical/fatal
 * 注：IncidentsView 保留其本地 severityTagType（info→default），
 * 以维持其既有视觉外观，故此处不强制统一。
 */
export const severityTagType: Record<string, NTagType> = {
  normal: 'default',
  info: 'info',
  low: 'info',
  warning: 'warning',
  high: 'error',
  critical: 'error',
  fatal: 'error',
}

/** 变更类型 → NTag 类型（迁移自 ChangesView） */
export const changeTypeColor: Record<string, NTagType> = {
  deployment: 'primary',
  config_change: 'warning',
  migration: 'info',
  scaling: 'info',
  restart: 'default',
  rollback: 'error',
  patch: 'success',
  other: 'default',
}

/** 严重度 → NTag 类型查询（缺省 default） */
export function tagSeverity(s?: string): NTagType {
  return severityTagType[s || ''] || 'default'
}
