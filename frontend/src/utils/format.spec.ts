import { describe, it, expect } from 'vitest'
import { formatDate, formatDateTime, formatFileSize } from '@/utils/format'

describe('format.ts', () => {
  describe('formatDate', () => {
    it('格式化 ISO 日期为 YYYY-MM-DD', () => {
      expect(formatDate('2026-07-10T10:00:00Z')).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    })
    it('空字符串返回空', () => {
      expect(formatDate('')).toBe('')
    })
    it('无效日期返回原值', () => {
      expect(formatDate('not-a-date')).toBe('not-a-date')
    })
  })

  describe('formatDateTime', () => {
    it('格式化为 YYYY-MM-DD HH:mm', () => {
      const result = formatDateTime('2026-07-10T10:30:00Z')
      expect(result).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
    })
    it('空字符串返回空', () => {
      expect(formatDateTime('')).toBe('')
    })
  })

  describe('formatFileSize', () => {
    it('小于 1KB 显示 B', () => {
      expect(formatFileSize(512)).toBe('512 B')
    })
    it('KB 保留一位小数', () => {
      expect(formatFileSize(1536)).toBe('1.5 KB')
    })
    it('MB 转换', () => {
      expect(formatFileSize(1048576)).toBe('1.0 MB')
    })
    it('0 或负数返回 0 B', () => {
      expect(formatFileSize(0)).toBe('0 B')
      expect(formatFileSize(-1)).toBe('0 B')
    })
  })
})
