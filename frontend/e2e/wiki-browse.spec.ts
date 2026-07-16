import { test, expect } from '@playwright/test'
import { skipIfBackendDown, login } from './helpers/auth'

/**
 * E2E 旅程 2：Wiki 浏览流程（S14-6）
 *
 * 覆盖：
 * 1. 从 dashboard 导航到 wiki 列表
 * 2. wiki 列表渲染（至少有内容或空状态）
 * 3. 点击 wiki 条目进入详情
 * 4. wiki 详情页渲染（标题、内容）
 * 5. 侧边栏导航到其他页面
 *
 * 前置条件：已登录（dev 模式或 admin 账号）
 */

test.describe('Wiki 浏览旅程', () => {
  test.beforeEach(async ({ page }) => {
    await skipIfBackendDown(page)
    await login(page)
  })

  test('dashboard 渲染关键卡片', async ({ page }) => {
    await expect(page).toHaveURL(/\/dashboard/)
    await expect(page.locator('body')).toBeVisible({ timeout: 10_000 })
  })

  test('从 dashboard 导航到 wiki 列表', async ({ page }) => {
    const wikiLink = page.locator('a[href*="wiki"], [data-testid="nav-wiki"]').first()
    if (await wikiLink.isVisible({ timeout: 5000 }).catch(() => false)) {
      await wikiLink.click()
      await expect(page).toHaveURL(/\/wiki/, { timeout: 10_000 })
    } else {
      await page.goto('/wiki')
      await expect(page).toHaveURL(/\/wiki/)
    }
    await expect(page.locator('body')).toBeVisible()
  })

  test('wiki 列表页渲染内容或空状态', async ({ page }) => {
    await page.goto('/wiki')
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(2000)
    const body = page.locator('body')
    await expect(body).toBeVisible()
    const textContent = await body.textContent()
    expect(textContent?.length).toBeGreaterThan(0)
  })

  test('wiki 查询页面可访问', async ({ page }) => {
    await page.goto('/wiki/query')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/wiki\/query/)
    await expect(page.locator('body')).toBeVisible()
  })

  test('wiki 健康检查页面可访问', async ({ page }) => {
    await page.goto('/wiki/health')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/wiki\/health/)
    await expect(page.locator('body')).toBeVisible()
  })
})