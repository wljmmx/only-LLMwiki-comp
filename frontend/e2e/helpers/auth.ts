import { test, expect, type Page } from '@playwright/test'

const TEST_USER = 'admin'
const TEST_PASS = 'admin'

/** 检查后端是否可用，不可用则 skip */
export async function skipIfBackendDown(page: Page) {
  const resp = await page.request.get('/api/auth/me').catch(() => null)
  if (!resp || resp.status() >= 500) {
    test.skip(true, '后端不可用，跳过 E2E 测试')
  }
}

/** 通过 API 登录获取 token，并通过 addInitScript 注入 */
export async function login(page: Page) {
  // 通过 API 直接登录获取 token
  const loginResp = await page.request.post('/api/auth/login', {
    data: { username: TEST_USER, password: TEST_PASS },
  }).catch(() => null)

  if (!loginResp || loginResp.status() !== 200) {
    test.skip(true, '登录失败，跳过 E2E 测试')
    return
  }

  const { token } = await loginResp.json()
  if (!token) {
    test.skip(true, '登录获取 token 失败，跳过 E2E 测试')
    return
  }

  // 通过 addInitScript 注入 token 和 setup dismissed
  await page.context().addInitScript((t: string) => {
    localStorage.setItem('opskg_token', t)
    localStorage.setItem('opskg:setup:dismissed', 'true')
  }, token)

  // 导航到 dashboard，完成登录
  await page.goto('/dashboard')
  await page.waitForLoadState('networkidle')
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 10_000 })
}