import api from './index'
import type { ReviewQueueResponse, ReviewStats } from '@/types/api'

export function getReviewQueue(status?: string, limit = 50, offset = 0) {
  return api.get<unknown, ReviewQueueResponse>('/review/queue', {
    params: { status, limit, offset },
  })
}

export function getReviewStats() {
  return api.get<unknown, ReviewStats>('/review/stats')
}

export function approveReview(itemId: string) {
  return api.post(`/review/${itemId}/approve`)
}

export function rejectReview(itemId: string, reason?: string) {
  return api.post(`/review/${itemId}/reject`, { reason })
}

export function batchApprove(ids: string[]) {
  return api.post('/review/batch-approve', { ids })
}
