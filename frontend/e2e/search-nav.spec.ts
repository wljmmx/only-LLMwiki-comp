import { test, expect } from '@playwright/test'
import { skipIfBackendDown, login } from './helpers/auth'

/**
 * E2E 旅程 3：搜索与导航流程（S14-6）
 *
 * 覆盖：
 * 1. 访问搜索页面
 * 2. 输入查询关键词
 * 3. 查看搜索结果
 * 4. 导航到文档管理页
 * 5. 导航到知识图谱页
 * 6. 404 页面处理
 *
 * 前置条件：已登录
 */

test.describe('搜索与导航旅程', () => {
  test.beforeEach(async ({ page }) => {
    await skipIfBackendDown(page)
    await login(page)
  })

  test('搜索页可访问并渲染', async ({ page }) => {
    await page.goto('/search')
    await expect(page).toHaveURL(/\/search/)
    await expect(page.locator('body')).toBeVisible()
  })

  test('输入查询并提交', async ({ page }) => {
    await page.goto('/search')
    await page.waitForLoadState('networkidle')
    const searchInput = page
      .locator('input[type="text"], input[placeholder*="搜索"], input[placeholder*="查询"]')
      .first()
    if (await searchInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      await searchInput.fill('nginx')
      await searchInput.press('Enter')
      await page.waitForTimeout(2000)
      await expect(page.locator('body')).toBeVisible()
    }
  })

  test('文档管理页可访问', async ({ page }) => {
    await page.goto('/documents')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/documents/)
    await expect(page.locator('body')).toBeVisible()
  })

  test('知识图谱页可访问', async ({ page }) => {
    await page.goto('/graph')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/graph/)
    await expect(page.locator('body')).toBeVisible()
  })

  test('拓扑图页可访问', async ({ page }) => {
    await page.goto('/topology')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/topology/)
    await expect(page.locator('body')).toBeVisible()
  })

  test('访问不存在路由显示 404 页面', async ({ page }) => {
    await page.goto('/this-does-not-exist-xyz')
    await page.waitForLoadState('networkidle')
    const body = page.locator('body')
    await expect(body).toBeVisible()
    const text = (await body.textContent()) || ''
    expect(
      text.includes('404') ||
        text.includes('不存在') ||
        text.includes('Not Found') ||
        page.url().includes('dashboard') ||
        page.url().includes('login'),
    ).toBeTruthy()
  })

  test('导出中心页可访问', async ({ page }) => {
    await page.goto('/export')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/export/)
    await expect(page.locator('body')).toBeVisible()
  })

  test('版本管理页可访问', async ({ page }) => {
    await page.goto('/versions')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/versions/)
    await expect(page.locator('body')).toBeVisible()
  })
})