import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'
import router from './router'
import { permission } from '@/directives/permission'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

// S15-4: 注册全局 v-permission 指令（按钮级权限控制）
app.directive('permission', permission)

// 全局错误处理器 — 捕获未被 onErrorCaptured 拦截的错误
app.config.errorHandler = (err, _instance, info) => {
  console.error('[全局错误]', err, '\n触发位置:', info)
}

// 捕获未处理的 Promise rejection
window.addEventListener('unhandledrejection', (event) => {
  console.error('[未处理的 Promise rejection]', event.reason)
})

app.mount('#app')
