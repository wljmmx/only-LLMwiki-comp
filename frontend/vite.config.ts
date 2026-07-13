import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

export default defineConfig({
  plugins: [vue()],
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
