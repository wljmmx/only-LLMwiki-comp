import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { permission } from './permission'
import { useAuthStore } from '@/stores/auth'

// mock api/auth 模块
vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  logout: vi.fn(),
  getMe: vi.fn(),
  getRoles: vi.fn(),
  listUsers: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
  deleteUser: vi.fn(),
  getOIDCStatus: vi.fn(),
  listOIDCProviders: vi.fn(),
  redirectToOIDC: vi.fn(),
  checkAuthRequired: vi.fn(),
}))

/** 创建测试用组件，带 v-permission 指令 */
function createTestComponent(requiredRole: string | string[]) {
  // 注意：v-permission="..." 内部是 JS 表达式，字符串需用单引号包裹，
  // 数组字面量也用单引号包裹元素，避免与模板属性的双引号定界符冲突。
  const roleExpr =
    typeof requiredRole === 'string'
      ? `'${requiredRole}'`
      : `[${requiredRole.map((r) => `'${r}'`).join(', ')}]`
  return defineComponent({
    directives: { permission },
    template: `
      <div class="test-container">
        <button class="restricted-btn" v-permission="${roleExpr}">受限按钮</button>
        <div class="always-visible">始终可见</div>
      </div>
    `,
  })
}

describe('directives/permission.ts', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('v-permission 单角色', () => {
    it('admin 用户可以看到 v-permission="admin" 元素', () => {
      const auth = useAuthStore()
      auth.user = {
        id: 1, username: 'admin', role: 'admin',
        display_name: 'A', email: null, active: true,
        created_at: '', updated_at: '',
      }
      auth.authRequired = true

      const TestComp = createTestComponent('admin')
      const wrapper = mount(TestComp)
      expect(wrapper.find('.restricted-btn').exists()).toBe(true)
    })

    it('viewer 用户看不到 v-permission="admin" 元素', () => {
      const auth = useAuthStore()
      auth.user = {
        id: 2, username: 'viewer', role: 'viewer',
        display_name: 'V', email: null, active: true,
        created_at: '', updated_at: '',
      }
      auth.authRequired = true

      const TestComp = createTestComponent('admin')
      const wrapper = mount(TestComp)
      expect(wrapper.find('.restricted-btn').exists()).toBe(false)
    })

    it('operator 用户看不到 v-permission="admin" 元素', () => {
      const auth = useAuthStore()
      auth.user = {
        id: 3, username: 'op', role: 'operator',
        display_name: 'O', email: null, active: true,
        created_at: '', updated_at: '',
      }
      auth.authRequired = true

      const TestComp = createTestComponent('admin')
      const wrapper = mount(TestComp)
      expect(wrapper.find('.restricted-btn').exists()).toBe(false)
    })
  })

  describe('v-permission 多角色', () => {
    it('admin 用户可以看到 v-permission="[admin, operator]" 元素', () => {
      const auth = useAuthStore()
      auth.user = {
        id: 1, username: 'admin', role: 'admin',
        display_name: 'A', email: null, active: true,
        created_at: '', updated_at: '',
      }
      auth.authRequired = true

      const TestComp = createTestComponent(['admin', 'operator'])
      const wrapper = mount(TestComp)
      expect(wrapper.find('.restricted-btn').exists()).toBe(true)
    })

    it('operator 用户可以看到 v-permission="[admin, operator]" 元素', () => {
      const auth = useAuthStore()
      auth.user = {
        id: 3, username: 'op', role: 'operator',
        display_name: 'O', email: null, active: true,
        created_at: '', updated_at: '',
      }
      auth.authRequired = true

      const TestComp = createTestComponent(['admin', 'operator'])
      const wrapper = mount(TestComp)
      expect(wrapper.find('.restricted-btn').exists()).toBe(true)
    })

    it('viewer 用户看不到 v-permission="[admin, operator]" 元素', () => {
      const auth = useAuthStore()
      auth.user = {
        id: 2, username: 'v', role: 'viewer',
        display_name: 'V', email: null, active: true,
        created_at: '', updated_at: '',
      }
      auth.authRequired = true

      const TestComp = createTestComponent(['admin', 'operator'])
      const wrapper = mount(TestComp)
      expect(wrapper.find('.restricted-btn').exists()).toBe(false)
    })
  })

  describe('dev 模式', () => {
    it('authRequired === false 时所有角色都能看到', () => {
      const auth = useAuthStore()
      auth.authRequired = false

      const TestComp = createTestComponent('admin')
      const wrapper = mount(TestComp)
      expect(wrapper.find('.restricted-btn').exists()).toBe(true)
    })

    it('authRequired === null（后端不可达）时也不拦截', () => {
      const auth = useAuthStore()
      auth.authRequired = null

      const TestComp = createTestComponent('admin')
      const wrapper = mount(TestComp)
      expect(wrapper.find('.restricted-btn').exists()).toBe(true)
    })
  })

  describe('未登录', () => {
    it('未登录且 authRequired === true 时移除元素', () => {
      const auth = useAuthStore()
      auth.authRequired = true

      const TestComp = createTestComponent('admin')
      const wrapper = mount(TestComp)
      expect(wrapper.find('.restricted-btn').exists()).toBe(false)
    })
  })

  describe('不影响其他元素', () => {
    it('受限元素被移除时其他元素仍可见', () => {
      const auth = useAuthStore()
      auth.user = {
        id: 2, username: 'viewer', role: 'viewer',
        display_name: 'V', email: null, active: true,
        created_at: '', updated_at: '',
      }
      auth.authRequired = true

      const TestComp = createTestComponent('admin')
      const wrapper = mount(TestComp)
      expect(wrapper.find('.restricted-btn').exists()).toBe(false)
      expect(wrapper.find('.always-visible').exists()).toBe(true)
    })
  })
})
