import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright E2E 测试配置（S14-6）
 *
 * 自动启动后端（FastAPI :8000）与前端（Vite :5173），
 * 测试基础 URL 为前端 dev server。
 *
 * 运行：
 *   npx playwright test          # 全量 E2E
 *   npx playwright test --grep auth  # 仅 auth 旅程
 *
 * 首次运行需安装浏览器：npx playwright install chromium
 *
 * 注意：E2E 测试需要完整后端栈（LLM、Neo4j 等）。
 * 若后端不可用，测试会自动 skip（通过 beforeAll 检查 /api/auth/me）。
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // E2E 串行避免端口冲突
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1, // 单 worker 避免后端并发问题
  reporter: process.env.CI ? 'line' : 'html',
  timeout: 30_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: [
    {
      command: 'cd /workspace/backend && uvicorn app.main:app --host 0.0.0.0 --port 8000',
      port: 8000,
      timeout: 30_000,
      reuseExistingServer: true,
      env: {
        ENV: 'dev',
        LOG_LEVEL: 'WARNING',
        OPSKG_API_TOKEN: '', // dev 模式无认证
      },
    },
    {
      command: 'npm run dev',
      port: 5173,
      timeout: 30_000,
      reuseExistingServer: true,
    },
  ],
})
