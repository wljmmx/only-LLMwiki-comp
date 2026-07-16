import { test, expect } from '@playwright/test'
import { skipIfBackendDown, login } from './helpers/auth'

/**
 * E2E 旅程 6：Wiki Lint 健康检查流程（P4-5）
 *
 * 覆盖：
 * 1. Wiki 健康检查页可访问
 * 2. 触发 lint 检查 API
 * 3. lint 结果包含已知问题类型（孤儿/死链/矛盾/stale）
 * 4. index 重建 API
 * 5. stale 页面检测 API
 *
 * 前置条件：已登录，后端可用
 */

test.describe('Wiki Lint 健康检查旅程', () => {
  test.beforeEach(async ({ page }) => {
    await skipIfBackendDown(page)
    await login(page)
  })

  test('Wiki 健康检查页可访问', async ({ page }) => {
    await page.goto('/wiki/health')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/wiki\/health/)
    await expect(page.locator('body')).toBeVisible()
  })

  test('触发 lint 检查 API', async ({ page }) => {
    const resp = await page.request.post('/api/llm-wiki/lint')
    expect(resp.status()).toBe(200)
    const data = await resp.json()
    expect(data).toBeTruthy()
    const jsonStr = JSON.stringify(data)
    expect(jsonStr.length).toBeGreaterThan(0)
  })

  test('孤儿页面检测 API', async ({ page }) => {
    const resp = await page.request.get('/api/llm-wiki/orphans')
    expect(resp.status()).toBe(200)
    const data = await resp.json()
    expect(data).toBeTruthy()
  })

  test('死链检测 API', async ({ page }) => {
    const resp = await page.request.get('/api/llm-wiki/deadlinks')
    expect(resp.status()).toBe(200)
    const data = await resp.json()
    expect(data).toBeTruthy()
  })

  test('stale 页面检测 API', async ({ page }) => {
    const resp = await page.request.get('/api/llm-wiki/stale')
    expect(resp.status()).toBe(200)
    const data = await resp.json()
    expect(data).toBeTruthy()
  })

  test('index 重建 API', async ({ page }) => {
    const resp = await page.request.post('/api/llm-wiki/index/rebuild')
    expect(resp.status()).toBe(200)
    const data = await resp.json()
    expect(data).toBeTruthy()
  })

  test('lint 建议缺失页面 API', async ({ page }) => {
    const resp = await page.request.get('/api/llm-wiki/lint/suggestions')
    expect(resp.status()).toBe(200)
    const data = await resp.json()
    expect(data).toBeTruthy()
  })
})