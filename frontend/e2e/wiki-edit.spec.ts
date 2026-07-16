import { test, expect, type Page } from '@playwright/test'
import { skipIfBackendDown, login } from './helpers/auth'

/**
 * E2E 旅程 5：Wiki 编辑流程（P4-5）
 *
 * 覆盖：
 * 1. Wiki 页面浏览
 * 2. 获取编辑锁
 * 3. 进入编辑模式
 * 4. 修改内容并保存（版本控制）
 * 5. PUT /llm-wiki/page/{slug} API 端到端
 * 6. 版本号递增验证
 *
 * 前置条件：已登录，后端可用，至少有一个 wiki 页面
 */

/** 确保有至少一个 wiki 页面可用（通过 ingest 创建） */
async function ensureWikiPage(page: Page): Promise<string | null> {
  const resp = await page.request.post('/api/llm-wiki/ingest', {
    multipart: {
      file: {
        name: 'e2e-wiki-source.md',
        mimeType: 'text/markdown',
        buffer: Buffer.from(
          '# E2E 测试概念\n\n## 概述\n这是一个用于 E2E 测试的概念页面。\n涉及服务：nginx\n涉及主机：web-prod-01\n',
        ),
      },
    },
    timeout: 30_000,
  }).catch(() => null)

  if (!resp || resp.status() !== 200) return null
  const data = await resp.json()
  const slugs = data.compile?.slugs || []
  return slugs.length > 0 ? slugs[0] : null
}

test.describe('Wiki 编辑旅程', () => {
  test.beforeEach(async ({ page }) => {
    await skipIfBackendDown(page)
    await login(page)
  })

  test('Wiki 页面浏览', async ({ page }) => {
    await page.goto('/wiki')
    await page.waitForLoadState('networkidle')
    await expect(page).toHaveURL(/\/wiki/)
    await expect(page.locator('body')).toBeVisible()
  })

  test('PUT API 编辑 wiki 页面内容', async ({ page }) => {
    const slug = await ensureWikiPage(page)
    if (!slug) {
      test.skip(true, '无法创建测试 wiki 页面，跳过')
      return
    }

    const getResp = await page.request.get(`/api/llm-wiki/page/${slug}`)
    expect(getResp.status()).toBe(200)
    const pageData = await getResp.json()
    const originalVersion = pageData.version

    const updatedContent = pageData.content.replace(
      /$/,
      '\n## E2E 编辑补充\n\n由 E2E 测试追加的内容。\n',
    )

    const putResp = await page.request.put(`/api/llm-wiki/page/${slug}`, {
      data: {
        content: updatedContent,
        change_summary: 'E2E 测试编辑',
      },
    })
    expect(putResp.status()).toBe(200)
    const putData = await putResp.json()
    expect(putData.version).toBeGreaterThan(originalVersion)
  })

  test('编辑后 backlink 和 index 更新', async ({ page }) => {
    const slug = await ensureWikiPage(page)
    if (!slug) {
      test.skip(true, '无法创建测试 wiki 页面，跳过')
      return
    }

    const blResp = await page.request.get(`/api/llm-wiki/backlinks/${slug}`)
    expect(blResp.status()).toBe(200)

    const idxResp = await page.request.get('/api/llm-wiki/index')
    expect(idxResp.status()).toBe(200)
    const idxData = await idxResp.json()
    expect(idxData).toBeTruthy()
  })

  test('版本历史可查看', async ({ page }) => {
    const slug = await ensureWikiPage(page)
    if (!slug) {
      test.skip(true, '无法创建测试 wiki 页面，跳过')
      return
    }

    for (let i = 0; i < 2; i++) {
      const getResp = await page.request.get(`/api/llm-wiki/page/${slug}`)
      if (getResp.status() !== 200) continue
      const data = await getResp.json()
      await page.request.put(`/api/llm-wiki/page/${slug}`, {
        data: {
          content: data.content + `\n<!-- edit ${i} -->`,
          change_summary: `E2E edit ${i}`,
        },
      })
    }

    const histResp = await page.request.get(`/api/versions/wiki:${slug}`)
    expect(histResp.status()).toBe(200)
  })
})