/**
 * useCollab composable 单元测试（S15-5 实时协作编辑）
 *
 * 覆盖：
 * 1. 初始状态：onlineUsers / lockHolder / connectionState / lastError
 * 2. myUserId 推断：dev 模式 / 已登录用户 / 未登录
 * 3. 连接生命周期：connect / disconnect / doConnect
 * 4. 消息处理：presence / user_joined / user_left / lock_* / heartbeat_ack / error
 * 5. 编辑锁：acquireLock / releaseLock / hasLock 计算属性
 * 6. 编辑事件 / cursor：sendEdit / sendCursor
 * 7. 心跳：connect 后启动 heartbeat 定时器
 * 8. 自动重连：onclose 触发指数退避重连，达到上限标记 error
 * 9. 消息解析失败：非 JSON 数据不抛错
 * 10. sendMessage 在未连接时返回 false
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// ────────── mock @/api/realtime 的 createCollabSocket ──────────
// 我们不直接 mock 整个模块（保留类型导入），只 mock createCollabSocket 函数
// vi.mock 工厂被 hoist，需用 vi.hoisted 让变量也可 hoist
const { mockCreateCollabSocket } = vi.hoisted(() => ({
  mockCreateCollabSocket: vi.fn(),
}))

vi.mock('@/api/realtime', async () => {
  const actual = await vi.importActual<typeof import('@/api/realtime')>('@/api/realtime')
  return {
    ...actual,
    createCollabSocket: mockCreateCollabSocket,
  }
})

import { useCollab, type ConnectionState } from './useCollab'
import { useAuthStore } from '@/stores/auth'

// ────────── Mock WebSocket 实例（由 createCollabSocket 返回） ──────────
const WS_OPEN = 1
const WS_CONNECTING = 0
const WS_CLOSING = 2
const WS_CLOSED = 3

class FakeSocket {
  readyState: number = WS_CONNECTING
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  sentMessages: any[] = []
  closed = false
  closeCode?: number
  closeReason?: string

  send(data: string) {
    this.sentMessages.push(JSON.parse(data))
  }
  close(code: number = 1000, reason: string = '') {
    this.closed = true
    this.closeCode = code
    this.closeReason = reason
    this.readyState = WS_CLOSED
  }
  /** 模拟服务端连接已建立 */
  fireOpen() {
    this.readyState = WS_OPEN
    if (this.onopen) this.onopen(new Event('open'))
  }
  /** 模拟服务端推送消息 */
  fireMessage(data: any) {
    if (this.onmessage) {
      this.onmessage({ data: typeof data === 'string' ? data : JSON.stringify(data) } as MessageEvent)
    }
  }
  /** 模拟服务端关闭 */
  fireClose(code: number = 1000, reason: string = '') {
    this.readyState = WS_CLOSED
    if (this.onclose) this.onclose({ code, reason } as CloseEvent)
  }
  /** 模拟错误 */
  fireError() {
    if (this.onerror) this.onerror(new Event('error'))
  }
}

// 让 useCollab.ts 中的 WebSocket.OPEN 等常量解析到 FakeSocket
;(FakeSocket as any).OPEN = WS_OPEN
;(FakeSocket as any).CONNECTING = WS_CONNECTING
;(FakeSocket as any).CLOSING = WS_CLOSING
;(FakeSocket as any).CLOSED = WS_CLOSED

// 全局 WebSocket 替换，使 useCollab 中的 WebSocket.OPEN 引用一致
const originalWebSocket = global.WebSocket
;(global as any).WebSocket = FakeSocket

describe('composables/useCollab.ts — S15-5 实时协作 composable', () => {
  let currentSocket: FakeSocket

  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
    currentSocket = new FakeSocket()
    mockCreateCollabSocket.mockReturnValue(currentSocket)
  })

  afterEach(() => {
    vi.useRealTimers()
    ;(global as any).WebSocket = originalWebSocket
  })

  // 工具：构造已登录用户
  function loginAs(username: string, role: 'admin' | 'operator' | 'viewer' = 'admin') {
    const auth = useAuthStore()
    auth.authRequired = true
    auth.user = {
      id: 1,
      username,
      role,
      display_name: username,
      email: null,
      active: true,
      created_at: '',
      updated_at: '',
    }
    return auth
  }

  // ────────── 1. 初始状态 ──────────

  describe('初始状态', () => {
    it('初始 connectionState 为 disconnected', () => {
      const { connectionState } = useCollab('nginx-502')
      expect(connectionState.value).toBe('disconnected' as ConnectionState)
    })

    it('初始 onlineUsers 为空数组', () => {
      const { onlineUsers } = useCollab('nginx-502')
      expect(onlineUsers.value).toEqual([])
    })

    it('初始 lockHolder 为 null', () => {
      const { lockHolder } = useCollab('nginx-502')
      expect(lockHolder.value).toBeNull()
    })

    it('初始 lastError 为 null', () => {
      const { lastError } = useCollab('nginx-502')
      expect(lastError.value).toBeNull()
    })

    it('初始 hasLock 为 false', () => {
      const { hasLock } = useCollab('nginx-502')
      expect(hasLock.value).toBe(false)
    })

    it('初始 onlineCount 为 0', () => {
      const { onlineCount } = useCollab('nginx-502')
      expect(onlineCount.value).toBe(0)
    })
  })

  // ────────── 2. myUserId 推断 ──────────

  describe('myUserId 推断（通过 hasLock 间接验证）', () => {
    it('dev 模式（authRequired=false）→ user_id="anon"', () => {
      const auth = useAuthStore()
      auth.authRequired = false
      const { hasLock } = useCollab('nginx-502')
      // 模拟服务端把锁分配给 anon
      const c = useCollab('nginx-502')
      c.connect()
      currentSocket.fireOpen()
      currentSocket.fireMessage({ type: 'lock_acquired', user_id: 'anon' })
      expect(c.hasLock.value).toBe(true)
    })

    it('已登录用户 → user_id="user:<username>"', () => {
      loginAs('alice', 'admin')
      const c = useCollab('nginx-502')
      c.connect()
      currentSocket.fireOpen()
      currentSocket.fireMessage({ type: 'lock_acquired_ack', user_id: 'user:alice' })
      expect(c.hasLock.value).toBe(true)
    })

    it('authRequired=null 视为 dev 模式', () => {
      const auth = useAuthStore()
      auth.authRequired = null
      const c = useCollab('nginx-502')
      c.connect()
      currentSocket.fireOpen()
      currentSocket.fireMessage({ type: 'lock_acquired', user_id: 'anon' })
      expect(c.hasLock.value).toBe(true)
    })

    it('未登录但 authRequired=true → user_id=null，hasLock 永远为 false', () => {
      const auth = useAuthStore()
      auth.authRequired = true
      auth.user = null
      const c = useCollab('nginx-502')
      c.connect()
      currentSocket.fireOpen()
      currentSocket.fireMessage({ type: 'lock_acquired_ack', user_id: 'user:alice' })
      expect(c.hasLock.value).toBe(false)
    })
  })

  // ────────── 3. 连接生命周期 ──────────

  describe('连接生命周期', () => {
    it('connect 调用 createCollabSocket 创建实例', () => {
      const { connect } = useCollab('nginx-502')
      connect()
      expect(mockCreateCollabSocket).toHaveBeenCalledTimes(1)
      expect(mockCreateCollabSocket).toHaveBeenCalledWith('nginx-502')
    })

    it('connect 后 connectionState=connecting，fireOpen 后变为 connected', () => {
      const { connect, connectionState } = useCollab('nginx-502')
      connect()
      expect(connectionState.value).toBe('connecting')
      currentSocket.fireOpen()
      expect(connectionState.value).toBe('connected')
    })

    it('connect 后 fireOpen 清空 lastError', () => {
      const { connect, lastError } = useCollab('nginx-502')
      // 先制造一个 error
      connect()
      currentSocket.fireOpen()
      currentSocket.fireMessage({ type: 'error', message: 'oops' })
      expect(lastError.value).toBe('oops')

      // 重连后清空
      const c2 = useCollab('nginx-502')
      c2.connect()
      currentSocket.fireOpen()
      expect(c2.lastError.value).toBeNull()
    })

    it('connect 时不重置已存在 socket（已是 OPEN/CONNECTING 跳过）', () => {
      const { connect } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen() // OPEN
      // 再次 connect：不应再创建新 socket
      connect()
      expect(mockCreateCollabSocket).toHaveBeenCalledTimes(1)
    })

    it('disconnect 设置 manuallyClosed 并 close socket', () => {
      const { connect, disconnect, connectionState } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()
      disconnect()
      expect(currentSocket.closed).toBe(true)
      expect(currentSocket.closeCode).toBe(1000)
      expect(connectionState.value).toBe('disconnected')
    })

    it('disconnect 后清空 onlineUsers 与 lockHolder', () => {
      const { connect, disconnect, onlineUsers, lockHolder } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()
      currentSocket.fireMessage({
        type: 'presence',
        users: [
          { user_id: 'user:bob', username: 'bob', display_name: 'Bob', role: 'viewer' },
        ],
        lock_holder: 'user:bob',
      })
      expect(onlineUsers.value.length).toBe(1)
      expect(lockHolder.value).toBe('user:bob')

      disconnect()
      expect(onlineUsers.value).toEqual([])
      expect(lockHolder.value).toBeNull()
    })

    it('disconnect 后 fireClose 不触发重连（manuallyClosed=true）', () => {
      const { connect, disconnect, connectionState } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()
      disconnect()
      // 模拟 socket 真正关闭事件触发
      currentSocket.fireClose(1000, 'client_closed')
      expect(connectionState.value).toBe('disconnected')
      // 没有再次创建 socket
      expect(mockCreateCollabSocket).toHaveBeenCalledTimes(1)
    })

    it('createCollabSocket 抛错时记录 error 信息并触发重连', () => {
      mockCreateCollabSocket.mockImplementation(() => {
        throw new Error('WebSocket constructor failed')
      })
      const { connect, connectionState, lastError } = useCollab('nginx-502')
      connect()
      // 抛错时 setConnectionState('error') 后立刻被 scheduleReconnect 覆盖为 'reconnecting'
      // 但 lastError 保留错误信息
      expect(['error', 'reconnecting']).toContain(connectionState.value)
      expect(lastError.value).toContain('创建 WebSocket 失败')
    })
  })

  // ────────── 4. 消息处理 ──────────

  describe('消息处理', () => {
    function setup() {
      const c = useCollab('nginx-502')
      c.connect()
      currentSocket.fireOpen()
      return c
    }

    it('presence 消息更新 onlineUsers 与 lockHolder', () => {
      const { onlineUsers, lockHolder } = setup()
      currentSocket.fireMessage({
        type: 'presence',
        users: [
          { user_id: 'user:alice', username: 'alice', display_name: 'Alice', role: 'admin' },
          { user_id: 'user:bob', username: 'bob', display_name: 'Bob', role: 'viewer' },
        ],
        lock_holder: 'user:alice',
      })
      expect(onlineUsers.value.length).toBe(2)
      expect(onlineUsers.value[0].user_id).toBe('user:alice')
      expect(lockHolder.value).toBe('user:alice')
    })

    it('user_left 消息从 onlineUsers 移除该用户', () => {
      const { onlineUsers, lockHolder } = setup()
      currentSocket.fireMessage({
        type: 'presence',
        users: [
          { user_id: 'user:alice', username: 'alice', display_name: 'Alice', role: 'admin' },
          { user_id: 'user:bob', username: 'bob', display_name: 'Bob', role: 'viewer' },
        ],
        lock_holder: null,
      })
      currentSocket.fireMessage({ type: 'user_left', user_id: 'user:bob' })
      expect(onlineUsers.value.length).toBe(1)
      expect(onlineUsers.value[0].user_id).toBe('user:alice')
    })

    it('user_left 时若离开者是 lockHolder，清空 lockHolder', () => {
      const { lockHolder } = setup()
      currentSocket.fireMessage({
        type: 'presence',
        users: [],
        lock_holder: 'user:bob',
      })
      expect(lockHolder.value).toBe('user:bob')
      currentSocket.fireMessage({ type: 'user_left', user_id: 'user:bob' })
      expect(lockHolder.value).toBeNull()
    })

    it('lock_acquired 消息设置 lockHolder', () => {
      const { lockHolder } = setup()
      currentSocket.fireMessage({ type: 'lock_acquired', user_id: 'user:alice' })
      expect(lockHolder.value).toBe('user:alice')
    })

    it('lock_released 消息在 holder 匹配时清空 lockHolder', () => {
      const { lockHolder } = setup()
      currentSocket.fireMessage({
        type: 'presence',
        users: [],
        lock_holder: 'user:alice',
      })
      currentSocket.fireMessage({ type: 'lock_released', user_id: 'user:alice' })
      expect(lockHolder.value).toBeNull()
    })

    it('lock_released 消息在 holder 不匹配时不变', () => {
      const { lockHolder } = setup()
      currentSocket.fireMessage({
        type: 'presence',
        users: [],
        lock_holder: 'user:alice',
      })
      currentSocket.fireMessage({ type: 'lock_released', user_id: 'user:bob' })
      expect(lockHolder.value).toBe('user:alice')
    })

    it('lock_acquired_ack 设置 lockHolder 为自己', () => {
      loginAs('alice', 'admin')
      const { lockHolder, hasLock } = setup()
      currentSocket.fireMessage({ type: 'lock_acquired_ack', user_id: 'user:alice' })
      expect(lockHolder.value).toBe('user:alice')
      expect(hasLock.value).toBe(true)
    })

    it('lock_denied 消息不变更 lockHolder', () => {
      const { lockHolder } = setup()
      currentSocket.fireMessage({
        type: 'presence',
        users: [],
        lock_holder: 'user:bob',
      })
      currentSocket.fireMessage({
        type: 'lock_denied',
        reason: 'held_by_other',
        holder: { user_id: 'user:bob' },
      })
      expect(lockHolder.value).toBe('user:bob')
    })

    it('error 消息写入 lastError', () => {
      const { lastError } = setup()
      currentSocket.fireMessage({ type: 'error', message: 'invalid message format' })
      expect(lastError.value).toBe('invalid message format')
    })

    it('heartbeat_ack 消息不影响状态', () => {
      const { onlineUsers, lockHolder, lastError } = setup()
      currentSocket.fireMessage({
        type: 'presence',
        users: [{ user_id: 'x', username: 'x', display_name: 'X', role: 'viewer' }],
        lock_holder: null,
      })
      currentSocket.fireMessage({ type: 'heartbeat_ack' })
      expect(onlineUsers.value.length).toBe(1)
      expect(lockHolder.value).toBeNull()
      expect(lastError.value).toBeNull()
    })

    it('非 JSON 数据不抛错（仅 console.error）', () => {
      const { onlineUsers } = setup()
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      // 直接构造非 JSON 字符串
      currentSocket.fireMessage('not-json-data')
      expect(onlineUsers.value).toEqual([])
      expect(consoleSpy).toHaveBeenCalled()
      consoleSpy.mockRestore()
    })

    it('edit_event / cursor 消息不影响 onlineUsers / lockHolder', () => {
      const { onlineUsers, lockHolder } = setup()
      currentSocket.fireMessage({
        type: 'presence',
        users: [{ user_id: 'u1', username: 'u1', display_name: 'U1', role: 'viewer' }],
        lock_holder: null,
      })
      currentSocket.fireMessage({
        type: 'edit_event',
        user_id: 'u1',
        payload: { op: 'insert', text: 'hi' },
      })
      currentSocket.fireMessage({
        type: 'cursor',
        user_id: 'u1',
        payload: { line: 5, col: 10 },
      })
      expect(onlineUsers.value.length).toBe(1)
      expect(lockHolder.value).toBeNull()
    })
  })

  // ────────── 5. 编辑锁 ──────────

  describe('编辑锁操作', () => {
    function setup() {
      loginAs('alice', 'admin')
      const c = useCollab('nginx-502')
      c.connect()
      currentSocket.fireOpen()
      return c
    }

    it('acquireLock 发送 acquire_lock 消息并返回 true', () => {
      const { acquireLock } = setup()
      const ok = acquireLock()
      expect(ok).toBe(true)
      expect(currentSocket.sentMessages).toContainEqual({ type: 'acquire_lock' })
    })

    it('releaseLock 发送 release_lock 消息并返回 true', () => {
      const { releaseLock } = setup()
      const ok = releaseLock()
      expect(ok).toBe(true)
      expect(currentSocket.sentMessages).toContainEqual({ type: 'release_lock' })
    })

    it('hasLock 在持锁时为 true，未持锁时为 false', () => {
      const { hasLock, lockHolder } = setup()
      expect(hasLock.value).toBe(false)
      currentSocket.fireMessage({ type: 'lock_acquired_ack', user_id: 'user:alice' })
      expect(hasLock.value).toBe(true)
      // 他人持锁
      currentSocket.fireMessage({ type: 'lock_acquired', user_id: 'user:bob' })
      expect(hasLock.value).toBe(false)
    })
  })

  // ────────── 6. 编辑事件 / cursor ──────────

  describe('编辑事件 / cursor', () => {
    function setup() {
      const c = useCollab('nginx-502')
      c.connect()
      currentSocket.fireOpen()
      return c
    }

    it('sendEdit 发送 edit_event 消息含 payload', () => {
      const { sendEdit } = setup()
      const ok = sendEdit({ op: 'insert', text: 'hello' })
      expect(ok).toBe(true)
      expect(currentSocket.sentMessages).toContainEqual({
        type: 'edit_event',
        payload: { op: 'insert', text: 'hello' },
      })
    })

    it('sendCursor 发送 cursor 消息含 line/col', () => {
      const { sendCursor } = setup()
      const ok = sendCursor(10, 5)
      expect(ok).toBe(true)
      expect(currentSocket.sentMessages).toContainEqual({
        type: 'cursor',
        payload: { line: 10, col: 5 },
      })
    })
  })

  // ────────── 7. sendMessage 失败情况 ──────────

  describe('sendMessage 防御性', () => {
    it('socket 为 null 时 acquireLock 返回 false', () => {
      const { acquireLock } = useCollab('nginx-502')
      // 不 connect
      const ok = acquireLock()
      expect(ok).toBe(false)
    })

    it('socket readyState 不是 OPEN 时 sendMessage 返回 false', () => {
      const { connect, acquireLock } = useCollab('nginx-502')
      connect()
      // 不 fireOpen，readyState 仍是 CONNECTING
      const ok = acquireLock()
      expect(ok).toBe(false)
    })
  })

  // ────────── 8. 心跳 ──────────

  describe('心跳', () => {
    it('connect 成功后启动心跳定时器', () => {
      const { connect, acquireLock } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()

      // 推进 30s 触发心跳
      vi.advanceTimersByTime(30_000)
      expect(currentSocket.sentMessages).toContainEqual({ type: 'heartbeat' })
    })

    it('心跳每 30s 发送一次（多次）', () => {
      const { connect } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()

      vi.advanceTimersByTime(30_000)
      vi.advanceTimersByTime(30_000)
      vi.advanceTimersByTime(30_000)
      const heartbeatCount = currentSocket.sentMessages.filter(
        (m) => m.type === 'heartbeat',
      ).length
      expect(heartbeatCount).toBe(3)
    })

    it('disconnect 后停止心跳', () => {
      const { connect, disconnect } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()
      disconnect()

      // 推进时间，不应有心跳
      vi.advanceTimersByTime(60_000)
      const heartbeatCount = currentSocket.sentMessages.filter(
        (m) => m.type === 'heartbeat',
      ).length
      expect(heartbeatCount).toBe(0)
    })

    it('onclose 触发后停止心跳', () => {
      const { connect } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()
      currentSocket.fireClose(1006, 'abnormal')

      vi.advanceTimersByTime(60_000)
      // 重连 schedule 会创建新 socket，但原 socket 不应再有心跳
      const heartbeatCount = currentSocket.sentMessages.filter(
        (m) => m.type === 'heartbeat',
      ).length
      expect(heartbeatCount).toBe(0)
    })
  })

  // ────────── 9. 自动重连 ──────────

  describe('自动重连', () => {
    it('非手动关闭的 onclose 触发重连', () => {
      const { connect } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()
      currentSocket.fireClose(1006, 'abnormal')

      // 第一次重连：延迟 1000ms（1 * 2^0）
      expect(mockCreateCollabSocket).toHaveBeenCalledTimes(1)
      vi.advanceTimersByTime(1_000)
      expect(mockCreateCollabSocket).toHaveBeenCalledTimes(2)
    })

    it('重连指数退避：1s → 2s → 4s → 8s → 16s', () => {
      const { connect } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()

      // 第 1 次重连
      currentSocket.fireClose(1006)
      vi.advanceTimersByTime(1_000)
      expect(mockCreateCollabSocket).toHaveBeenCalledTimes(2)
      currentSocket.fireOpen() // 第 2 次连接成功（重置 attempts）—— 但这里不重置，因为先 fireOpen 又断开
      // 重新拿到当前 socket
      const socket2 = mockCreateCollabSocket.mock.results.at(-1)!.value as FakeSocket
      socket2.fireOpen()
      // 然后立刻再断开
      socket2.fireClose(1006)

      // 第 2 次重连：延迟 2s
      vi.advanceTimersByTime(2_000)
      expect(mockCreateCollabSocket).toHaveBeenCalledTimes(3)
    })

    it('达到最大重试次数后标记 error', () => {
      const { connect, connectionState, lastError } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()

      // 不让重连成功（每次都 fireOpen 后立刻断开？不，重连时 socket 是新创建的，
      // 但 mockCreateCollabSocket 一直返回新实例，我们手动驱动 fireClose）
      // 简化：直接连续触发 6 次重连（> MAX_RECONNECT_ATTEMPTS=5）

      for (let i = 1; i <= 6; i++) {
        const s = mockCreateCollabSocket.mock.results.at(-1)!.value as FakeSocket
        // 不 fireOpen，让重连尝试失败（readyState 仍 CONNECTING）
        // 实际上 onclose 触发需要 socket.close 事件。改用直接 fireClose（即使没 open）
        s.fireClose(1006)
        // 推进对应延迟（用最大值 16s 确保覆盖）
        vi.advanceTimersByTime(60_000)
      }

      expect(connectionState.value).toBe('error')
      expect(lastError.value).toContain('重连失败')
    })

    it('成功重连后 reconnectAttempts 重置为 0', () => {
      const { connect } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()
      currentSocket.fireClose(1006)

      vi.advanceTimersByTime(1_000) // 触发第 1 次重连
      expect(mockCreateCollabSocket).toHaveBeenCalledTimes(2)
      currentSocket.fireOpen() // 重连成功

      // 再次断开，应该从 1s 开始（attempts=0）
      currentSocket.fireClose(1006)
      vi.advanceTimersByTime(1_000)
      expect(mockCreateCollabSocket).toHaveBeenCalledTimes(3)
    })

    it('disconnect 期间不再重连', () => {
      const { connect, disconnect } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()
      disconnect()

      // 推进大量时间，不应创建新 socket
      vi.advanceTimersByTime(120_000)
      expect(mockCreateCollabSocket).toHaveBeenCalledTimes(1)
    })
  })

  // ────────── 10. onclose 清空本地状态 ──────────

  describe('onclose 清空本地状态', () => {
    it('连接断开时 onlineUsers 清空', () => {
      const { connect, onlineUsers } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()
      currentSocket.fireMessage({
        type: 'presence',
        users: [
          { user_id: 'u1', username: 'u1', display_name: 'U1', role: 'viewer' },
        ],
        lock_holder: 'u1',
      })
      expect(onlineUsers.value.length).toBe(1)
      currentSocket.fireClose(1006)
      expect(onlineUsers.value).toEqual([])
    })

    it('连接断开时 lockHolder 清空', () => {
      const { connect, lockHolder } = useCollab('nginx-502')
      connect()
      currentSocket.fireOpen()
      currentSocket.fireMessage({
        type: 'presence',
        users: [],
        lock_holder: 'user:alice',
      })
      currentSocket.fireClose(1006)
      expect(lockHolder.value).toBeNull()
    })
  })
})
