<script setup lang="ts">
/**
 * 全局 Toast 通知组件
 *
 * 统一管理成功/错误/警告/信息提示，提供一致性体验。
 * 基于 Naive UI useMessage，封装为全局可调用的 composable。
 *
 * 用法：
 *   const toast = useToast()
 *   toast.success('操作成功')
 *   toast.error('操作失败')
 *   toast.warning('请注意')
 *   toast.info('提示信息')
 */

import { useMessage } from 'naive-ui'
import type { MessageOptions } from 'naive-ui'

export interface ToastInstance {
  success: (content: string, options?: MessageOptions) => void
  error: (content: string, options?: MessageOptions) => void
  warning: (content: string, options?: MessageOptions) => void
  info: (content: string, options?: MessageOptions) => void
  loading: (content: string, options?: MessageOptions) => void
}

export function useToast(): ToastInstance {
  const message = useMessage()

  const defaults: MessageOptions = {
    duration: 3000,
    closable: true,
    keepAliveOnHover: true,
  }

  return {
    success(content, options) {
      message.success(content, { ...defaults, ...options })
    },
    error(content, options) {
      message.error(content, { ...defaults, ...options, duration: 5000 })
    },
    warning(content, options) {
      message.warning(content, { ...defaults, ...options })
    },
    info(content, options) {
      message.info(content, { ...defaults, ...options })
    },
    loading(content, options) {
      message.loading(content, { ...defaults, ...options, duration: 0 })
    },
  }
}
</script>

<template>
  <!-- 此组件为 composable 提供者，挂载在 App.vue 中提供全局 message context -->
  <div style="display: none" />
</template>