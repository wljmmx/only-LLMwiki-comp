/**
 * 协作 composable（S15-5 实时协作编辑）
 *
 * 用法：
 *   const {
 *     onlineUsers, lockHolder, hasLock, connectionState,
 *     connect, disconnect, acquireLock, releaseLock, sendEdit, sendCursor,
 *   } = useCollab('nginx-502-troubleshooting')
 *
 *   onMounted(() => connect())
 *   onUnmounted(() => disconnect())
 *
 * 功能：
 * - 自动管理 WebSocket 生命周期（连接 / 重连 / 心跳）
 * - 响应式在线用户列表（onlineUsers）与编辑锁持有者（lockHolder）
 * - 心跳：每 30s 发送一次 heartbeat，避免被服务端清理
 * - 自动重连：连接断开时按指数退避重连（最多 5 次）
 * - 编辑锁：acquireLock / releaseLock，hasLock 表示当前用户是否持有锁
 */
import { ref, computed, readonly } from 'vue'
import {
  createCollabSocket,
  type CollabUser,
  type CollabEvent,
  type ServerMessage,
  type ClientMessage,
} from '@/api/realtime'
import { useAuthStore } from '@/stores/auth'

/** 连接状态 */
export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'error'

/** 心跳间隔（毫秒）— 必须小于后端 HEARTBEAT_TIMEOUT (60s) */
const HEARTBEAT_INTERVAL_MS = 30_000
/** 自动重连最大次数 */
const MAX_RECONNECT_ATTEMPTS = 5
/** 重连基础延迟（毫秒），实际延迟 = base * 2^attempt */
const RECONNECT_BASE_DELAY_MS = 1_000
/** 事件流容量上限（S16-3）— 超出后保留最新 N 条 */
const MAX_EVENTS = 50

export function useCollab(slug: string) {
  const authStore = useAuthStore()

  // ────────── 响应式状态 ──────────
  const onlineUsers = ref<CollabUser[]>([])
  const lockHolder = ref<string | null>(null)
  const connectionState = ref<ConnectionState>('disconnected')
  const lastError = ref<string | null>(null)
  // S16-3：事件流（按消息到达顺序追加，cap MAX_EVENTS，UI 渲染时倒序）
  const events = ref<CollabEvent[]>([])

  // ────────── 内部状态（非响应式） ──────────
  let socket: WebSocket | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let reconnectAttempts = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let manuallyClosed = false
  // S16-5：重连成功后回调列表（调用方注册，用于拉取最新版本 / 检测草稿冲突）
  const reconnectCallbacks: Array<() => void> = []

  // ────────── 计算属性 ──────────

  /** 当前用户是否持有编辑锁 */
  const hasLock = computed<boolean>(() => {
    const myId = myUserId()
    return myId !== null && lockHolder.value === myId
  })

  /** 在线用户数 */
  const onlineCount = computed(() => onlineUsers.value.length)

  // ────────── 工具函数 ──────────

  /**
   * 推断当前用户的 user_id
   *
   * 与后端 _resolve_user 保持一致：
   * - dev 模式（authRequired === false）→ "anon"
   * - legacy 共享 token → "legacy"
   * - session token → "user:<username>"
   */
  function myUserId(): string | null {
    if (authStore.authRequired === false || authStore.authRequired === null) {
      return 'anon'
    }
    const username = authStore.user?.username
    return username ? `user:${username}` : null
  }

  // ────────── 事件流辅助（S16-3） ──────────

  /**
   * 从 onlineUsers 反查 display_name，找不到时回退 user_id
   * 注意：user_left 时需在从 onlineUsers 移除之前调用
   */
  function lookupDisplayName(userId: string): string {
    const u = onlineUsers.value.find((x) => x.user_id === userId)
    return u?.display_name || u?.username || userId
  }

  /**
   * 追加一条事件到 events，超出 MAX_EVENTS 时保留最新 N 条
   * timestamp 优先取后端注入的 msg.timestamp（秒 × 1000），缺失时用 Date.now() 兜底
   */
  function appendEvent(
    type: CollabEvent['type'],
    userId: string,
    displayName: string,
    message: string,
    serverTimestamp?: number,
  ): void {
    const ts = typeof serverTimestamp === 'number' ? serverTimestamp * 1000 : Date.now()
    const ev: CollabEvent = { timestamp: ts, type, userId, displayName, message }
    // cap：保留最新 MAX_EVENTS 条（按到达顺序）
    const next = events.value.length >= MAX_EVENTS ? [...events.value.slice(1), ev] : [...events.value, ev]
    events.value = next
  }

  function setConnectionState(state: ConnectionState, error?: string) {
    connectionState.value = state
    if (error !== undefined) {
      lastError.value = error
    } else if (state === 'connected') {
      lastError.value = null
    }
  }

  function sendMessage(msg: ClientMessage): boolean {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return false
    }
    try {
      socket.send(JSON.stringify(msg))
      return true
    } catch (e) {
      console.error('[useCollab] sendMessage 失败:', e)
      return false
    }
  }

  function startHeartbeat() {
    stopHeartbeat()
    heartbeatTimer = setInterval(() => {
      sendMessage({ type: 'heartbeat' })
    }, HEARTBEAT_INTERVAL_MS)
  }

  function stopHeartbeat() {
    if (heartbeatTimer !== null) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  function scheduleReconnect() {
    if (manuallyClosed) return
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      setConnectionState('error', `重连失败：已达最大重试次数 ${MAX_RECONNECT_ATTEMPTS}`)
      return
    }
    const delay = RECONNECT_BASE_DELAY_MS * Math.pow(2, reconnectAttempts)
    reconnectAttempts++
    setConnectionState('reconnecting')
    reconnectTimer = setTimeout(() => {
      doConnect()
    }, delay)
  }

  function cancelReconnect() {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  // ────────── 消息处理 ──────────

  function handleMessage(event: MessageEvent) {
    let msg: ServerMessage
    try {
      msg = JSON.parse(event.data)
    } catch (e) {
      console.error('[useCollab] 解析消息失败:', e)
      return
    }

    switch (msg.type) {
      case 'presence':
        onlineUsers.value = msg.users
        lockHolder.value = msg.lock_holder
        break
      case 'user_joined':
        // presence 消息会同步完整列表，这里仅做日志
        // S16-3：追加事件流条目
        appendEvent(
          'user_joined',
          msg.user.user_id,
          msg.user.display_name || msg.user.username || msg.user.user_id,
          `${msg.user.display_name || msg.user.username || msg.user.user_id} 加入了协作`,
          msg.timestamp,
        )
        break
      case 'user_left': {
        // S16-3：先反查 display_name（移除前），再更新 onlineUsers
        const leftName = lookupDisplayName(msg.user_id)
        onlineUsers.value = onlineUsers.value.filter((u) => u.user_id !== msg.user_id)
        if (lockHolder.value === msg.user_id) {
          lockHolder.value = null
        }
        appendEvent(
          'user_left',
          msg.user_id,
          leftName,
          `${leftName} 离开了协作`,
          msg.timestamp,
        )
        break
      }
      case 'lock_acquired': {
        // S16-3：锁被获取（自己或他人）
        lockHolder.value = msg.user_id
        const acqName = lookupDisplayName(msg.user_id)
        appendEvent(
          'lock_acquired',
          msg.user_id,
          acqName,
          `${acqName} 申请了编辑锁`,
          msg.timestamp,
        )
        break
      }
      case 'lock_released': {
        // S16-3：锁被释放
        if (lockHolder.value === msg.user_id) {
          lockHolder.value = null
        }
        const relName = lookupDisplayName(msg.user_id)
        appendEvent(
          'lock_released',
          msg.user_id,
          relName,
          `${relName} 释放了编辑锁`,
          msg.timestamp,
        )
        break
      }
      case 'lock_acquired_ack':
        // 自己请求锁成功（lockHolder 已是自己的回执）
        lockHolder.value = msg.user_id
        break
      case 'lock_denied': {
        // S16-3：自己申请锁被拒，holder 是当前持锁者
        const holderName = msg.holder?.display_name || msg.holder?.username || '他人'
        const myId = myUserId() || 'me'
        appendEvent(
          'lock_denied',
          myId,
          myId,
          `你申请编辑锁被拒（${holderName} 正在编辑）`,
          msg.timestamp,
        )
        break
      }
      case 'heartbeat_ack':
        // 心跳回执，无需处理
        break
      case 'error':
        lastError.value = msg.message
        console.warn('[useCollab] 服务端错误:', msg.message)
        break
      case 'edit_event':
      case 'cursor':
        // 业务层可通过 watch / 事件订阅处理
        break
    }
  }

  // ────────── 连接管理 ──────────

  function doConnect() {
    if (manuallyClosed) return
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return
    }

    setConnectionState('connecting')
    try {
      socket = createCollabSocket(slug)
    } catch (e) {
      setConnectionState('error', `创建 WebSocket 失败: ${String(e)}`)
      scheduleReconnect()
      return
    }

    socket.onopen = () => {
      // S16-5：reconnectAttempts > 0 表示这是重连成功（首次连接为 0）
      const wasReconnect = reconnectAttempts > 0
      reconnectAttempts = 0
      setConnectionState('connected')
      startHeartbeat()
      // S16-5：重连成功后触发回调（调用方可在此拉取最新版本 / 检测草稿冲突）
      if (wasReconnect) {
        for (const cb of reconnectCallbacks) {
          try {
            cb()
          } catch (e) {
            console.error('[useCollab] onReconnect 回调执行失败:', e)
          }
        }
      }
    }

    socket.onmessage = handleMessage

    socket.onerror = (event) => {
      console.error('[useCollab] WebSocket error:', event)
      // onclose 会随后触发，重连逻辑放在 onclose
    }

    socket.onclose = (event) => {
      stopHeartbeat()
      socket = null
      // 清空本地状态（避免显示陈旧数据）
      onlineUsers.value = []
      lockHolder.value = null
      // S16-3：连接断开清空事件流（重连后会从新状态开始）
      events.value = []
      if (manuallyClosed) {
        setConnectionState('disconnected')
      } else {
        console.warn(`[useCollab] 连接断开 code=${event.code} reason=${event.reason}, 准备重连`)
        scheduleReconnect()
      }
    }
  }

  function connect() {
    manuallyClosed = false
    cancelReconnect()
    reconnectAttempts = 0
    doConnect()
  }

  function disconnect() {
    manuallyClosed = true
    cancelReconnect()
    stopHeartbeat()
    if (socket) {
      try {
        socket.close(1000, 'client_closed')
      } catch {
        // 忽略关闭错误
      }
      socket = null
    }
    onlineUsers.value = []
    lockHolder.value = null
    // S16-3：主动断开同样清空事件流
    events.value = []
    // S16-5：主动断开时清空重连回调（避免组件卸载后回调仍持有引用造成泄漏）
    reconnectCallbacks.length = 0
    setConnectionState('disconnected')
  }

  // ────────── 重连回调（S16-5） ──────────

  /**
   * 注册重连成功回调
   *
   * 当 WebSocket 断线后重连成功（非首次连接）时触发。
   * 调用方可在此：
   *   1. 拉取最新页面版本，比较本地草稿版本号检测冲突
   *   2. 重新申请编辑锁（锁状态在断线期间可能已被他人获取）
   *   3. 同步其他需要与服务器一致的状态
   *
   * 回调内的异常会被捕获并 console.error，不会中断其他回调或重连流程。
   *
   * @param cb 回调函数（无参，调用方可闭包捕获外部状态）
   * @returns 注销函数（调用后移除该回调）
   */
  function onReconnect(cb: () => void): () => void {
    reconnectCallbacks.push(cb)
    return () => {
      const idx = reconnectCallbacks.indexOf(cb)
      if (idx >= 0) {
        reconnectCallbacks.splice(idx, 1)
      }
    }
  }

  // ────────── 编辑锁操作 ──────────

  function acquireLock(): boolean {
    return sendMessage({ type: 'acquire_lock' })
  }

  function releaseLock(): boolean {
    return sendMessage({ type: 'release_lock' })
  }

  // ────────── 编辑事件 / cursor ──────────

  function sendEdit(payload: Record<string, unknown>): boolean {
    return sendMessage({ type: 'edit_event', payload })
  }

  function sendCursor(line: number, col: number): boolean {
    return sendMessage({ type: 'cursor', payload: { line, col } })
  }

  return {
    // 响应式状态（只读）
    onlineUsers: readonly(onlineUsers),
    lockHolder: readonly(lockHolder),
    connectionState: readonly(connectionState),
    lastError: readonly(lastError),
    // S16-3：事件流（只读）
    events: readonly(events),
    // 计算属性
    hasLock,
    onlineCount,
    // 连接管理
    connect,
    disconnect,
    // S16-5：重连回调注册
    onReconnect,
    // 编辑锁
    acquireLock,
    releaseLock,
    // 编辑事件
    sendEdit,
    sendCursor,
  }
}
