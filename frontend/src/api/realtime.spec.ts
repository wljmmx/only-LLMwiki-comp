/**
 * realtime API 单元测试（S15-5 实时协作编辑）
 *
 * 覆盖：
 * 1. 类型定义：CollabUser / RoomState / ClientMessage / ServerMessage 形状
 * 2. HTTP 端点：listRooms / getRoom（mock axios）
 * 3. WebSocket URL 构造：buildCollabWsUrl（http/https/token/slug 编码）
 * 4. createCollabSocket：自动注入 token 并创建 WebSocket
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// ────────── mock @/api/index ──────────
// 提供 default（axios 实例）+ 命名导出 getAuthToken / getApiBaseUrl
// vi.mock 工厂被 hoist 到文件顶部，需用 vi.hoisted 让变量也可 hoist
const { mockApiGet, mockGetAuthToken, mockGetApiBaseUrl } = vi.hoisted(() => ({
  mockApiGet: vi.fn(),
  mockGetAuthToken: vi.fn(),
  mockGetApiBaseUrl: vi.fn(() => '/api'),
}))

vi.mock('@/api/index', () => ({
  default: { get: mockApiGet },
  getAuthToken: mockGetAuthToken,
  getApiBaseUrl: mockGetApiBaseUrl,
}))

import api, { getAuthToken, getApiBaseUrl } from '@/api/index'
import {
  listRooms,
  getRoom,
  buildCollabWsUrl,
  createCollabSocket,
  type CollabUser,
  type RoomState,
  type ClientMessage,
  type ServerMessage,
} from './realtime'

// ────────── Mock WebSocket ──────────
class MockWebSocket {
  static instances: MockWebSocket[] = []
  static lastInstance: MockWebSocket | null = null

  url: string
  readyState: number = 0 // CONNECTING
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
    MockWebSocket.lastInstance = this
  }
  close() {
    this.readyState = 3
  }
  send() {}
}

// 保存原始 WebSocket
const originalWebSocket = global.WebSocket

describe('api/realtime.ts — S15-5 实时协作 API', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetAuthToken.mockReturnValue(null)
    mockGetApiBaseUrl.mockReturnValue('/api')
    MockWebSocket.instances = []
    MockWebSocket.lastInstance = null
    global.WebSocket = MockWebSocket as any
  })

  afterEach(() => {
    global.WebSocket = originalWebSocket
  })

  // ────────── 1. 类型定义 ──────────

  describe('类型定义', () => {
    it('CollabUser 形状符合预期', () => {
      const u: CollabUser = {
        user_id: 'user:alice',
        username: 'alice',
        display_name: 'Alice',
        role: 'admin',
      }
      expect(u.user_id).toBe('user:alice')
      expect(u.role).toBe('admin')
    })

    it('RoomState 形状符合预期', () => {
      const r: RoomState = {
        slug: 'nginx-502',
        online_users: [],
        online_count: 0,
        lock_holder: null,
        lock_acquired_at: null,
      }
      expect(r.slug).toBe('nginx-502')
      expect(r.lock_holder).toBeNull()
    })

    it('ClientMessage 各类型形状符合预期', () => {
      const heartbeat: ClientMessage = { type: 'heartbeat' }
      const acquire: ClientMessage = { type: 'acquire_lock' }
      const release: ClientMessage = { type: 'release_lock' }
      const edit: ClientMessage = {
        type: 'edit_event',
        payload: { op: 'insert', text: 'hello' },
      }
      const cursor: ClientMessage = {
        type: 'cursor',
        payload: { line: 10, col: 5 },
      }
      expect(heartbeat.type).toBe('heartbeat')
      expect(acquire.type).toBe('acquire_lock')
      expect(release.type).toBe('release_lock')
      expect(edit.type).toBe('edit_event')
      expect((edit as any).payload.op).toBe('insert')
      expect(cursor.type).toBe('cursor')
      expect((cursor as any).payload.line).toBe(10)
    })

    it('ServerMessage 各类型形状符合预期', () => {
      const presence: ServerMessage = {
        type: 'presence',
        users: [],
        lock_holder: null,
      }
      const joined: ServerMessage = {
        type: 'user_joined',
        user: {
          user_id: 'user:bob',
          username: 'bob',
          display_name: 'Bob',
          role: 'viewer',
        },
      }
      const left: ServerMessage = {
        type: 'user_left',
        user_id: 'user:bob',
        reason: 'client_closed',
      }
      const lockAck: ServerMessage = { type: 'lock_acquired_ack', user_id: 'user:alice' }
      const lockDenied: ServerMessage = {
        type: 'lock_denied',
        reason: 'held_by_other',
        holder: { user_id: 'user:bob' },
      }
      const err: ServerMessage = { type: 'error', message: 'invalid message' }
      const heartbeatAck: ServerMessage = { type: 'heartbeat_ack' }
      const editEvent: ServerMessage = {
        type: 'edit_event',
        user_id: 'user:bob',
        payload: { op: 'insert' },
      }
      const cursorEvent: ServerMessage = {
        type: 'cursor',
        user_id: 'user:bob',
        payload: { line: 1, col: 1 },
      }
      const lockAcquired: ServerMessage = { type: 'lock_acquired', user_id: 'user:alice' }
      const lockReleased: ServerMessage = {
        type: 'lock_released',
        user_id: 'user:alice',
        reason: 'client_released',
      }

      expect(presence.type).toBe('presence')
      expect(joined.type).toBe('user_joined')
      expect(left.type).toBe('user_left')
      expect(lockAck.type).toBe('lock_acquired_ack')
      expect(lockDenied.type).toBe('lock_denied')
      expect(err.type).toBe('error')
      expect(heartbeatAck.type).toBe('heartbeat_ack')
      expect(editEvent.type).toBe('edit_event')
      expect(cursorEvent.type).toBe('cursor')
      expect(lockAcquired.type).toBe('lock_acquired')
      expect(lockReleased.type).toBe('lock_released')
    })
  })

  // ────────── 2. HTTP 端点 ──────────

  describe('HTTP 端点', () => {
    it('listRooms 调用 GET /realtime/rooms 并返回数据', async () => {
      const expected = {
        rooms: [
          {
            slug: 'nginx-502',
            online_users: [],
            online_count: 1,
            lock_holder: null,
            lock_acquired_at: null,
          },
        ],
        count: 1,
      }
      mockApiGet.mockResolvedValue(expected)

      const res = await listRooms()

      expect(api.get).toHaveBeenCalledWith('/realtime/rooms')
      expect(res).toEqual(expected)
    })

    it('getRoom 调用 GET /realtime/rooms/{slug} 并对 slug 编码', async () => {
      const expected = {
        slug: 'nginx-502',
        online_users: [],
        online_count: 0,
        lock_holder: null,
        lock_acquired_at: null,
      }
      mockApiGet.mockResolvedValue(expected)

      const res = await getRoom('nginx-502')

      expect(api.get).toHaveBeenCalledWith('/realtime/rooms/nginx-502')
      expect(res).toEqual(expected)
    })

    it('getRoom 对特殊字符 slug 进行 URL 编码', async () => {
      mockApiGet.mockResolvedValue({
        slug: 'wiki/with spaces',
        online_users: [],
        online_count: 0,
        lock_holder: null,
        lock_acquired_at: null,
      })

      await getRoom('wiki/with spaces')

      // encodeURIComponent 把 / 与空格分别编码
      expect(api.get).toHaveBeenCalledWith(
        '/realtime/rooms/wiki%2Fwith%20spaces',
      )
    })
  })

  // ────────── 3. WebSocket URL 构造 ──────────

  describe('buildCollabWsUrl', () => {
    it('http 页面使用 ws:// 协议', () => {
      // jsdom 默认 http://localhost
      const url = buildCollabWsUrl('nginx-502', null)
      expect(url.startsWith('ws://')).toBe(true)
    })

    it('https 页面使用 wss:// 协议', () => {
      // 模拟 https
      const originalProtocol = window.location.protocol
      Object.defineProperty(window, 'location', {
        value: { ...window.location, protocol: 'https:', host: 'opskg.io' },
        writable: true,
      })

      const url = buildCollabWsUrl('nginx-502', null)
      expect(url.startsWith('wss://')).toBe(true)

      // 还原
      Object.defineProperty(window, 'location', {
        value: { ...window.location, protocol: originalProtocol },
        writable: true,
      })
    })

    it('URL 包含 /api/realtime/collab/{slug} 路径', () => {
      const url = buildCollabWsUrl('nginx-502', null)
      expect(url).toContain('/api/realtime/collab/nginx-502')
    })

    it('无 token 时 URL 不包含 ?token=', () => {
      const url = buildCollabWsUrl('nginx-502', null)
      expect(url).not.toContain('?token=')
    })

    it('有 token 时 URL 附加 ?token=<token>', () => {
      const url = buildCollabWsUrl('nginx-502', 'secret-token')
      expect(url).toContain('?token=secret-token')
    })

    it('特殊字符 token 被 URL 编码', () => {
      const url = buildCollabWsUrl('nginx-502', 'tok&with=special')
      // & = 被 encodeURIComponent 编码为 %26 %3D
      expect(url).toContain('?token=tok%26with%3Dspecial')
    })

    it('特殊字符 slug 被 URL 编码', () => {
      const url = buildCollabWsUrl('wiki/with space', null)
      expect(url).toContain('/api/realtime/collab/wiki%2Fwith%20space')
    })

    it('使用 getApiBaseUrl 派生路径前缀', () => {
      mockGetApiBaseUrl.mockReturnValue('/custom-api')
      const url = buildCollabWsUrl('nginx-502', null)
      expect(url).toContain('/custom-api/realtime/collab/nginx-502')
      mockGetApiBaseUrl.mockReturnValue('/api') // 还原
    })
  })

  // ────────── 4. createCollabSocket ──────────

  describe('createCollabSocket', () => {
    it('调用 getAuthToken 获取 token', () => {
      mockGetAuthToken.mockReturnValue(null)
      createCollabSocket('nginx-502')
      expect(getAuthToken).toHaveBeenCalledTimes(1)
    })

    it('token 为 null 时创建无 ?token= 的 WebSocket', () => {
      mockGetAuthToken.mockReturnValue(null)
      const socket = createCollabSocket('nginx-502')
      expect(socket).toBeInstanceOf(MockWebSocket)
      expect(MockWebSocket.lastInstance!.url).not.toContain('?token=')
    })

    it('token 存在时创建带 ?token= 的 WebSocket', () => {
      mockGetAuthToken.mockReturnValue('my-token')
      const socket = createCollabSocket('nginx-502')
      expect(socket).toBeInstanceOf(MockWebSocket)
      expect(MockWebSocket.lastInstance!.url).toContain('?token=my-token')
    })

    it('返回的 WebSocket 实例可设置 onopen 回调', () => {
      const socket = createCollabSocket('nginx-502')
      const cb = vi.fn()
      socket.onopen = cb
      expect(socket.onopen).toBe(cb)
    })
  })
})
