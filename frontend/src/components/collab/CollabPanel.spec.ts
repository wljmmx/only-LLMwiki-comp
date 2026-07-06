/**
 * CollabPanel 组件测试（S16-1 WikiView 协作集成）
 *
 * 覆盖：
 * 1. 渲染：标题 / 连接状态标签 / 在线用户列表 / 编辑锁区域
 * 2. 连接生命周期：onMounted connect / onUnmounted disconnect
 * 3. 锁状态：无人编辑 / 他人编辑 / 自己持锁
 * 4. 按钮：canAcquire / canRelease / 点击事件
 * 5. 在线用户列表渲染（含持锁者高亮）
 * 6. 错误提示
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// ────────── mock @/api/realtime 的 createCollabSocket ──────────
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

import CollabPanel from './CollabPanel.vue'
import { useAuthStore } from '@/stores/auth'
import '@/test/setup'

// ────────── Fake WebSocket ──────────
const WS_OPEN = 1
const WS_CONNECTING = 0

class FakeSocket {
  readyState: number = WS_CONNECTING
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  sentMessages: any[] = []
  closed = false
  closeCode?: number

  send(data: string) {
    this.sentMessages.push(JSON.parse(data))
  }
  close(code: number = 1000) {
    this.closed = true
    this.closeCode = code
    this.readyState = 3
  }
  fireOpen() {
    this.readyState = WS_OPEN
    if (this.onopen) this.onopen(new Event('open'))
  }
  fireMessage(data: any) {
    if (this.onmessage) {
      this.onmessage({ data: typeof data === 'string' ? data : JSON.stringify(data) } as MessageEvent)
    }
  }
  fireClose(code: number = 1000, reason: string = '') {
    this.readyState = 3
    if (this.onclose) this.onclose({ code, reason } as CloseEvent)
  }
}
;(FakeSocket as any).OPEN = WS_OPEN
;(FakeSocket as any).CONNECTING = WS_CONNECTING

const originalWebSocket = global.WebSocket

describe('components/collab/CollabPanel.vue — S16-1 协作面板', () => {
  let currentSocket: FakeSocket
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    currentSocket = new FakeSocket()
    mockCreateCollabSocket.mockReturnValue(currentSocket)
    ;(global as any).WebSocket = FakeSocket
  })

  afterEach(() => {
    vi.useRealTimers()
    ;(global as any).WebSocket = originalWebSocket
  })

  function mountPanel(slug: string = 'nginx-502') {
    // 复用 active pinia，确保 loginAs 设置的 user 状态可见
    return mount(CollabPanel, {
      props: { slug },
      global: { plugins: [pinia] },
    })
  }

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

  // ────────── 1. 渲染 ──────────

  describe('渲染', () => {
    it('渲染标题"协作"与连接状态标签', async () => {
      const wrapper = mountPanel()
      // onMounted 调用 connect 后状态变为 connecting，需 await DOM 更新
      await flushPromises()
      expect(wrapper.text()).toContain('协作')
      expect(wrapper.text()).toContain('连接中')
    })

    it('连接成功后状态标签变为"已连接"', async () => {
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      expect(wrapper.text()).toContain('已连接')
    })

    it('渲染"在线用户"区域', () => {
      const wrapper = mountPanel()
      expect(wrapper.text()).toContain('在线用户')
    })

    it('渲染"编辑锁"区域', () => {
      const wrapper = mountPanel()
      expect(wrapper.text()).toContain('编辑锁')
    })
  })

  // ────────── 2. 连接生命周期 ──────────

  describe('连接生命周期', () => {
    it('onMounted 调用 connect', () => {
      mountPanel()
      expect(mockCreateCollabSocket).toHaveBeenCalledTimes(1)
      expect(mockCreateCollabSocket).toHaveBeenCalledWith('nginx-502')
    })

    it('onUnmounted 调用 disconnect 关闭 socket', async () => {
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      wrapper.unmount()
      expect(currentSocket.closed).toBe(true)
    })
  })

  // ────────── 3. 在线用户列表 ──────────

  describe('在线用户列表', () => {
    it('无在线用户时显示"暂无其他用户"', async () => {
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      expect(wrapper.text()).toContain('暂无其他用户')
    })

    it('有在线用户时渲染用户名', async () => {
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      currentSocket.fireMessage({
        type: 'presence',
        users: [
          {
            user_id: 'user:alice',
            username: 'alice',
            display_name: 'Alice',
            role: 'admin',
          },
        ],
        lock_holder: null,
      })
      await flushPromises()
      expect(wrapper.text()).toContain('Alice')
      expect(wrapper.text()).toContain('admin')
    })

    it('在线用户数为 0 时显示 (0)', async () => {
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      expect(wrapper.text()).toContain('(0)')
    })

    it('在线用户数为 2 时显示 (2)', async () => {
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      currentSocket.fireMessage({
        type: 'presence',
        users: [
          { user_id: 'u1', username: 'u1', display_name: 'U1', role: 'admin' },
          { user_id: 'u2', username: 'u2', display_name: 'U2', role: 'viewer' },
        ],
        lock_holder: null,
      })
      await flushPromises()
      expect(wrapper.text()).toContain('(2)')
    })
  })

  // ────────── 4. 锁状态 ──────────

  describe('锁状态', () => {
    it('无人编辑时显示"无人编辑，可申请锁" + 申请按钮', async () => {
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      expect(wrapper.text()).toContain('无人编辑')
      expect(wrapper.text()).toContain('申请编辑锁')
    })

    it('他人编辑时显示持有者名称', async () => {
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      currentSocket.fireMessage({
        type: 'presence',
        users: [
          {
            user_id: 'user:bob',
            username: 'bob',
            display_name: 'Bob',
            role: 'operator',
          },
        ],
        lock_holder: 'user:bob',
      })
      await flushPromises()
      expect(wrapper.text()).toContain('Bob')
      expect(wrapper.text()).toContain('正在编辑')
    })

    it('自己持锁时显示"你正在编辑此页面" + 释放按钮', async () => {
      loginAs('alice', 'admin')
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      currentSocket.fireMessage({ type: 'lock_acquired_ack', user_id: 'user:alice' })
      await flushPromises()
      expect(wrapper.text()).toContain('你正在编辑此页面')
      expect(wrapper.text()).toContain('释放锁')
    })

    it('点击"申请编辑锁"发送 acquire_lock 消息', async () => {
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      const btn = wrapper.find('button')
      expect(btn.exists()).toBe(true)
      await btn.trigger('click')
      expect(currentSocket.sentMessages).toContainEqual({ type: 'acquire_lock' })
    })

    it('持锁后点击"释放锁"发送 release_lock 消息', async () => {
      loginAs('alice', 'admin')
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      currentSocket.fireMessage({ type: 'lock_acquired_ack', user_id: 'user:alice' })
      await flushPromises()
      // 找到"释放锁"按钮
      const buttons = wrapper.findAll('button')
      const releaseBtn = buttons.find((b) => b.text().includes('释放锁'))
      expect(releaseBtn).toBeDefined()
      await releaseBtn!.trigger('click')
      expect(currentSocket.sentMessages).toContainEqual({ type: 'release_lock' })
    })

    it('他人持锁时申请按钮 disabled', async () => {
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      currentSocket.fireMessage({
        type: 'presence',
        users: [
          { user_id: 'user:bob', username: 'bob', display_name: 'Bob', role: 'viewer' },
        ],
        lock_holder: 'user:bob',
      })
      await flushPromises()
      // 锁区域不显示申请按钮（显示"Bob 正在编辑"）
      expect(wrapper.text()).not.toContain('申请编辑锁')
    })
  })

  // ────────── 5. 错误提示 ──────────

  describe('错误提示', () => {
    it('服务端 error 消息渲染到面板', async () => {
      const wrapper = mountPanel()
      currentSocket.fireOpen()
      await flushPromises()
      currentSocket.fireMessage({ type: 'error', message: '房间已满' })
      await flushPromises()
      expect(wrapper.text()).toContain('房间已满')
    })
  })

  // ────────── 6. slug prop 传递 ──────────

  describe('slug prop', () => {
    it('不同 slug 调用 createCollabSocket 时传递对应 slug', () => {
      mountPanel('reverse-proxy')
      expect(mockCreateCollabSocket).toHaveBeenCalledWith('reverse-proxy')
    })
  })
})
