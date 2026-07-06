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

export function useCollab(slug: string) {
  const authStore = useAuthStore()

  // ────────── 响应式状态 ──────────
  const onlineUsers = ref<CollabUser[]>([])
  const lockHolder = ref<string | null>(null)
  const connectionState = ref<ConnectionState>('disconnected')
  const lastError = ref<string | null>(null)

  // ────────── 内部状态（非响应式） ──────────
  let socket: WebSocket | null = null
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let reconnectAttempts = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let manuallyClosed = false

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
        break
      case 'user_left':
        onlineUsers.value = onlineUsers.value.filter((u) => u.user_id !== msg.user_id)
        if (lockHolder.value === msg.user_id) {
          lockHolder.value = null
        }
        break
      case 'lock_acquired':
        lockHolder.value = msg.user_id
        break
      case 'lock_released':
        if (lockHolder.value === msg.user_id) {
          lockHolder.value = null
        }
        break
      case 'lock_acquired_ack':
        // 自己请求锁成功
        lockHolder.value = msg.user_id
        break
      case 'lock_denied':
        // 自己请求锁失败，lockHolder 不变（仍是他人）
        break
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
      reconnectAttempts = 0
      setConnectionState('connected')
      startHeartbeat()
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
    setConnectionState('disconnected')
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
    // 计算属性
    hasLock,
    onlineCount,
    // 连接管理
    connect,
    disconnect,
    // 编辑锁
    acquireLock,
    releaseLock,
    // 编辑事件
    sendEdit,
    sendCursor,
  }
}
