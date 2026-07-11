<script setup lang="ts">
/**
 * 用户管理（admin）
 *
 * 功能：
 * - 列出所有用户（admin 才能访问，后端强制）
 * - 创建 / 编辑 / 删除用户
 * - 重置密码（编辑模式下填写新密码）
 * - 清理过期 session
 *
 * 权限：
 * - 后端 /auth/users 系列端点 require_role('admin')
 * - 前端在非 admin 访问时显示 forbidden 提示（API 403 触发）
 * - dev 模式（legacy 共享 token 视为 admin）可正常使用
 */
import { h, ref, computed, onMounted } from 'vue'
import {
  NCard,
  NTag,
  NSpace,
  NButton,
  NSpin,
  NEmpty,
  NDataTable,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NSelect,
  NSwitch,
  NPopconfirm,
  NAlert,
  useMessage,
  useDialog,
  type DataTableColumns,
} from 'naive-ui'
import * as authApi from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'
import type { User } from '@/api/auth'

const message = useMessage()
const dialog = useDialog()
const authStore = useAuthStore()

const loading = ref(false)
const users = ref<User[]>([])
const forbidden = ref(false)
const forbiddenMsg = ref('')

// 编辑/创建弹窗
const editorVisible = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const editorSaving = ref(false)
const editorForm = ref({
  id: 0,
  username: '',
  password: '',
  role: 'viewer' as 'admin' | 'operator' | 'viewer',
  display_name: '',
  email: '',
  active: true,
})

const roleOptions = [
  { label: 'Admin', value: 'admin' },
  { label: 'Operator', value: 'operator' },
  { label: 'Viewer', value: 'viewer' },
]

const editorTitle = computed(() =>
  editorMode.value === 'create' ? '创建用户' : `编辑用户 #${editorForm.value.id}`,
)

const roleTagType = (role: string): 'error' | 'warning' | 'default' => {
  if (role === 'admin') return 'error'
  if (role === 'operator') return 'warning'
  return 'default'
}

const columns = computed<DataTableColumns<User>>(() => [
  { title: 'ID', key: 'id', width: 60 },
  { title: '用户名', key: 'username', width: 140 },
  {
    title: '角色',
    key: 'role',
    width: 110,
    render: (row) => h(NTag, { type: roleTagType(row.role), size: 'small' }, { default: () => row.role }),
  },
  {
    title: '显示名',
    key: 'display_name',
    render: (row) => row.display_name || '-',
  },
  {
    title: '邮箱',
    key: 'email',
    render: (row) => row.email || '-',
  },
  {
    title: '状态',
    key: 'active',
    width: 80,
    render: (row) =>
      h(
        NTag,
        { type: row.active ? 'success' : 'error', size: 'small' },
        { default: () => (row.active ? '启用' : '禁用') },
      ),
  },
  {
    title: '创建时间',
    key: 'created_at',
    width: 180,
    render: (row) => (row.created_at ? formatDateTime(row.created_at) : '-'),
  },
  {
    title: '操作',
    key: 'actions',
    width: 180,
    fixed: 'right',
    render: (row) => {
      const isSelf = authStore.user?.id === row.id
      return h(NSpace, { size: 'small' }, {
        default: () => [
          h(
            NButton,
            { size: 'small', onClick: () => openEdit(row) },
            { default: () => '编辑' },
          ),
          h(
            NPopconfirm,
            {
              onPositiveClick: () => handleDelete(row),
              positiveText: '删除',
              negativeText: '取消',
            },
            {
              default: () =>
                isSelf
                  ? '不能删除当前登录用户'
                  : `确认删除用户 "${row.username}"？此操作不可恢复。`,
              trigger: () =>
                h(
                  NButton,
                  { size: 'small', type: 'error', disabled: isSelf },
                  { default: () => '删除' },
                ),
            },
          ),
        ],
      })
    },
  },
])

async function loadUsers() {
  loading.value = true
  forbidden.value = false
  try {
    const resp = await authApi.listUsers()
    users.value = resp.users
  } catch (err: any) {
    if (err.response?.status === 403) {
      forbidden.value = true
      forbiddenMsg.value = err.response?.data?.detail || '需要 admin 权限访问此页面'
    } else {
      message.error(err.response?.data?.detail || '加载用户失败')
    }
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editorMode.value = 'create'
  editorForm.value = {
    id: 0,
    username: '',
    password: '',
    role: 'viewer',
    display_name: '',
    email: '',
    active: true,
  }
  editorVisible.value = true
}

function openEdit(user: User) {
  editorMode.value = 'edit'
  editorForm.value = {
    id: user.id,
    username: user.username,
    password: '', // 留空表示不修改
    role: user.role,
    display_name: user.display_name || '',
    email: user.email || '',
    active: user.active,
  }
  editorVisible.value = true
}

async function handleSave() {
  // 校验
  if (editorMode.value === 'create') {
    if (!editorForm.value.username.trim()) {
      message.warning('请填写用户名')
      return
    }
    if (!editorForm.value.password) {
      message.warning('请填写密码')
      return
    }
  } else {
    // 编辑：密码留空表示不修改，但若填写需符合长度
    if (editorForm.value.password && editorForm.value.password.length < 1) {
      message.warning('密码不能为空（留空则不修改）')
      return
    }
  }

  editorSaving.value = true
  try {
    if (editorMode.value === 'create') {
      const resp = await authApi.createUser({
        username: editorForm.value.username.trim(),
        password: editorForm.value.password,
        role: editorForm.value.role,
        display_name: editorForm.value.display_name.trim() || undefined,
        email: editorForm.value.email.trim() || undefined,
      })
      message.success(`用户 ${resp.user.username} 创建成功`)
    } else {
      const payload: {
        role: string
        display_name?: string
        email?: string
        active: boolean
        password?: string
      } = {
        role: editorForm.value.role,
        display_name: editorForm.value.display_name.trim() || undefined,
        email: editorForm.value.email.trim() || undefined,
        active: editorForm.value.active,
      }
      if (editorForm.value.password) {
        payload.password = editorForm.value.password
      }
      await authApi.updateUser(editorForm.value.id, payload)
      message.success('用户更新成功')
      // 若修改了自己，刷新本地 user 状态
      if (authStore.user?.id === editorForm.value.id) {
        await authStore.fetchMe()
      }
    }
    editorVisible.value = false
    await loadUsers()
  } catch (err: any) {
    message.error(err.response?.data?.detail || '保存失败')
  } finally {
    editorSaving.value = false
  }
}

async function handleDelete(user: User) {
  try {
    await authApi.deleteUser(user.id)
    message.success(`用户 ${user.username} 已删除`)
    await loadUsers()
  } catch (err: any) {
    message.error(err.response?.data?.detail || '删除失败')
  }
}

function handleCleanup() {
  dialog.warning({
    title: '清理过期 session',
    content: '将删除所有已过期的用户 session，在线用户不受影响。继续？',
    positiveText: '清理',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const resp = await authApi.cleanupSessions()
        message.success(`已清理 ${resp.cleaned} 个过期 session`)
      } catch (err: any) {
        message.error(err.response?.data?.detail || '清理失败')
      }
    },
  })
}

onMounted(() => {
  loadUsers()
})

// 暴露内部方法用于单元测试
defineExpose({
  loadUsers,
  openCreate,
  openEdit,
  handleSave,
  handleDelete,
  handleCleanup,
  // state refs（只读访问）
  users,
  loading,
  forbidden,
  forbiddenMsg,
  editorVisible,
  editorMode,
  editorForm,
  editorSaving,
})
</script>

<template>
  <div class="users-view">
    <NCard title="用户管理" :bordered="false">
      <template #header-extra>
        <NSpace>
          <NButton @click="handleCleanup" :disabled="forbidden">清理过期 session</NButton>
          <NButton type="primary" @click="openCreate" :disabled="forbidden">创建用户</NButton>
          <NButton @click="loadUsers" :loading="loading">刷新</NButton>
        </NSpace>
      </template>

      <NAlert v-if="forbidden" type="error" :title="forbiddenMsg" style="margin-bottom: 12px">
        当前账号无权访问用户管理。请联系管理员提升权限，或切换 admin 账号登录。
      </NAlert>

      <NSpin :show="loading">
        <NDataTable
          v-if="!forbidden"
          :columns="columns"
          :data="users"
          :bordered="false"
          :pagination="{ pageSize: 20 }"
          :scroll-x="900"
          striped
        />
        <NEmpty v-else description="无权查看用户列表" style="padding: 40px 0" />
      </NSpin>
    </NCard>

    <!-- 创建/编辑弹窗 -->
    <NModal
      v-model:show="editorVisible"
      preset="card"
      :title="editorTitle"
      style="width: 520px; max-width: 90vw"
      :mask-closable="false"
    >
      <NForm label-placement="left" label-width="90px">
        <NFormItem label="用户名" v-if="editorMode === 'create'">
          <NInput
            v-model:value="editorForm.username"
            placeholder="登录用户名"
            maxlength="64"
          />
        </NFormItem>
        <NFormItem label="用户名" v-else>
          <NInput :value="editorForm.username" disabled />
        </NFormItem>
        <NFormItem :label="editorMode === 'create' ? '密码' : '新密码'">
          <NInput
            v-model:value="editorForm.password"
            type="password"
            show-password-on="click"
            :placeholder="editorMode === 'create' ? '登录密码' : '留空表示不修改密码'"
            maxlength="128"
          />
        </NFormItem>
        <NFormItem label="角色">
          <NSelect v-model:value="editorForm.role" :options="roleOptions" />
        </NFormItem>
        <NFormItem label="显示名">
          <NInput v-model:value="editorForm.display_name" placeholder="可选" maxlength="128" />
        </NFormItem>
        <NFormItem label="邮箱">
          <NInput v-model:value="editorForm.email" placeholder="可选" maxlength="128" />
        </NFormItem>
        <NFormItem label="启用" v-if="editorMode === 'edit'">
          <NSwitch v-model:value="editorForm.active" />
        </NFormItem>
      </NForm>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="editorVisible = false">取消</NButton>
          <NButton type="primary" :loading="editorSaving" @click="handleSave">
            {{ editorMode === 'create' ? '创建' : '保存' }}
          </NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.users-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
