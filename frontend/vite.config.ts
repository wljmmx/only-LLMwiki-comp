import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

// P2: Naive UI tree-shaking — 需要安装 unplugin-vue-components 后启用
// import { NaiveUiResolver } from 'unplugin-vue-components/resolvers'
// import Components from 'unplugin-vue-components/vite'

export default defineConfig({
  plugins: [
    vue(),
    // P2: 待 unplugin-vue-components 安装后启用
    // Components({ resolvers: [NaiveUiResolver()] }),
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // P1-5: API 版本化 — /api/v1 优先，同时保留 /api 向后兼容
      '/api/v1': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/v1/, ''),
        ws: true,
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
        // S15-5：支持 WebSocket 协作端点（/api/realtime/collab/{slug}）
        ws: true,
      },
    },
  },
})
