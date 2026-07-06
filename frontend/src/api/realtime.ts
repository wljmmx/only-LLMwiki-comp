/**
 * 实时协作 API（S15-5）
 *
 * 后端端点：
 *   WS  /realtime/collab/{slug}?token=<token>
 *   GET /realtime/rooms                  列出所有房间
 *   GET /realtime/rooms/{slug}           获取指定房间状态
 *
 * WebSocket URL 协议：
 *   - 同源 https → wss://host/api/realtime/collab/{slug}
 *   - 同源 http  → ws://host/api/realtime/collab/{slug}
 *   - token 通过 query param 传递（与 HTTP Bearer 等价）
 */
import { getAuthToken, getApiBaseUrl } from './index'
import api from './index'

// ────────── 类型定义 ──────────

export interface CollabUser {
  user_id: string
  username: string
  display_name: string
  role: 'admin' | 'operator' | 'viewer'
}

export interface RoomState {
  slug: string
  online_users: CollabUser[]
  online_count: number
  lock_holder: string | null
  lock_acquired_at: number | null
}

/** 客户端 → 服务端 消息 */
export type ClientMessage =
  | { type: 'heartbeat' }
  | { type: 'acquire_lock' }
  | { type: 'release_lock' }
  | { type: 'edit_event'; payload: Record<string, unknown> }
  | { type: 'cursor'; payload: { line: number; col: number } }

/** 服务端 → 客户端 消息 */
export type ServerMessage =
  | { type: 'presence'; users: CollabUser[]; lock_holder: string | null }
  | { type: 'user_joined'; user: CollabUser }
  | { type: 'user_left'; user_id: string; reason?: string }
  | { type: 'lock_acquired'; user_id: string }
  | { type: 'lock_released'; user_id: string; reason?: string }
  | { type: 'lock_denied'; reason: string; holder?: Partial<CollabUser> }
  | { type: 'lock_acquired_ack'; user_id: string }
  | { type: 'edit_event'; user_id: string; payload: Record<string, unknown> }
  | { type: 'cursor'; user_id: string; payload: { line: number; col: number } }
  | { type: 'heartbeat_ack' }
  | { type: 'error'; message: string }

// ────────── HTTP 端点 ──────────

/** 列出所有协作房间 */
export async function listRooms(): Promise<{ rooms: RoomState[]; count: number }> {
  return api.get('/realtime/rooms') as Promise<{ rooms: RoomState[]; count: number }>
}

/** 获取指定房间状态 */
export async function getRoom(slug: string): Promise<RoomState> {
  return api.get(`/realtime/rooms/${encodeURIComponent(slug)}`) as Promise<RoomState>
}

// ────────── WebSocket URL 构造 ──────────

/**
 * 构造协作 WebSocket URL
 *
 * 协议：基于当前页面 location（https → wss，http → ws）
 * 路径：/api/realtime/collab/{slug}（dev 模式由 vite proxy 转发到后端）
 * 鉴权：token 通过 query param 传递
 */
export function buildCollabWsUrl(slug: string, token: string | null): string {
  const loc = window.location
  const protocol = loc.protocol === 'https:' ? 'wss:' : 'ws:'
  // 复用 api base 路径（默认 /api），保持与 axios 同源
  const apiBase = getApiBaseUrl()
  const path = `${apiBase}/realtime/collab/${encodeURIComponent(slug)}`
  const url = `${protocol}//${loc.host}${path}`
  return token ? `${url}?token=${encodeURIComponent(token)}` : url
}

/**
 * 创建协作 WebSocket 连接（带 token 自动注入）
 *
 * 调用方负责处理 onopen / onmessage / onerror / onclose。
 */
export function createCollabSocket(slug: string): WebSocket {
  const token = getAuthToken()
  const url = buildCollabWsUrl(slug, token)
  return new WebSocket(url)
}
