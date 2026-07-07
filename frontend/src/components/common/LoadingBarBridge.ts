/**
 * LoadingBar 桥接组件（S14-2 全局 loading bar）
 *
 * 作用：
 * - 在 NLoadingBarProvider 内部调用 useLoadingBar() 获取实例
 * - 将实例存入模块级单例，供 axios 拦截器（非组件上下文）调用
 *
 * 用法：
 *   <NLoadingBarProvider>
 *     <LoadingBarBridge />
 *     <router-view />
 *   </NLoadingBarProvider>
 *
 * 然后 api/loadingBar.ts 可通过 getLoadingBar() 获取实例。
 */
import { onMounted, onBeforeUnmount } from 'vue'
import { useLoadingBar } from 'naive-ui'
import { setLoadingBar, clearLoadingBar } from '@/api/loadingBar'

export default {
  name: 'LoadingBarBridge',
  setup() {
    const loadingBar = useLoadingBar()

    onMounted(() => {
      setLoadingBar(loadingBar)
    })

    onBeforeUnmount(() => {
      clearLoadingBar()
    })

    // 不渲染任何内容，仅做桥接
    return () => null
  },
}
