import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/index', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import api from '@/api/index'
import {
  getReviewQueue,
  getReviewStats,
  approveReview,
  rejectReview,
  batchApprove,
} from './review'

describe('api/review.ts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('getReviewQueue GET /review/queue 携带 status/limit/offset 与默认值', async () => {
    const data = { items: [], total: 0 }
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue(data)
    await getReviewQueue('pending')
    expect(api.get).toHaveBeenCalledWith('/review/queue', {
      params: { status: 'pending', limit: 50, offset: 0 },
    })
  })

  it('getReviewStats GET /review/stats', async () => {
    ;(api.get as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await getReviewStats()
    expect(api.get).toHaveBeenCalledWith('/review/stats')
  })

  it('approveReview POST /review/{id}/approve', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await approveReview('r1')
    expect(api.post).toHaveBeenCalledWith('/review/r1/approve')
  })

  it('rejectReview POST /review/{id}/reject 携带 reason', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await rejectReview('r1', '信息不足')
    expect(api.post).toHaveBeenCalledWith('/review/r1/reject', { reason: '信息不足' })
  })

  it('batchApprove POST /review/batch-approve 携带 ids', async () => {
    ;(api.post as ReturnType<typeof vi.fn>).mockResolvedValue({})
    await batchApprove(['r1', 'r2'])
    expect(api.post).toHaveBeenCalledWith('/review/batch-approve', { ids: ['r1', 'r2'] })
  })
})