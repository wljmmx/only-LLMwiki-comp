/**
 * P2-2: 通用格式化工具函数
 *
 * 集中管理日期/文件大小等格式化逻辑，替换散落在各组件内的同名函数。
 */

/** 格式化 ISO 日期为 YYYY-MM-DD */
export function formatDate(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`
}

/** 格式化 ISO 日期时间为 YYYY-MM-DD HH:mm */
export function formatDateTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const date = formatDate(iso)
  const time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return `${date} ${time}`
}

/** 格式化文件大小（字节 → KB/MB/GB） */
export function formatFileSize(bytes: number): string {
  if (!bytes || bytes < 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = bytes
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex++
  }
  // 整数不带小数，非整数保留一位
  const formatted = size % 1 === 0 ? size.toFixed(0) : size.toFixed(1)
  return `${formatted} ${units[unitIndex]}`
}

/** 格式化 epoch 毫秒为 HH:mm:ss（用于协作面板等实时时间戳） */
export function formatClock(ms: number): string {
  if (!ms || isNaN(ms)) return ''
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 格式化 epoch 毫秒为 MM-DD HH:mm:ss（用于协作历史等带日期的时间戳） */
export function formatClockWithDate(ms: number): string {
  if (!ms || isNaN(ms)) return ''
  const d = new Date(ms)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${formatClock(ms)}`
}

/** 格式化相对时间（"刚刚" / "X 分钟前" / "X 小时前" / 月日时分） */
export function formatRelativeTime(ts: number): string {
  if (!ts || isNaN(ts)) return ''
  const diff = Date.now() - ts
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}-${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** P2-20: Wiki 页面类型中文标签映射（统一全视图翻译） */
export const typeLabelMap: Record<string, string> = {
  entity: '实体',
  concept: '概念',
  incident: '事件',
  runbook: '运行手册',
  service: '服务',
  host: '主机',
}

/** P2-20: 获取类型中文标签，未知类型返回原文 */
export function getTypeLabel(type: string): string {
  return typeLabelMap[type] || type
}
