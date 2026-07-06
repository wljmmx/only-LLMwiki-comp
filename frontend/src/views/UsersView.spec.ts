import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { User } from '@/api/auth'

const mockMessage = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}
const mockDialog = {
  warning: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
}

// mock naive-ui 的 useMessage/useDialog（避免需要 Provider 包裹）
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useMessage: () => mockMessage,
    useDialog: () => mockDialog,
  }
})

// mock api/auth 模块
vi.mock('@/api/auth', () => ({
  listUsers: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
  deleteUser: vi.fn(),
  cleanupSessions: vi.fn(),
  getMe: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  getRoles: vi.fn(),
  getOIDCStatus: vi.fn(),
  listOIDCProviders: vi.fn(),
  redirectToOIDC: vi.fn(),
  checkAuthRequired: vi.fn(),
}))

import * as authApi from '@/api/auth'
import UsersView from '@/views/UsersView.vue'
import '@/test/setup'

const adminUser: User = {
  id: 1,
  username: 'admin',
  role: 'admin',
  display_name: 'Administrator',
  email: 'admin@example.com',
  active: true,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
}

const viewerUser: User = {
  id: 2,
  username: 'viewer1',
  role: 'viewer',
  display_name: null,
  email: null,
  active: true,
  created_at: '2026-07-02T00:00:00Z',
  updated_at: '2026-07-02T00:00:00Z',
}

describe('UsersView.vue', () => {
  let pinia: ReturnType<typeof createPinia>

  beforeEach(() => {
    localStorage.clear()
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mountView() {
    return mount(UsersView, {
      global: {
        plugins: [pinia],
      },
    })
  }

  it('挂载时调用 listUsers 加载用户列表', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [adminUser, viewerUser], count: 2 })
    const wrapper = mountView()
    await flushPromises()
    expect(authApi.listUsers).toHaveBeenCalledTimes(1)
    expect((wrapper.vm as any).users).toHaveLength(2)
  })

  it('listUsers 返回 403 时设置 forbidden 状态', async () => {
    ;(authApi.listUsers as any).mockRejectedValue({
      response: { status: 403, data: { detail: '需要 admin 权限' } },
    })
    const wrapper = mountView()
    await flushPromises()
    expect((wrapper.vm as any).forbidden).toBe(true)
    expect((wrapper.vm as any).forbiddenMsg).toBe('需要 admin 权限')
  })

  it('listUsers 其他错误时调用 message.error 且不 forbidden', async () => {
    ;(authApi.listUsers as any).mockRejectedValue({
      response: { status: 500, data: { detail: '服务器错误' } },
    })
    const wrapper = mountView()
    await flushPromises()
    expect((wrapper.vm as any).forbidden).toBe(false)
    expect(mockMessage.error).toHaveBeenCalled()
  })

  it('openCreate 重置表单为创建模式并打开弹窗', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openCreate()
    expect(vm.editorMode).toBe('create')
    expect(vm.editorVisible).toBe(true)
    expect(vm.editorForm.username).toBe('')
    expect(vm.editorForm.role).toBe('viewer')
    expect(vm.editorForm.active).toBe(true)
  })

  it('openEdit 填充表单为编辑模式，密码留空', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [adminUser], count: 1 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openEdit(adminUser)
    expect(vm.editorMode).toBe('edit')
    expect(vm.editorVisible).toBe(true)
    expect(vm.editorForm.id).toBe(1)
    expect(vm.editorForm.username).toBe('admin')
    expect(vm.editorForm.password).toBe('') // 留空表示不修改
    expect(vm.editorForm.role).toBe('admin')
  })

  it('handleSave 创建模式调用 createUser 并关闭弹窗', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [], count: 0 })
    ;(authApi.createUser as any).mockResolvedValue({ created: true, user: viewerUser })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openCreate()
    vm.editorForm.username = 'newuser'
    vm.editorForm.password = 'pass123'
    vm.editorForm.role = 'viewer'
    await vm.handleSave()
    expect(authApi.createUser).toHaveBeenCalledWith({
      username: 'newuser',
      password: 'pass123',
      role: 'viewer',
      display_name: undefined,
      email: undefined,
    })
    expect(mockMessage.success).toHaveBeenCalled()
    expect(vm.editorVisible).toBe(false)
  })

  it('handleSave 创建模式用户名为空时 warning 不调用 API', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openCreate()
    vm.editorForm.username = ''
    vm.editorForm.password = 'pass123'
    await vm.handleSave()
    expect(authApi.createUser).not.toHaveBeenCalled()
    expect(mockMessage.warning).toHaveBeenCalled()
  })

  it('handleSave 创建模式密码为空时 warning 不调用 API', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openCreate()
    vm.editorForm.username = 'newuser'
    vm.editorForm.password = ''
    await vm.handleSave()
    expect(authApi.createUser).not.toHaveBeenCalled()
    expect(mockMessage.warning).toHaveBeenCalled()
  })

  it('handleSave 编辑模式调用 updateUser（不传密码）', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [adminUser], count: 1 })
    ;(authApi.updateUser as any).mockResolvedValue({ updated: true, user: adminUser })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openEdit(adminUser)
    vm.editorForm.role = 'operator'
    vm.editorForm.password = '' // 不修改密码
    await vm.handleSave()
    expect(authApi.updateUser).toHaveBeenCalledWith(
      1,
      expect.objectContaining({
        role: 'operator',
        active: true,
      }),
    )
    const callArgs = (authApi.updateUser as any).mock.calls[0][1]
    expect(callArgs.password).toBeUndefined()
  })

  it('handleSave 编辑模式填写密码时传 password', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [adminUser], count: 1 })
    ;(authApi.updateUser as any).mockResolvedValue({ updated: true, user: adminUser })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openEdit(adminUser)
    vm.editorForm.password = 'newpass'
    await vm.handleSave()
    expect(authApi.updateUser).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ password: 'newpass' }),
    )
  })

  it('handleDelete 调用 deleteUser 并刷新列表', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [adminUser, viewerUser], count: 2 })
    ;(authApi.deleteUser as any).mockResolvedValue({ deleted: true, user_id: 2 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    ;(authApi.listUsers as any).mockClear()
    await vm.handleDelete(viewerUser)
    expect(authApi.deleteUser).toHaveBeenCalledWith(2)
    expect(mockMessage.success).toHaveBeenCalled()
    expect(authApi.listUsers).toHaveBeenCalled() // 刷新
  })

  it('handleDelete 失败时调用 message.error', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [adminUser], count: 1 })
    ;(authApi.deleteUser as any).mockRejectedValue({
      response: { status: 404, data: { detail: '用户不存在' } },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    await vm.handleDelete(adminUser)
    expect(mockMessage.error).toHaveBeenCalled()
  })

  it('handleCleanup 调用 dialog.warning 弹出确认框', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [], count: 0 })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.handleCleanup()
    expect(mockDialog.warning).toHaveBeenCalledWith(
      expect.objectContaining({ title: '清理过期 session' }),
    )
  })

  it('handleSave 创建失败时调用 message.error 且保持弹窗打开', async () => {
    ;(authApi.listUsers as any).mockResolvedValue({ users: [], count: 0 })
    ;(authApi.createUser as any).mockRejectedValue({
      response: { status: 400, data: { detail: '用户名已存在' } },
    })
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as any
    vm.openCreate()
    vm.editorForm.username = 'duplicate'
    vm.editorForm.password = 'pass123'
    await vm.handleSave()
    expect(mockMessage.error).toHaveBeenCalled()
    expect(vm.editorVisible).toBe(true) // 失败时保持弹窗
  })
})
