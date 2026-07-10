/**
 * P2-13a: Wiki 问答反馈机制纯函数测试
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  computeFeedbackFingerprint,
  getFeedback,
  setFeedback,
  clearFeedback,
  _clearAllForTest,
} from '@/utils/queryFeedback'

describe('queryFeedback', () => {
  beforeEach(() => {
    _clearAllForTest()
  })

  describe('computeFeedbackFingerprint', () => {
    it('同一 question + citedSlugs → 相同指纹', () => {
      const fp1 = computeFeedbackFingerprint('什么是 nginx', ['nginx-502'])
      const fp2 = computeFeedbackFingerprint('什么是 nginx', ['nginx-502'])
      expect(fp1).toBe(fp2)
    })

    it('question 大小写/首尾空格不敏感（归一化）', () => {
      const fp1 = computeFeedbackFingerprint('  什么是 nginx  ', ['nginx-502'])
      const fp2 = computeFeedbackFingerprint('什么是 NGINX', ['nginx-502'])
      expect(fp1).toBe(fp2)
    })

    it('不同 citedSlugs → 不同指纹', () => {
      const fp1 = computeFeedbackFingerprint('什么是 nginx', ['nginx-502'])
      const fp2 = computeFeedbackFingerprint('什么是 nginx', ['reverse-proxy'])
      expect(fp1).not.toBe(fp2)
    })

    it('无 citedSlugs 时用空串占位（仍稳定）', () => {
      const fp1 = computeFeedbackFingerprint('什么是 nginx', [])
      const fp2 = computeFeedbackFingerprint('什么是 nginx', [])
      expect(fp1).toBe(fp2)
      expect(fp1).toMatch(/^[0-9a-f]{8}$/)
    })
  })

  describe('setFeedback / getFeedback / clearFeedback', () => {
    it('setFeedback 后 getFeedback 返回该 rating', () => {
      const fp = computeFeedbackFingerprint('q1', ['s1'])
      expect(getFeedback(fp)).toBeNull()
      setFeedback(fp, 'up')
      expect(getFeedback(fp)).toBe('up')
    })

    it('重复 setFeedback 同值 → 覆盖 ts，rating 不变', () => {
      const fp = computeFeedbackFingerprint('q1', ['s1'])
      setFeedback(fp, 'up')
      setFeedback(fp, 'up')
      expect(getFeedback(fp)).toBe('up')
    })

    it('setFeedback 切换 rating（up → down）', () => {
      const fp = computeFeedbackFingerprint('q1', ['s1'])
      setFeedback(fp, 'up')
      setFeedback(fp, 'down')
      expect(getFeedback(fp)).toBe('down')
    })

    it('clearFeedback 后 getFeedback 返回 null', () => {
      const fp = computeFeedbackFingerprint('q1', ['s1'])
      setFeedback(fp, 'up')
      clearFeedback(fp)
      expect(getFeedback(fp)).toBeNull()
    })

    it('clearFeedback 未记录的指纹不报错', () => {
      const fp = computeFeedbackFingerprint('never', [])
      expect(() => clearFeedback(fp)).not.toThrow()
      expect(getFeedback(fp)).toBeNull()
    })
  })

  describe('localStorage 损坏降级', () => {
    it('localStorage 存非 JSON 时 getFeedback 返回 null（不抛错）', () => {
      localStorage.setItem('opskg:wiki:feedback', 'not-json{{{')
      const fp = computeFeedbackFingerprint('q1', [])
      expect(getFeedback(fp)).toBeNull()
    })
  })
})
