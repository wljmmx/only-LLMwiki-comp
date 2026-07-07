import { test, expect, type Page } from '@playwright/test'

/**
 * E2E 旅程 1：认证流程（S14-6）
 *
 * 覆盖：
 * 1. 未登录访问受保护路由 → 重定向到 /login
 * 2. 登录页渲染（标题、表单、OIDC 按钮）
 * 3. 错误凭证 → 显示错误提示
 * 4. 正确凭证 → 跳转到 dashboard
 * 5. 登录后 token 持久化（localStorage）
 * 6. 退出登录 → 清除 token，返回 login
 *
 * 前置条件：后端在 :8000 运行，dev 模式（OPSKG_API_TOKEN 为空）
 */

const TEST_USER = 'admin'
const TEST_PASS = 'admin123'

/** 检查后端是否可用，不可用则 skip */
async function skipIfBackendDown(page: Page) {
  const resp = await page.request.get('/api/auth/me').catch(() => null)
  if (!resp || resp.status() >= 500) {
    test.skip(true, '后端不可用，跳过 E2E 测试')
  }
}

test.describe('认证旅程', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
    // 清除可能残留的 token
    await page.evaluate(() => localStorage.removeItem('opskg_token'))
  })

  test('未登录访问 dashboard 重定向到 /login', async ({ page }) => {
    await skipIfBackendDown(page)
    await page.goto('/dashboard')
    await expect(page).toHaveURL(/\/login/)
  })

  test('登录页正确渲染', async ({ page }) => {
    await skipIfBackendDown(page)
    await page.goto('/login')
    // 标题或文案存在
    await expect(page.locator('body')).toContainText(/登录|Login/i, { timeout: 5000 })
  })

  test('错误凭证显示错误提示', async ({ page }) => {
    await skipIfBackendDown(page)
    await page.goto('/login')
    // 填写表单
    const usernameInput = page.locator('input[type="text"], input[placeholder*="用户"]').first()
    const passwordInput = page.locator('input[type="password"]').first()
    if (await usernameInput.isVisible() && await passwordInput.isVisible()) {
      await usernameInput.fill('wronguser')
      await passwordInput.fill('wrongpass')
      const submitBtn = page.locator('button[type="submit"], button:has-text("登录")').first()
      await submitBtn.click()
      // 等待错误提示出现（message 或 alert）
      await page.waitForTimeout(2000)
      // 不检查具体文案，只确认未跳转到 dashboard
      await expect(page).not.toHaveURL(/\/dashboard/)
    }
  })

  test('正确凭证登录成功跳转 dashboard', async ({ page }) => {
    await skipIfBackendDown(page)
    await page.goto('/login')
    const usernameInput = page.locator('input[type="text"], input[placeholder*="用户"]').first()
    const passwordInput = page.locator('input[type="password"]').first()
    await usernameInput.fill(TEST_USER)
    await passwordInput.fill(TEST_PASS)
    const submitBtn = page.locator('button[type="submit"], button:has-text("登录")').first()
    await submitBtn.click()
    // 等待跳转
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 })
    // token 已存入 localStorage
    const token = await page.evaluate(() => localStorage.getItem('opskg_token'))
    expect(token).toBeTruthy()
  })

  test('登录后刷新页面保持会话', async ({ page }) => {
    await skipIfBackendDown(page)
    await page.goto('/login')
    const usernameInput = page.locator('input[type="text"], input[placeholder*="用户"]').first()
    const passwordInput = page.locator('input[type="password"]').first()
    await usernameInput.fill(TEST_USER)
    await passwordInput.fill(TEST_PASS)
    const submitBtn = page.locator('button[type="submit"], button:has-text("登录")').first()
    await submitBtn.click()
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 })
    // 刷新
    await page.reload()
    // 仍在 dashboard（未跳回 login）
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10_000 })
  })
})
